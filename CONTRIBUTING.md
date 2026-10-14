# Contributing

按 [README](README.md) 和 [开发说明](docs/development.md) 安装锁定依赖，准备本机配置与独立测试库。不要复制维护者的 `.local/`。

## 修改约束

- 保持适配器 → 服务 → 权限/隐私/审核 → 仓储的调用方向。
- 保留稳定 ID、不可变历史、来源与证据等级；更正通过新修订实现。
- 追加 Alembic migration，不重写已应用的迁移语义。
- 契约变更同时验证拒绝路径、scope 隔离和乐观并发。
- 音乐权威事实与浏览范围分开；UI 过滤不裁剪完整详情。
- 界面语言不翻译用户正文、音乐名称或私人标签。

```powershell
uv sync --locked --python 3.12
uv run shiros lint
uv run shiros test
uv run mypy --platform win32
uv run shiros integration
```

集成测试需要 `SHIROS_TEST_DATABASE_URL` 指向 `_test` 库，备份测试需要 PostgreSQL 16 客户端工具。严格类型债务见 [验证记录](docs/source-publication-20261014.md)；新增问题不能由既有债务掩盖。提交说明包含最终行为、验证和限制，不把 Mock 或待审候选写成验收成功。

## 共建署名

**ShirrHaShirim** 是所有者与维护者。**OpenAI Codex** 是参与实现、修复、文档和测试的 AI 共建者；最终产品与发布决定由所有者作出。
