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

EDITOR_SCROLL_STATE = '''pub type ScrollOffset = f64;
pub type ScrollPixelOffset = f64;

/// Pending distance for interpolating discrete mouse-wheel scrolls.
///
/// Only the editor opts into smoothing; every other scroll surface keeps the
/// platform's native per-notch behaviour.
#[derive(Default)]
pub(crate) struct SmoothScrollState {
    /// Remaining distance to scroll, in rows/columns.
    pending: gpui::Point<f32>,
    /// When the previous step was applied. The eased fraction is derived from
    /// elapsed time rather than a frame counter, so a slow frame compresses
    /// the animation instead of building up a backlog.
    last_step: Option<Instant>,
    /// Whether a next-frame callback is already queued.
    scheduled: bool,
}

/// The maximum distance, in rows, that can accumulate while smoothing.
const MAX_PENDING_SMOOTH_SCROLL: f32 = 120.;

/// Remaining distances below this, in rows, are flushed immediately.
const SMOOTH_SCROLL_EPSILON: f32 = 0.01;

'''

EDITOR_SCROLL_METHODS = '''    /// Adds a discrete wheel scroll to the pending smooth-scroll distance.
    ///
    /// Returns `true` when a next-frame callback needs to be scheduled.
    pub(crate) fn push_smooth_scroll(&mut self, delta: gpui::Point<f32>) -> bool {
        let smooth = &mut self.smooth_scroll;
        // Reversing direction should respond immediately instead of first
        // cancelling out the distance still pending from the previous notch.
        if smooth.pending.x * delta.x < 0. {
            smooth.pending.x = 0.;
        }
        if smooth.pending.y * delta.y < 0. {
            smooth.pending.y = 0.;
        }
        smooth.pending.x = (smooth.pending.x + delta.x)
            .clamp(-MAX_PENDING_SMOOTH_SCROLL, MAX_PENDING_SMOOTH_SCROLL);
        smooth.pending.y = (smooth.pending.y + delta.y)
            .clamp(-MAX_PENDING_SMOOTH_SCROLL, MAX_PENDING_SMOOTH_SCROLL);
        if smooth.last_step.is_none() {
            smooth.last_step = Some(Instant::now());
        }
        if smooth.scheduled {
            false
        } else {
            smooth.scheduled = true;
            true
        }
    }

    /// Releases one eased step of the pending distance, then re-arms the next
    /// frame while distance remains.
    pub(crate) fn advance_smooth_scroll(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        self.smooth_scroll.scheduled = false;

        let Some(duration) = cx.smooth_scroll() else {
            self.smooth_scroll = SmoothScrollState::default();
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

        let step = point(
            self.smooth_scroll.pending.x * progress,
            self.smooth_scroll.pending.y * progress,
        );
        self.smooth_scroll.pending.x -= step.x;
        self.smooth_scroll.pending.y -= step.y;
        let done = self.smooth_scroll.pending.x.abs() < SMOOTH_SCROLL_EPSILON
            && self.smooth_scroll.pending.y.abs() < SMOOTH_SCROLL_EPSILON;
        if done {
            self.smooth_scroll.pending = point(0., 0.);
        }

        if step.x != 0. || step.y != 0. {
            let current = self.scroll_position(cx);
            let target = point(
                current.x + f64::from(step.x),
                current.y + f64::from(step.y),
            );
            self.scroll(target, window, cx);
        }

        if done {
            self.smooth_scroll.last_step = None;
        } else {
            self.smooth_scroll.scheduled = true;
            let editor = cx.entity();
            window.on_next_frame(move |window, cx| {
                editor.update(cx, |editor, cx| editor.advance_smooth_scroll(window, cx));
            });
        }
    }
'''

EDITOR_SCROLL_LISTENER = '''                    delta = delta.coalesce(event.delta);

                    // Interpolate discrete wheel notches. Only the editor
                    // reaches this listener, so the terminal, panels, and
                    // lists keep the platform's native scroll behaviour.
                    if let ScrollDelta::Lines(lines) = event.delta
                        && !event.modifiers.secondary(cx)
                        && lines.x.abs().max(lines.y.abs()) >= 1.
                        && cx.smooth_scroll().is_some_and(|duration| !duration.is_zero())
                    {
                        let scroll_sensitivity = if event.modifiers.alt {
                            fast_scroll_sensitivity
                        } else {
                            base_scroll_sensitivity
                        };
                        let step =
                            point(lines.x * scroll_sensitivity, lines.y * scroll_sensitivity);
                        let schedule = editor.update(cx, |editor, _| {
                            editor.push_smooth_scroll(step)
                        });
                        delta = ScrollDelta::default();
                        if schedule {
                            let editor = editor.clone();
                            window.on_next_frame(move |window, cx| {
                                editor.update(cx, |editor, cx| {
                                    editor.advance_smooth_scroll(window, cx)
                                });
                            });
                        }
                        cx.stop_propagation();
                        return;
                    }
'''

