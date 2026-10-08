# 模块与接口落点

core 不依赖 FastAPI、SQLAlchemy 或厂商 SDK。服务编排使用核心 schema、内部仓储和 Provider 端口。同步 SQL 在工作线程内执行，避免阻塞调用方的 asyncio loop。

| 对象/能力 | 模块 | 当前能力 |
| --- | --- | --- |
| Entity | core/records.py EntityRecord、core/entities.py | scope 身份、来源、隐私；PostgresEntityRepository |
| Source / Artifact / Relation | core/records.py | 正式类型与关系表；ingestion 原子写入；Artifact 仅 text/plain |
| Memory | core/memory.py | 稳定 memory_id、不可变版本；PostgresMemoryRepository |
| Event | core/events.py | ID/code/provenance payload，PostgresEventStore |
| Summary / Snapshot | core/records.py、layers.py | per-memory L1/L0、来源范围和版本；确定性 Mock |
| Checkpoint | core/records.py、layers.py | 追加式进度，批次事务内更新 |
| Inference | core/records.py、memory_service.py | 未验证 inference、证据 revision 链、来源和置信度 |
| Context | core/context.py、context_compiler.py | 有权限和预算的 SharedContextCompiler |
| Retrieval | core/retrieval.py、retrieval.py | exact/structured/keyword/semantic/hybrid 与 metadata filters |
| Trusted Review | review.py | actor/request-bound、限时内存批准，独立 review 权限 |
| Privacy | core/privacy.py、memory_service.py | 规则分类、脱敏/泛化和持久化拒绝 |
| Permissions | core/permissions.py | read/persist/execute/review/project，明确 scope 授权 |
| i18n | i18n/ | zh-CN/en-US、资源回退、插件注册与格式化 |
| Composition | shared_memory.py | Memory/Review/Layers/Retrieval/Context 组合 |
| Entry | memory_demo.py、cli.py | 可重复合成数据演示；不自动审阅真实资料 |
| Conversation / Message | core/conversations.py | 与 Memory 分离；Mock provider，正式持久化待实现 |
| Job / Run | core/jobs.py | 独立 Worker Mock；调度延后 |
| Domain | domains/ | Music/Papers/Knowledge/People/Projects 业务延后 |
| Workspace | adapters/projection.py | 仍为内存 Mock，报告是本次人工导出，不是同步器 |
| Local identity | identity.py | OS principal、持久 scope grants、Owner/Reviewer/Reader/Worker |
| Intake / Candidate | intake.py、review_workflow.py | 有界文件预览、明确暂存、候选状态与事务审核 |
| Visibility | visibility.py | 正常检索/派生统一撤销和隐藏规则 |
| Review console | review_console.py | 双语本机交互，动作仅调用服务，不直接操作数据库 |
| Backup | backup.py | pg_dump、manifest、校验与独立测试库恢复；调用方必须执行全库管理授权 |

adapters/database/records.py 的 Entity/Memory/Event 实现是事务内部端口。权限、review 与完整流程在 MemoryService；不得把 repository.save、SQL Connection 或 ReviewAuthority 直接开放为客户端/Agent 工具。

StorageAdapter 的通用二进制实现、真实 Summary/Embedding Provider、ConversationRepository、Web/MCP UI 仍是后续能力。所有 Mock 都明确标注，当前不会真实接入外部模型。

