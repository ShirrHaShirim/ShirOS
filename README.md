# ShirOS v0.5.0

个人知识与记忆基础设施，附带原件文件库和可选音乐库。

## Windows 桌面版

从本仓库 Releases 下载 `full`（带曲库）或 `core`（不开放音乐功能）安装包。支持 Windows 10/11 x64，需要 Microsoft WebView2。安装包包含 Python、PostgreSQL 与 pgvector；首次运行创建独立空白数据库，不含开发者或使用者的私人记忆、文件、密钥或账号配置。

[使用说明书](docs/user-manual.md) · [音乐库](docs/music-library.md) · [第三方组件](docs/desktop-third-party.md)

## 功能

- 已审核记忆、检索、树状标签、无标签与来源记录。
- 单条及全库 Markdown 导出，遵守隐藏和撤销边界。
- 任意类型文件原件导入、搜索、下载、删除，单文件 50 MB。
- ChatGPT JSON/ZIP 导入：原件保留，全部消息节点及分支转为 Markdown。
- GPT 网页持续快照试验扩展：默认关闭，仅记录已加载消息，不能保证完整历史。
- full 版：音乐档案、豆瓣导入、评论评分、独立 Genre、封面头像。
- 五种颜色主题。

## 源码开发

Python 3.12+，`uv sync --all-groups --locked`。复制 `.env.example` 为 `.env` 并配置自己的 PostgreSQL + pgvector，执行 `uv run shiros migrate`，再 `uv run shiros web --port 8001` 和 `uv run shiros open`。

测试：配置独立 `_test` 数据库的 `SHIROS_TEST_DATABASE_URL`，运行 `uv run pytest`。静态检查：`uv run ruff check .`、`uv run mypy`。

桌面构建参见说明书。安装版不会继承任何开发环境的数据库或 MCP 授权。更新与卸载保留已产生的用户资料。发行包未作商业代码签名。