APPLY_SCROLL_DELTA = '''    pub fn apply_scroll_delta(
        &mut self,
        scroll_delta: gpui::Point<f32>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let mut delta = scroll_delta;
        if self.scroll_manager.forbid_vertical_scroll {
            delta.y = 0.0;
        }
        let display_map = self.display_map.update(cx, |map, cx| map.snapshot(cx));
        let position = self.scroll_manager.scroll_position(&display_map, cx) + delta.map(f64::from);
        self.set_scroll_position_taking_display_map(position, true, false, display_map, window, cx);
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
                description: "把编辑器的滚轮离散滚动分摊到多帧，减少跳格感。仅对编辑器生效，触控板与 Ctrl+滚轮缩放不受影响。",
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
        """    pub focus_follows_mouse: Option<FocusFollowsMouse>,
""",
        """    /// Whether discrete mouse-wheel scrolling is interpolated across frames.
    ///
    /// Only the editor is affected.
    ///
    /// Default: enabled, 120ms
    pub smooth_scroll: Option<SmoothScrollSettings>,
    pub focus_follows_mouse: Option<FocusFollowsMouse>,
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
    /// Only the editor is affected.
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
  // Only the editor is affected; touchpads and Ctrl+wheel zooming are untouched.
  "smooth_scroll": {
    "enabled": true,
    "duration_ms": 120,
  },
""",
    ),
    (
        "crates/editor/src/editor.rs",
        """    pub scroll_manager: ScrollManager,
""",
        """    pub scroll_manager: ScrollManager,
    /// Pending distance for interpolating discrete mouse-wheel scrolls.
    pub(crate) smooth_scroll: crate::scroll::SmoothScrollState,
""",
    ),
    (
        "crates/editor/src/editor.rs",
        """            scroll_manager: ScrollManager::new(cx),
""",
        """            scroll_manager: ScrollManager::new(cx),
            smooth_scroll: crate::scroll::SmoothScrollState::default(),
""",
    ),
    (
        "crates/editor/src/scroll.rs",
        """use std::{cmp::Ordering, time::Duration};
""",
        """use std::{
    cmp::Ordering,
    time::{Duration, Instant},
};
""",
    ),
    (
        "crates/editor/src/scroll.rs",
        """pub type ScrollOffset = f64;
pub type ScrollPixelOffset = f64;
""",
        EDITOR_SCROLL_STATE,
    ),
    (
        "crates/editor/src/scroll.rs",
        APPLY_SCROLL_DELTA,
        APPLY_SCROLL_DELTA + "\n" + EDITOR_SCROLL_METHODS,
    ),
    (
        "crates/editor/src/element/mouse.rs",
        """                    delta = delta.coalesce(event.delta);
""",
        EDITOR_SCROLL_LISTENER,
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


THEME_LICENSE_HEADING = "## [VSCode Dark Modern](https://github.com/kevcamel/vscode_dark_modern.zed)"

THEME_LICENSE_ENTRY = """********************************************************************************

## [VSCode Dark Modern](https://github.com/kevcamel/vscode_dark_modern.zed)

The MIT License (MIT)

Copyright (c) 2024 Kevin Camellini

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def append_theme_license(zed_root: Path) -> None:
    """Attribute the bundled VSCode Dark Modern theme in the licenses list.

    `script/generate-licenses.ps1` copies `assets/themes/LICENSES` verbatim into
    `assets/licenses.md`, which feeds the in-app Open Source License
    Attribution page, so the theme's MIT notice has to land in this file.
    """
    path = zed_root / "assets" / "themes" / "LICENSES"
    text = path.read_text(encoding="utf-8")
    if THEME_LICENSE_HEADING in text:
        print(f"theme license already present in {path}")
        return

    separator = "\n" if text.endswith("\n") else "\n\n"
    path.write_text(text + separator + THEME_LICENSE_ENTRY, encoding="utf-8")
    print(f"appended theme license to {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed-root", required=True, type=Path)
    args = parser.parse_args()

    zed_root = args.zed_root.resolve()
    if not (zed_root / "crates" / "gpui" / "src" / "style.rs").is_file():
        raise SystemExit(f"error: not a Zed checkout: {zed_root}")

    install_theme(zed_root)
    append_theme_license(zed_root)

    style_path = zed_root / "crates" / "gpui" / "src" / "style.rs"
    replace_exact(style_path, STYLE_OLD, STYLE_NEW)

    for relative, expected in SHADOW_PATCHES.items():
        add_shadow_none(zed_root / relative, expected)

    for relative, old, new in SMOOTH_SCROLL_PATCHES:
        replace_exact(zed_root / relative, old, new)

    touched = [
        zed_root / "assets" / "themes" / "vscode-dark-modern.json",
        zed_root / "assets" / "themes" / "LICENSES",
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
