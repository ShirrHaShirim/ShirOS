# Module Boundaries — 0.7.18

依赖方向：协议适配器 → 绑定身份的服务 → 权限/隐私/审核 → 专用仓储。连接、审批权威与原件路径不直接开放给 Agent。

| 责任 | 模块 | 边界 |
| --- | --- | --- |
| 类型与政策 | `core/` | 来源、权限、隐私、事件和请求契约 |
| 数据库 | `adapters/database/`、`migrations/` | 参数化 SQL、锁、事件时钟、迁移 |
| 记忆写入 | `memory_service.py`、`review_workflow.py`、`candidate_submission.py` | 候选、审核、幂等、修订 |
| 记忆维护 | `memory_mutations.py`、`memory_metadata.py`、`tags.py` | 乐观并发、来源、标签、软删除 |
| 检索 | `retrieval.py`、`context_compiler.py`、`visibility.py` | scope、可见性、预算与来源 |
| 身份 | `identity.py`、`memory_bridge.py`、`memory_gateway.py` | Profile、角色、独立 capability |
| 知识层 | `knowledge.py` | canonical L2、L1/L0、租约、依赖、发布 |
| 音乐 | `domains/music*.py` | 四类对象、关系、评价、图片、瞬时范围 |
| 原件 | `file_library.py`、`conversation_import.py` | 授权文件、审计、官方导入 |
| 采集 | `capture*.py` | 会话授权、增量、撤销与适配 |
| 工作台 | `apps/workbench.py`、`apps/web/` | 本机业务接口与瞬时界面状态 |
| 健康 API | `apps/api.py` | 独立 live/ready 端点 |
| MCP | `adapters/mcp/server.py` | 协议与传输，不授予审核权 |
| 桌面 | `desktop.py`、`desktop_settings.py`、`desktop/` | UI、生命周期、安装与运行时 |
| 备份导出 | `backup.py`、`markdown_export.py` | 授权、校验、独立目标与可见性 |

Music 与 Memory 分别持有权威事实；音乐修改不自动生成记忆，L1 引用不接管领域编辑权。测试分为单元、独立 `_test` 数据库集成和浏览器回归；浏览器脚本的样本与会话须显式准备。
