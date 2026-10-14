# Shared Memory Clients — 0.7.18

MCP 客户端通过同一 MemoryGateway 与领域服务访问 ShirOS。Profile 名称是本机独立身份配置，不证明某个外部产品已经连接。

## 初始化与授权

先准备数据库并迁移，再创建只读客户端：

```powershell
uv run python scripts/mcp_shiros_client.py init --client codex
uv run python scripts/mcp_shiros_client.py status --client codex
```

支持的标识：`codex`、`chatgpt`、`work`、`deepseek`、`opencode`、`deepseek-harness`、`antigravity`、`gemini-desktop`。私密 Profile 位于 `.local/memory-clients/`，包含服务端绑定的 actor、scope 和 token，不提交、不共享。

本机所有者按需求分别授权：

```powershell
uv run python scripts/mcp_shiros_client.py access --client codex --mode proposer
uv run python scripts/mcp_shiros_client.py access --client codex --mode editor
uv run python scripts/mcp_shiros_client.py music-access --client codex --music enabled
```

Reader 只读；proposer 可以提交受服务端政策检查的候选；editor 可以按明确请求修订或撤销记忆。角色不授予审核、管理员、原始 SQL 或任意文件访问。音乐写入是独立 capability。

降回 `--mode reader`、使用 `--music disabled` 或 `revoke --client codex` 可以收回对应权限。收回后已有进程的后续操作仍会重新检查授权。

## 本地 stdio

在客户端中注册以下命令；路径改为实际克隆位置：

```json
{
  "mcpServers": {
    "shiros": {
      "command": "<repo>/.venv/Scripts/python.exe",
      "args": ["<repo>/scripts/mcp_shiros_client.py", "serve", "--client", "codex"]
    }
  }
}
```

Unix 使用 `.venv/bin/python`。需要其他客户端时替换标识，先创建其独立 Profile。OpenCode 使用其自己的 `mcp` 配置形状，不能原样复制通用 `mcpServers` JSON：

```json
{
  "mcp": {
    "shiros": {
      "type": "local",
      "command": ["<repo>/.venv/Scripts/python.exe", "<repo>/scripts/mcp_shiros_client.py", "serve", "--client", "opencode"],
      "enabled": true
    }
  }
}
```

以上为模板，不包含维护者的本机配置。支持本地 MCP 的客户端负责启动进程；仅生成 JSON 不等于实际连接。

## HTTP 与外部连接

```powershell
uv run python scripts/mcp_shiros_client.py serve --client chatgpt --transport http --port 8002
```

HTTP 保持 loopback 和 bearer 认证。外部产品若要求远程 URL，应另行部署符合其认证要求的受控通道；不能直接公开裸端口或把 stdio JSON 填作服务器 URL。既有隧道是本机可选配置，不在仓库附带凭据。

## 验收

分开检查：Profile 创建 → initialize → tools/list → status → 实际 search/fetch → 客户端内真实调用。工具共 22 个；具体能力受角色和 capability 限制。检查 proposal 的 `applied/pending/blocked`，不把待审候选报为正式记忆。

Gemini 桌面支持情况见 [接入边界](gemini-desktop-mcp.md)。完整工具与传输契约见 [MCP README](../src/shiros/adapters/mcp/README.md)。
