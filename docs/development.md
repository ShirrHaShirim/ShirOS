# Development — 0.7.18

当前验证与类型检查债务见 [源码发布记录](source-publication-20261014.md)。默认 `uv sync` 安装 dev 工具；桌面构建另用 `uv sync --group desktop --locked`。业务工作台使用 `uv run shiros web --port 8001` 和另一终端中的 `uv run shiros open`；`shiros dev` 是独立健康 API。

## Python 工具链

```powershell
uv sync --locked --python 3.12
uv run shiros demo
uv run shiros memory-demo --locale zh-CN
uv run shiros test
uv run shiros lint
uv run shiros typecheck
uv run shiros dev --port 8000
```

`uv.lock` 固定本次解析的依赖；支持 Python 3.12+，本次验收使用 3.12。`dev` 是前台服务，Ctrl+C 停止；端口被占用时改用 `--port 8001`。只绑定 loopback，当前不具备公网服务认证条件。

`memory-demo` 需要先完成下文数据库启动和 migration；其余基础单元测试不需要运行数据库。

## Docker 数据库

准备 `.env` 后：

```powershell
docker compose up -d --wait
uv run shiros migrate
uv run shiros db-health
docker compose stop
```

Compose 使用 pgvector 0.8.6 / PostgreSQL 16，只绑定 loopback，保留 named volume。`.env.example` 没有真实密码；必须自行填入本地值。应用通过分开的 host/port/name/user/password 字段构建连接 URL，避免密码特殊字符误解析。

不要并行启动占用同一端口的 Docker 与便携数据库。普通 stop 不删除数据。

## Windows 便携数据库

没有 Docker 时可使用项目内 `.local` 运行时，无需修改系统服务或 shell profile。下载来源为 [Micromamba 官方分发](https://github.com/mamba-org/micromamba-releases) 和 [conda-forge pgvector](https://anaconda.org/conda-forge/pgvector)。

首次准备运行时：

```powershell
New-Item -ItemType Directory -Force .local/tools
Invoke-WebRequest -Uri 'https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-win-64' -OutFile .local/tools/micromamba.exe
& .local/tools/micromamba.exe --no-rc -r .local/mamba create -p .local/postgres -c conda-forge --override-channels 'postgresql=16' 'pgvector=0.8.6' -y
uv run python scripts/local_db.py init
uv run shiros migrate
uv run shiros db-health
```

`init` 仅在 `.env` 不存在时生成随机本地密码；已有 `.env` 不覆盖，需要配置有效 `SHIROS_DB_PASSWORD`。集群使用 SCRAM 认证，仅本机访问。创建 `shiros` 与独立 `shiros_test`；`.local/pgdata` 为数据目录，`.local/postgres.log` 为数据库操作日志。开发角色拥有迁移权限，不适用于生产。

以后使用：

```powershell
uv run python scripts/local_db.py start
uv run python scripts/local_db.py test
uv run python scripts/local_db.py status
uv run python scripts/local_db.py stop
```

这些命令从任意目录定位项目根目录。测试 helper 从 `.env` 读取密码并在子进程环境传递，不打印凭据。

## 集成测试

集成测试验证 migrate 幂等、pgvector/UTC、Mock 对话/Worker，以及完整 Source → Memory → Event → Summary/Snapshot → vector retrieval → ContextBundle。还覆盖审核绕过拒绝、敏感内容拒绝、历史版本、并发幂等、checkpoint、批次中途回滚与安全重试。

便携环境直接运行 `uv run python scripts/local_db.py test`。Docker 环境先创建独立测试库：

```powershell
docker compose exec db createdb -U shiros shiros_test
# 通过当前 shell 环境注入测试库 URL，不要在历史或版本库中保存真实密码。
uv run shiros integration
```

`SHIROS_TEST_DATABASE_URL` 应指向数据库名以 `_test` 结尾的专用库。它只从进程环境读取，不自动读取 `.env`。未设置时显式失败，不假装跳过即通过。测试保留基础 migration，合成行在事务中回滚。CI 使用一次性 service container 验证同一流程；CI 使用显式的合成测试用户名和密码，仅限临时 service container。

## Migration 与日志

```powershell
uv run alembic revision -m 'describe change'
uv run shiros migrate
uv run alembic upgrade head --sql
```

在线迁移需要可连接数据库和 extension 创建权限；离线 SQL 生成不等同实机验收。当前使用 SQLAlchemy Core 的参数化 SQL 仓储，不依赖 ORM autogeneration，请手写并审查 migration。最新版本为 `0031_music_catalog_sort`。降级会删除相关业务表，不应在有数据的环境中未经备份执行；升级保留旧 migration 和历史数据。

不要记录正文、请求体、凭据或含密码 URL。应用健康检查和 CLI 错误输出已收敛为无凭据摘要；不得在外部工具中打开 SQL 参数/正文调试日志。Memory、Source/Artifact、摘要/快照、Inference 和向量已在服务中门控，Event 仅存代码、ID、来源等结构字段，不携带自由正文。当前按会话授权的采集与持久化见 conversation-sync-v073.md；通用 ConversationProvider 仍含 Mock。

## 核心兼容接口与合成演示

本机已有便携环境时：

```powershell
uv run python scripts/local_db.py start
uv run shiros migrate
uv run shiros memory-demo --locale zh-CN
uv run shiros memory-demo --locale en-US
uv run python scripts/local_db.py test
```

演示的 UUID 与幂等键固定，只写合成内容；同一输入重复执行返回同一个 memory_id。完成当前升级后 `/health/ready` 应报告 head `0031_music_catalog_sort`；旧版本报告中的 migration 只描述当时状态。

Python 入口是 `shiros.shared_memory.build_shared_memory(engine, permissions)`。`reviews.approve(reviewer, actor, request)` 必须由可信本地应用/人工审阅调用，批准有效期 30 分钟，进程重启后失效；`memory.ingest(actor, request, approval)` 才是统一写入入口。不要将 ReviewAuthority 直接公开为匿名 HTTP 接口。

`MemoryWrite` 不接受 `reviewed`、`persistence_allowed`、`verified`；更新须提供稳定 memory_id 和 expected_revision。旧版本不会删除，新版本引用 supersedes_id。不同观察时间/来源仍保留为独立输入；同一标准化且脱敏输入在同一 actor/scope 下可跨幂等键去重。

`layers.process(actor, scope, limit=100)` 是显式增量操作，不启动后台代理。一个批次的摘要、快照、派生事件与 checkpoint 同事务提交，失败整体回滚；再次执行不会跳过失败事件。

`retrieval.search(actor, SearchRequest(...))` 要求明确 scope_ids，支持 structured/keyword/semantic/hybrid。中文 keyword 有字面子串后备，尚无专门中文分词器。semantic 使用实际 pgvector 距离，embedding 本身为固定 32 维测试 hash，不能代表生产语义质量。

`context.compile(ContextRequest(...))` 需要 scope_ids 或 project_id，默认每阶段最多 16 个候选；未授权 scope 不读取。预算是所选 item 的 UTF-8 JSON 字节保守估算（含来源与选择元数据），不含 bundle 外壳，不是特定模型 tokenizer 的精确计数。原始 Source 仅在 exact_source_ids 显式指定时获取。

所有测试使用随机隔离 scope 和合成数据。历史表不可变，因此测试保留其合成行，不隐式 TRUNCATE 或删除已有测试库；清理应另行针对专用测试数据库进行。

