# Gemini 与 ShirOS MCP

截至本次 2026-10-14 核对，Google 官方 Gemini Windows 桌面应用不提供可读取本地 stdio MCP 配置的入口。官方 Custom apps 文档描述远程服务器 URL 接入路径及产品、账号条件，不能将 Gemini CLI 的配置能力套用到桌面应用。

来源：[Google Custom apps](https://support.google.com/gemini/answer/17209137)、[Gemini Windows](https://support.google.com/gemini/answer/18263854)。产品支持可能改变，配置前应重新核对官方文档与当前应用。

## ShirOS 侧

`gemini-desktop` 是独立只读 Profile 标识。它可供支持 stdio MCP 的客户端运行，创建 Profile 或通过协议测试不证明官方 Gemini 桌面实际连接。

```powershell
uv run python scripts/mcp_shiros_client.py init --client gemini-desktop
uv run python scripts/mcp_shiros_client.py serve --client gemini-desktop
```

配置模板见 [客户端说明](shared-memory-clients.md)。不要将配置写入官方应用安装包，或把 token、个人数据库复制到其目录。

## 可用路线

- Antigravity 支持 MCP，可选择其当前提供的 Gemini 模型；使用独立 `antigravity` Profile。客户端配置、重载和连接状态见 [官方 MCP 说明](https://antigravity.google/docs/mcp)。
- Gemini CLI 支持本地 MCP，使用其正式配置格式，见 [官方说明](https://geminicli.com/docs/tools/mcp-server/)。
- Gemini Custom apps 的远程接入需要符合官方账号条件、URL 与认证要求，须单独部署和验收；本仓库不提供一个已经连接的公网服务器。

公开文档不包含任何本机私密 Profile、安装路径或个人查询结果。
