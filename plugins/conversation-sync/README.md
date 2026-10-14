# ShirOS Conversation Sync · 0.7.4

包含 Codex 的单会话授权 hooks 和 OpenCode 原生插件。由现有 ShirOS 运行端提供鉴权、隐私检查、去重、审核和附件索引，无需另建数据库。

先在 ShirOS「设置 → 插件与对话同步」为准确的会话编号授权。配对信息仅保存到个人 `~/.config/ShirOS/capture.json` 或 `SHIROS_CAPTURE_CONFIG` 指定的路径。多个配对使用 `{"pairings":[...]}`。包内不包含任何授权密钥或个人对话。

Codex 安装后仍须信任 hooks 定义；不允许自动绕过客户端信任确认。OpenCode 使用 `opencode.js`，复制到项目或个人插件目录。未配对会话不会读取；插件没有批准待审记忆的能力。

阅读随包提供的 `USAGE.md`，或源项目 `docs/conversation-sync-v073.md`。Windows ChatGPT 的窗口接入仅为条件适配，正式客户端尚未接通验证。
