# Step Three: 本机审核操作

## 初始化

先启动本机 PostgreSQL，再执行 `uv run shiros migrate`、`uv run shiros db-health`。

```powershell
uv run shiros review-init --locale zh-CN
uv run shiros review --scope <返回的scope UUID> --locale zh-CN
```

首次初始化将当前 OS 用户绑定为 Owner，并返回 scope。再次初始化返回已有 Owner scope。未注册用户默认拒绝；CLI 不允许伪造 actor。共享同一个 Windows 登录账号的进程共享身份。API 仍仅监听 loopback 健康检查；审核控制台不监听任何端口。

## 文件与候选

1. 手动将准备审核的文件放到项目 `.local/inbox`；程序不会扫描 Obsidian Vault 或磁盘。
2. 选择预览，输入单层文件名。支持 UTF-8 TXT、Markdown、JSON、CSV，文件上限 64 KiB；规范化后的候选上限 20,000 字符。
3. 检查原文、规范化文本与隐私结果。预览本身不持久化；终端显示内容也属于本机隐私边界，注意录屏和终端历史。
4. 确认暂存，得到候选 UUID。候选及来源保存的是安全文本，秘密或明确禁止持久化的资料直接拒绝。
5. 在队列检查来源、隐私、provenance 和历史；编辑会重新检查隐私。批准才写正式 Memory；拒绝不会产生 Memory。

同 scope 中相同安全正文的文件检测为重复，不创建新候选。路径不是稳定主键。JSON 格式化、Unicode NFKC 和换行规范化可能改变显示形式；文件名作为安全标题，绝对路径不持久化。suggested_entity_links 当前为空，未实现自动实体抽取。队列最多显示最早的 100 个待审候选；本阶段不面向批量积压。

## 角色

| 角色 | 权限 |
| --- | --- |
| Owner | read / review / approve / write / revoke / admin / execute |
| Reviewer | read / review / approve / write |
| Reader | read |
| Worker | read / write / execute；无人工审核或批准权 |

Owner 可在本 scope 授予角色；Windows principal 使用 `windows:S-1-...`，Unix 使用 `unix:<uid>`。权限在执行动作时查询数据库，不相信客户端布尔字段。不能降级当前 Owner 自身。整库备份和恢复仅允许原始 bootstrap Owner，不随普通 scope Owner 授权扩散。

## 撤销与遗忘

| 语义 | 行为 |
| --- | --- |
| Supersede | 新版本替代当前版本，历史仍可明确查询 |
| Hide / Show | 可逆地从正常检索隐藏/恢复整个 memory_id |
| Revoke Source | intake/core 来源及相关稳定记忆撤销，候选失效，不允许重新批准或去重重放复活 |
| Revoke Memory | 整个 memory_id 永久退出正常检索；show 不能恢复 |
| Erase | Deferred；尚无允许物理删除的策略或执行入口 |

正常 exact/source、四种检索、摘要/快照及 Context 均排除已撤销资料。保留 Source、Memory、事件、审核历史、旧向量和派生行作为审计，数据库管理员仍能访问。撤销不是密码学擦除，也不能追回已经显示或导出的内容。旧备份保留撤销前状态，恢复旧备份时需重新评估撤销状态，禁止直接替换当前主库。

## 备份与恢复

在 review 控制台选择备份，生成 `.local/backups/shiros-<UTC>-<UUID>.dump` 与同名 `.json`。备份覆盖 PostgreSQL 全库结构、表数据、文本 Artifact、身份/授权、审核历史、事件、派生数据、向量及 migration 信息。不包含 `.env`、项目代码、便携运行时、原始 inbox 文件或尚未实现的外部二进制 Artifact Store。

恢复菜单只接受本备份目录的可信归档，校验 SHA-256、manifest、migration revision 和 PostgreSQL major。目标必须是不存在的 `shiros_restore_<name>_test`，不会覆盖主库或已有数据库。恢复成功后保留隔离库，测试只清理自己创建的随机测试库。失败会清理本次创建的目标，不接管已有数据库。

归档没有加密，包含已允许持久化的私人信息与历史撤销内容；请保护目录和备份介质。checksum 只能检测意外损坏，不能鉴别恶意来源。`pg_restore` 可执行归档中的 SQL，不要恢复他人发送的文件。本步骤仅验证同机独立数据库恢复，不代表异机灾难恢复完成。

## 验证

```powershell
uv run shiros test
uv run python scripts/local_db.py test
uv run shiros lint
uv run shiros typecheck
uv run shiros migrate
uv run shiros db-health
```

仅使用合成数据。真实个人资料未自动导入，外部模型未接入。审核服务是同步接口，异步调用方须在线程中调用，不能直接从已运行的事件循环调用 approve。
