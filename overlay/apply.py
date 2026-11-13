#!/usr/bin/env python3
"""Apply the custom UI patches to a Zed checkout.

This script is deliberately small and strict: every replacement must match
exactly once. If upstream refactors the target code, the build fails loudly
instead of producing a silently broken binary.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

STYLE_OLD = """        let rem_size = window.rem_size();
        let corner_radii = self
            .corner_radii
            .to_pixels(rem_size)
            .clamp_radii_for_quad_size(bounds.size);
"""

STYLE_NEW = """        let rem_size = window.rem_size();
        let raw_radii = self.corner_radii.to_pixels(rem_size);
        let corner_radii = if raw_radii.top_left >= px(100.) {
            raw_radii.clamp_radii_for_quad_size(bounds.size)
        } else {
            Corners::all(px(0.))
        };
"""

SHADOW_PATCHES = {
    "crates/ui/src/components/context_menu.rs": 2,
    "crates/ui/src/components/popover.rs": 2,
}

SMOOTH_SCROLL_STATE = '''#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Ord, PartialOrd)]
enum InputModality {
    Mouse,
    Keyboard,
    Touch,
}

/// Interpolates discrete mouse-wheel scrolling over animation frames.
///
/// A wheel notch arrives as a single [`crate::ScrollDelta::Lines`] event, which
/// would otherwise move the scroll position by several lines at once. The
/// distance is accumulated here and released in small steps, one per frame, so
/// every scroll surface (editor, terminal, panels, lists) moves smoothly
/// without each of them needing its own animation.
#[derive(Default)]
struct SmoothScrollState {
    /// Remaining distance to scroll, in lines, per axis.
    pending_x: f32,
    pending_y: f32,
    /// When the previous step was dispatched. The eased fraction is derived
    /// from elapsed time rather than a frame counter, so a slow frame
    /// compresses the animation instead of building up a backlog.
    last_step: Option<Instant>,
    /// The original wheel event, reused as the template for interpolated
    /// events. Only its delta is replaced.
    template: Option<crate::ScrollEvent>,
    /// Whether a next-frame callback is already queued.
    scheduled: bool,
    /// Set while dispatching an interpolated event so it is not captured again.
    dispatching: bool,
}

/// The maximum distance, in lines, that can accumulate while smoothing.
///
/// A free-spinning wheel can emit notches faster than the animation drains
/// them; capping the backlog keeps the motion responsive instead of letting it
/// trail far behind the wheel.
const MAX_PENDING_SCROLL_LINES: f32 = 120.;

/// Remaining distances below this, in lines, are flushed immediately.
const SMOOTH_SCROLL_EPSILON: f32 = 0.01;
'''

SMOOTH_SCROLL_METHODS = '''    /// Schedule the given closure to be run directly after the current frame is rendered.
    pub fn on_next_frame(&self, callback: impl FnOnce(&mut Window, &mut App) + 'static) {
        RefCell::borrow_mut(&self.next_frame_callbacks).push(Box::new(callback));
        self.platform_window.schedule_frame();
        // Next-frame callbacks create frame demand without dirtying the
        // window, so the platform's frame source must be woken explicitly.
        self.invalidator.wake_platform();
    }

    /// Captures a discrete mouse-wheel scroll so it can be interpolated.
    ///
    /// Returns `true` when the event was captured, in which case the caller
    /// must not dispatch it. Precise (pixel) deltas from trackpads and
    /// touchscreens, modifier-assisted zooming, and events that are already
    /// interpolated pass through untouched.
    fn try_smooth_scroll(&mut self, event: crate::ScrollEvent, cx: &mut App) -> bool {
        if self.smooth_scroll.dispatching {
            return false;
        }

        let Some(duration) = cx.smooth_scroll() else {
            self.smooth_scroll.pending_x = 0.;
            self.smooth_scroll.pending_y = 0.;
            return false;
        };

        let crate::ScrollDelta::Lines(delta) = event.delta else {
            return false;
        };
        if !self.active.get()
            || event.modifiers.secondary(cx)
            || event.touch_phase != crate::TouchPhase::Moved
        {
            return false;
        }

        // Windows delivers precision-touchpad scrolling as line deltas too, but
        // in much finer steps than a wheel notch. Only interpolate whole lines
        // so trackpads keep their native, already-smooth behaviour.
        let dominant = if delta.x.abs() >= delta.y.abs() {
            delta.x
        } else {
            delta.y
        };
        if dominant.abs() < 1. || duration.is_zero() {
            return false;
        }

        // Reversing direction should respond immediately instead of first
        // cancelling out the distance that is still pending from the previous
        // notch.
        if self.smooth_scroll.pending_x * delta.x < 0. {
            self.smooth_scroll.pending_x = 0.;
        }
        if self.smooth_scroll.pending_y * delta.y < 0. {
            self.smooth_scroll.pending_y = 0.;
        }

        self.smooth_scroll.pending_x = (self.smooth_scroll.pending_x + delta.x)
            .clamp(-MAX_PENDING_SCROLL_LINES, MAX_PENDING_SCROLL_LINES);
        self.smooth_scroll.pending_y = (self.smooth_scroll.pending_y + delta.y)
            .clamp(-MAX_PENDING_SCROLL_LINES, MAX_PENDING_SCROLL_LINES);
        self.smooth_scroll.template = Some(event);
        if self.smooth_scroll.last_step.is_none() {
            self.smooth_scroll.last_step = Some(Instant::now());
        }
        self.schedule_smooth_scroll();
        true
    }

    fn schedule_smooth_scroll(&mut self) {
        if self.smooth_scroll.scheduled {
            return;
        }
        self.smooth_scroll.scheduled = true;
        self.on_next_frame(|window, cx| window.advance_smooth_scroll(cx));
    }

    /// Releases one eased step of the pending scroll distance, then re-arms the
    /// next frame while distance remains.
    fn advance_smooth_scroll(&mut self, cx: &mut App) {
        self.smooth_scroll.scheduled = false;

        let Some(duration) = cx.smooth_scroll() else {
            self.smooth_scroll.pending_x = 0.;
            self.smooth_scroll.pending_y = 0.;
            self.smooth_scroll.last_step = None;
            return;
        };

        let now = Instant::now();
        let elapsed = self
            .smooth_scroll
            .last_step
            .map(|last_step| now.duration_since(last_step))
            .unwrap_or_default();
        self.smooth_scroll.last_step = Some(now);

        let progress = if duration.is_zero() {
            1.
        } else {
            let t = (elapsed.as_secs_f32() / duration.as_secs_f32()).clamp(0., 1.);
            1. - (1. - t).powi(3)
        };

        let step_x = self.smooth_scroll.pending_x * progress;
        let step_y = self.smooth_scroll.pending_y * progress;
        self.smooth_scroll.pending_x -= step_x;
        self.smooth_scroll.pending_y -= step_y;

        let done = self.smooth_scroll.pending_x.abs() < SMOOTH_SCROLL_EPSILON
            && self.smooth_scroll.pending_y.abs() < SMOOTH_SCROLL_EPSILON;
        if done {
            self.smooth_scroll.pending_x = 0.;
            self.smooth_scroll.pending_y = 0.;
            self.smooth_scroll.last_step = None;
        }

        if step_x != 0. || step_y != 0. {
            if let Some(template) = self.smooth_scroll.template.clone() {
                self.smooth_scroll.dispatching = true;
                self.dispatch_event(
                    crate::PlatformInput::Scroll(crate::ScrollEvent {
                        delta: crate::ScrollDelta::Lines(Point {
                            x: step_x,
                            y: step_y,
                        }),
                        ..template
                    }),
                    cx,
                );
                self.smooth_scroll.dispatching = false;
            }
        }

        if !done {
            self.schedule_smooth_scroll();
        }
    }
'''

SMOOTH_SCROLL_SETTING = '''fn init_reduce_motion(cx: &mut App) {
    let apply = |cx: &mut App| {
        let reduce_motion = ReduceMotionSetting::get_global(cx).0 == settings::ReduceMotionMode::On;
        cx.set_reduce_motion(reduce_motion);
    };
    apply(cx);
    cx.observe_global::<SettingsStore>(apply).detach();
}

#[derive(Copy, Clone, Debug, settings::RegisterSetting)]
struct SmoothScrollSetting {
    enabled: bool,
    duration: std::time::Duration,
}

impl Settings for SmoothScrollSetting {
    fn from_settings(content: &settings::SettingsContent) -> Self {
        let smooth_scroll = content.workspace.smooth_scroll.as_ref();
        Self {
            enabled: smooth_scroll
                .and_then(|settings| settings.enabled)
                .unwrap_or(true),
            duration: std::time::Duration::from_millis(
                smooth_scroll
                    .and_then(|settings| settings.duration_ms)
                    .unwrap_or(120)
                    .min(2_000),
            ),
        }
    }
}

fn init_smooth_scroll(cx: &mut App) {
    let apply = |cx: &mut App| {
        let setting = *SmoothScrollSetting::get_global(cx);
        let duration = if setting.enabled && !cx.reduce_motion() {
            Some(setting.duration)
        } else {
            None
        };
        cx.set_smooth_scroll(duration);
    };
    apply(cx);
    cx.observe_global::<SettingsStore>(apply).detach();
}
'''

SMOOTH_SCROLL_SECTION = '''    fn scrolling_section() -> [SettingsPageItem; 3] {
        [
            SettingsPageItem::SectionHeader("滚动"),
            SettingsPageItem::SettingItem(SettingItem {
                title: "平滑滚动",
                description: "把鼠标滚轮的离散滚动分摊到多帧，减少跳格感。触控板与 Ctrl+滚轮缩放不受影响。",
                field: Box::new(SettingField {
                    organization_override: None,
                    json_path: Some("smooth_scroll.enabled"),
                    pick: |settings_content| {
                        settings_content
                            .workspace
                            .smooth_scroll
                            .as_ref()?
                            .enabled
                            .as_ref()
                    },
                    write: |settings_content, value, _| {
                        settings_content
                            .workspace
                            .smooth_scroll
                            .get_or_insert_default()
                            .enabled = value;
                    },
                }),
                metadata: None,
                files: USER,
            }),
            SettingsPageItem::SettingItem(SettingItem {
                title: "平滑滚动时长",
                description: "每次滚轮滚动完成插值所需的时间（毫秒）。数值越大越柔和，建议 80–160。",
                field: Box::new(SettingField {
                    organization_override: None,
                    json_path: Some("smooth_scroll.duration_ms"),
                    pick: |settings_content| {
                        settings_content
                            .workspace
                            .smooth_scroll
                            .as_ref()?
                            .duration_ms
                            .as_ref()
                    },
                    write: |settings_content, value, _| {
                        settings_content
                            .workspace
                            .smooth_scroll
                            .get_or_insert_default()
                            .duration_ms = value;
                    },
                }),
                metadata: None,
                files: USER,
            }),
        ]
    }

    fn layout_section() -> [SettingsPageItem; 6] {
'''

SMOOTH_SCROLL_PATCHES = [
    (
        "crates/gpui/src/window.rs",
        """#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Ord, PartialOrd)]
