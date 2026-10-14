# Source Publication — 2026-10-14

## 范围

发布 0.7.18 当前源码、完整迁移链、测试、程序素材与技术说明，整体替换 GitHub 主分支的旧文件树。旧提交历史继续保留供回溯；本机数据库与运行配置不受源码发布影响。

不发布 `.env`、`.local/`、凭据、原件、个人数据库、备份、安装输出、历史插件 ZIP 或个人曲库迁移清单。维护脚本保留源码，其私有审核计划由所有者在本机提供。公共根目录配置使用模板说明，不附带机器专属 OpenCode 配置。

## 文档与 CI

README、架构、模块边界、MCP、音乐、客户端文档按当前实现重写。新增安全、贡献、版本摘要和 Codex 共建署名。更新迁移 head，区分健康 API、业务工作台、Mock、候选和正式结果。

CI 拆为 lint/unit、数据库集成、严格类型三个独立 job。类型检查仍严格失败，不能阻止其他验证运行，也没有通过 ignore 或 continue-on-error 隐藏失败。

## 验证

| 检查 | 本次结果 |
| --- | --- |
| 单元测试 | 210 passed，180 integration deselected |
| PostgreSQL 集成测试 | 180 passed，210 unit deselected |
| Ruff | 通过；修正 SQL 长行、import 位置和格式 |
| 严格 mypy，Windows 分支 | 未通过：835 errors / 39 files / 190 checked files |

类型检查错误集中于尚未完整标注的测试和维护脚本，也包含少量应用实现；它是公开的工程债务，本次不宣称全仓静态类型验收完成。数据库测试使用独立 `_test` 库和合成 scope。

本记录不证明新的安装包已生成、远程 MCP 已连接或 GitHub Actions 已通过。推送结果需检查远端 commit 和实际文件树，CI 状态以 GitHub 运行结果为准。
