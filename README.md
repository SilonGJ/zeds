# zeds

基于 [Zed](https://github.com/zed-industries/zed) 的 Windows 定制构建：

- 简体中文本地化（来自 [LI-NA/zed-i18n](https://github.com/LI-NA/zed-i18n)）
- 全局直角界面（保留头像、开关等真正的圆形）
- 内置 [VSCode Dark Modern](https://github.com/kevcamel/vscode_dark_modern.zed) 主题（`border.variant` 改为 `#2B2B2B`，不设为默认主题）
- 移除菜单/弹出层阴影
- 编辑器滚轮平滑滚动（可在设置中开关、调整时长）
- 启用 Zed 内置自动更新，更新源指向本仓库

本仓库不 fork、不修改、不向上游提交任何内容。所有补丁只在 GitHub Actions 的临时工作目录中应用，构建完成后即丢弃。

## 触发方式

zed-i18n 通常比 Zed 官方晚一个版本发布，因此构建以 **zed-i18n 的 release** 为准：

1. 每 6 小时检查一次 `LI-NA/zed-i18n` 的最新 release
2. 读取该 tag 固定的 `zed_version` / `zed_commit`
3. 生成发布标签 `zeds-<i18n_tag>`，例如 `zeds-v1.27.0-i18n.1`
4. 若该 release 已存在则跳过，否则构建

也可以在 Actions 页面手动运行 `Build customized Zed (Windows)`，并可指定：

- `i18n_tag`：要使用的 zed-i18n tag（留空表示最新）
- `revision`：自动更新版本号（留空则从 tag 推导）

手动重建时若 revision 与 tag 内数字不同，发布标签会追加 `-r<revision>`，例如 `zeds-v1.27.0-i18n.1-r2`。手动重建后，定时任务对同一 i18n tag 也会跳过，避免把旧 revision 重新发布成最新版。

## 产物

每次发布包含：

| 文件 | 说明 |
| --- | --- |
| `Zed-i18n-zh-CN-windows-x86_64.exe` | Windows 安装包 |
| `Zed-i18n-zh-CN-windows-x86_64.zip` | 便携版 |
| `manifest.json` | 自动更新清单 |
| `SHA256SUMS.txt` | 校验和 |

## 安装与更新

1. 首次需要手动下载并运行安装包。
2. 安装包沿用 zed-i18n 的 Windows 应用身份，可原地升级已有的 zed-i18n 安装并保留设置。
3. 之后 Zed 会通过本仓库的 `releases/latest/download/manifest.json` 检查更新。

自动更新版本格式为 `<zed_version>+i18n.<revision>`，只有 revision 增加时才会提示更新。

## 平滑滚动

只对**编辑器**里鼠标滚轮的离散滚动做跨帧插值；终端、面板、列表保持系统原生滚动。触控板的精确滚动和 Ctrl+滚轮缩放不受影响。

默认开启、时长 120ms。可在「设置 → 窗口与布局 → 滚动」里开关和调整，也可以直接改 `settings.json`，保存后立即生效，无需重启：

```json
"smooth_scroll": {
  "enabled": true,
  "duration_ms": 120
}
```

`reduce_motion` 为 `"on"` 时会自动停用平滑滚动。

## 仓库结构

```text
.github/workflows/build-windows.yml   # Windows 构建与发布
overlay/apply.py                      # 直角 UI、菜单阴影、主题、平滑滚动补丁
overlay/resolve.py                    # 解析 zed-i18n 目标版本
overlay/package.py                    # 收集产物并生成 manifest
overlay/distribution.toml             # Windows 应用身份与自动更新配置
overlay/vscode-dark-modern.json       # 内置主题
```

`overlay/apply.py` 对每一处替换都要求精确匹配一次。上游代码结构变化时会直接报错，不会静默产出错误的安装包。

## 注意事项

- 安装包未签名，Windows SmartScreen 可能提示；可用 `SHA256SUMS.txt` 校验。
- 主题只是内置，不会自动切换。已有设置不会被覆盖，需要在主题列表中手动选择一次 `VSCode Dark Modern`。
- 自动更新只对已经安装本仓库构建的 Zed 生效，无法从官方 Zed 直接跳转。
- 公开仓库的定时任务在 60 天无活动后会被 GitHub 暂停；可随时手动触发，或提交一次改动恢复。