enum InputModality {
    Mouse,
    Keyboard,
    Touch,
}
""",
        SMOOTH_SCROLL_STATE,
    ),
    (
        "crates/gpui/src/window.rs",
        """    last_input_modality: InputModality,
    touch_gestures: TouchGestureRecognizer,
""",
        """    last_input_modality: InputModality,
    smooth_scroll: SmoothScrollState,
    touch_gestures: TouchGestureRecognizer,
""",
    ),
    (
        "crates/gpui/src/window.rs",
        """            last_input_modality: InputModality::Mouse,
            touch_gestures: TouchGestureRecognizer::new(
""",
        """            last_input_modality: InputModality::Mouse,
            smooth_scroll: SmoothScrollState::default(),
            touch_gestures: TouchGestureRecognizer::new(
""",
    ),
    (
        "crates/gpui/src/window.rs",
        """    /// Dispatch a mouse, keyboard, or touch event on the window.
    #[profiling::function]
    pub fn dispatch_event(&mut self, event: PlatformInput, cx: &mut App) -> DispatchEventResult {
        #[cfg(feature = "profiler")]
        self.window_profiler.begin_input(event.kind_name());
""",
        """    /// Dispatch a mouse, keyboard, or touch event on the window.
    #[profiling::function]
    pub fn dispatch_event(&mut self, event: PlatformInput, cx: &mut App) -> DispatchEventResult {
        // Capture discrete mouse-wheel scrolls and release them over the next
        // few frames. Every scroll surface receives its events through here,
        // so this covers the editor, terminal, panels, and lists in one place.
        if let crate::PlatformInput::Scroll(scroll) = &event
            && self.try_smooth_scroll(scroll.clone(), cx)
        {
            return DispatchEventResult {
                propagate: false,
                default_prevented: false,
            };
        }

        #[cfg(feature = "profiler")]
        self.window_profiler.begin_input(event.kind_name());
""",
    ),
    (
        "crates/gpui/src/window.rs",
        """    /// Schedule the given closure to be run directly after the current frame is rendered.
    pub fn on_next_frame(&self, callback: impl FnOnce(&mut Window, &mut App) + 'static) {
        RefCell::borrow_mut(&self.next_frame_callbacks).push(Box::new(callback));
        self.platform_window.schedule_frame();
        // Next-frame callbacks create frame demand without dirtying the
        // window, so the platform's frame source must be woken explicitly.
        self.invalidator.wake_platform();
    }
""",
        SMOOTH_SCROLL_METHODS,
    ),
    (
        "crates/gpui/src/app.rs",
        """    pub(crate) reduce_motion: bool,
    /// Origin of the shared clock that phase-locks synced repeating animations.
""",
        """    pub(crate) reduce_motion: bool,
    /// How long discrete wheel scrolls are interpolated over, if enabled.
    pub(crate) smooth_scroll: Option<Duration>,
    /// Origin of the shared clock that phase-locks synced repeating animations.
""",
    ),
    (
        "crates/gpui/src/app.rs",
        """                reduce_motion: false,
                synced_animation_epoch,
""",
        """                reduce_motion: false,
                smooth_scroll: None,
                synced_animation_epoch,
""",
    ),
    (
        "crates/gpui/src/app.rs",
        """    pub fn set_reduce_motion(&mut self, reduce_motion: bool) {
        if self.reduce_motion != reduce_motion {
            self.reduce_motion = reduce_motion;
            self.refresh_windows();
        }
    }
""",
        """    pub fn set_reduce_motion(&mut self, reduce_motion: bool) {
        if self.reduce_motion != reduce_motion {
            self.reduce_motion = reduce_motion;
            self.refresh_windows();
        }
    }

    /// Returns how long discrete wheel scrolls are interpolated over, or `None`
    /// when smooth scrolling is disabled.
    pub fn smooth_scroll(&self) -> Option<Duration> {
        self.smooth_scroll
    }

    /// Sets how long discrete wheel scrolls are interpolated over.
    ///
    /// `None` disables smooth scrolling and makes wheel scrolls immediate again.
    pub fn set_smooth_scroll(&mut self, duration: Option<Duration>) {
        if self.smooth_scroll != duration {
            self.smooth_scroll = duration;
            self.refresh_windows();
        }
    }
""",
    ),
    (
        "crates/zed/src/zed.rs",
        """    init_reduce_motion(cx);
    init_global_config_error_notifications(cx);
""",
        """    init_reduce_motion(cx);
    init_smooth_scroll(cx);
    init_global_config_error_notifications(cx);
""",
    ),
    (
        "crates/zed/src/zed.rs",
        """fn init_reduce_motion(cx: &mut App) {
    let apply = |cx: &mut App| {
        let reduce_motion = ReduceMotionSetting::get_global(cx).0 == settings::ReduceMotionMode::On;
        cx.set_reduce_motion(reduce_motion);
    };
    apply(cx);
    cx.observe_global::<SettingsStore>(apply).detach();
}
""",
        SMOOTH_SCROLL_SETTING,
    ),
    (
        "crates/settings_content/src/workspace.rs",
        """    /// Whether the focused panel follows the mouse location
    /// Default: false
    pub focus_follows_mouse: Option<FocusFollowsMouse>,
}
""",
        """    /// Whether the focused panel follows the mouse location
    /// Default: false
    pub focus_follows_mouse: Option<FocusFollowsMouse>,
    /// Whether discrete mouse-wheel scrolling is interpolated across frames.
    ///
    /// Default: enabled, 120ms
    pub smooth_scroll: Option<SmoothScrollSettings>,
}
""",
    ),
    (
        "crates/settings_content/src/workspace.rs",
        """#[with_fallible_options]
#[derive(Copy, Clone, PartialEq, Default, Serialize, Deserialize, JsonSchema, MergeFrom, Debug)]
pub struct FocusFollowsMouse {
    pub enabled: Option<bool>,
    pub debounce_ms: Option<u64>,
}
""",
        """#[with_fallible_options]
#[derive(Copy, Clone, PartialEq, Default, Serialize, Deserialize, JsonSchema, MergeFrom, Debug)]
pub struct FocusFollowsMouse {
    pub enabled: Option<bool>,
    pub debounce_ms: Option<u64>,
}

/// Settings controlling interpolation of discrete mouse-wheel scrolling.
#[with_fallible_options]
#[derive(Copy, Clone, PartialEq, Default, Serialize, Deserialize, JsonSchema, MergeFrom, Debug)]
pub struct SmoothScrollSettings {
    /// Whether wheel scrolling is interpolated across animation frames.
    pub enabled: Option<bool>,
    /// How long, in milliseconds, an interpolated scroll takes to finish.
    pub duration_ms: Option<u64>,
}
""",
    ),
    (
        "crates/settings/src/vscode_import.rs",
        """            zoomed_padding: None,
            focus_follows_mouse: None,
""",
        """            zoomed_padding: None,
            focus_follows_mouse: None,
            smooth_scroll: None,
""",
    ),
    (
        "crates/settings_ui/src/page_data.rs",
        """    fn layout_section() -> [SettingsPageItem; 6] {
""",
        SMOOTH_SCROLL_SECTION,
    ),
    (
        "crates/settings_ui/src/page_data.rs",
        """            layout_section(),
            window_section(),
""",
        """            layout_section(),
            scrolling_section(),
            window_section(),
""",
    ),
    (
        "assets/settings/default.json",
        """  // Determines whether the focused panel follows the mouse location.
  "focus_follows_mouse": {
    "enabled": false,
    "debounce_ms": 250,
  },
""",
        """  // Determines whether the focused panel follows the mouse location.
  "focus_follows_mouse": {
    "enabled": false,
    "debounce_ms": 250,
  },
  // Whether discrete mouse-wheel scrolling is interpolated across frames.
  // Touchpads and Ctrl+wheel zooming are unaffected.
  "smooth_scroll": {
    "enabled": true,
    "duration_ms": 120,
  },
""",
    ),
]


def replace_exact(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"error: expected exactly one match in {path}, found {count}\n"
            "       upstream code changed; update overlay/apply.py"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"patched {path}")


def add_shadow_none(path: Path, expected: int) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    result: list[str] = []
    found = 0
    inserted = 0

    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped != ".elevation_2(cx)":
            result.append(line)
            continue

        found += 1
        indent = line[: len(line) - len(line.lstrip())]
        result.append(line)
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if following.strip() != ".shadow_none()":
            ending = line[len(line.rstrip("\r\n")) :]
            result.append(f"{indent}.shadow_none(){ending}")
            inserted += 1

    if found != expected:
        raise SystemExit(
            f"error: expected {expected} '.elevation_2(cx)' lines in {path}, "
            f"found {found}"
        )
    if inserted == 0:
        print(f"already patched {path}")
        return

    path.write_text("".join(result), encoding="utf-8")
    print(f"patched {path} ({inserted} menus)")


def print_diff(zed_root: Path, files: list[Path]) -> None:
    root = zed_root.resolve()
    relative = []
    for file in files:
        try:
            relative.append(str(file.resolve().relative_to(root)))
        except ValueError:
            continue
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "diff", "--stat", "--", *relative],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return
    if result.stdout.strip():
        print(result.stdout.rstrip())


def install_theme(zed_root: Path) -> None:
    source = HERE / "vscode-dark-modern.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    for theme in data.get("themes", []):
        style = theme.setdefault("style", {})
        if "border.variant" in style:
            style["border.variant"] = "#2B2B2B"

    destination = zed_root / "assets" / "themes" / "vscode-dark-modern.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    installed = json.loads(destination.read_text(encoding="utf-8"))
    borders = [
        theme.get("style", {}).get("border.variant")
        for theme in installed.get("themes", [])
    ]
    if not borders or any(value != "#2B2B2B" for value in borders):
        raise SystemExit("error: border.variant was not applied to every bundled theme")
    print(f"installed {destination}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed-root", required=True, type=Path)
    args = parser.parse_args()

    zed_root = args.zed_root.resolve()
    if not (zed_root / "crates" / "gpui" / "src" / "style.rs").is_file():
        raise SystemExit(f"error: not a Zed checkout: {zed_root}")

    install_theme(zed_root)

    style_path = zed_root / "crates" / "gpui" / "src" / "style.rs"
    replace_exact(style_path, STYLE_OLD, STYLE_NEW)

    for relative, expected in SHADOW_PATCHES.items():
        add_shadow_none(zed_root / relative, expected)

    for relative, old, new in SMOOTH_SCROLL_PATCHES:
        replace_exact(zed_root / relative, old, new)

    touched = [
        zed_root / "assets" / "themes" / "vscode-dark-modern.json",
        zed_root / "assets" / "settings" / "default.json",
        style_path,
        *(zed_root / relative for relative in SHADOW_PATCHES),
        *(zed_root / relative for relative, _, _ in SMOOTH_SCROLL_PATCHES),
    ]
    print_diff(zed_root, touched)
    print("custom UI patches applied successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
