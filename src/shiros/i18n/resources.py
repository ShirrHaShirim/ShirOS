"""Built-in interface strings, kept separate from domain and CLI logic."""

from shiros.i18n import Locale, TranslationCatalog


def default_catalog() -> TranslationCatalog:
    resources: dict[str, dict[Locale, dict[str, str]]] = {
        "core": {
            Locale.ZH_CN: {
                "memory.demo_complete": "记忆演示已完成",
                "memory.results": "结果",
                "memory.context_size": "上下文大小",
                "error.operation_failed": "操作失败",
            },
            Locale.EN_US: {
                "memory.demo_complete": "Memory demo complete",
                "memory.results": "Results",
                "memory.context_size": "Context size",
                "error.operation_failed": "Operation failed",
            },
        },
        "review": {
            Locale.ZH_CN: {
                "review.title": "ShirOS 本地审核",
                "review.initialized": "本机审核身份已初始化，scope：{scope}",
                "review.scope_required": "review 命令必须提供 --scope UUID",
                "review.menu": (
                    "1 预览并暂存 | 2 队列 | 3 检查 | 4 编辑 | 5 拒绝 | 6 批准 | "
                    "7 记忆 | 8 撤销 | 9 隐藏/显示 | 10 语言 | 11 授予角色 | "
                    "12 备份 | 13 恢复 | 0 退出"
                ),
                "review.prompt": "选择操作：",
                "review.filename": "导入文件名（仅收件目录）：",
                "review.preview": (
                    "预览：{title}\n类型：{kind}\n隐私允许暂存：{allowed}\n"
                    "原文：\n{original}\n规范化内容：\n{normalized}"
                ),
                "review.confirm_stage": "确认将这份预览暂存为候选项吗？(y/N) ",
                "review.confirm": "已确认，项目编号：{id}",
                "review.cancelled": "已取消",
                "review.candidate_id": "候选项 UUID：",
                "review.memory_id": "记忆稳定 UUID：",
                "review.target_id": "目标 UUID：",
                "review.queue_empty": "审核队列为空",
                "review.queue_header": "待审核候选项：",
                "review.inspect": "候选项记录（JSON）：\n{record}",
                "review.edit_text": "新的候选内容：",
                "review.updated": "已更新：{status}",
                "review.status.pending": "待审核",
                "review.status.edited": "已编辑",
                "review.status.approved": "已批准",
                "review.status.rejected": "已拒绝",
                "review.status.invalidated": "已失效",
                "review.privacy.allowed": "允许",
                "review.privacy.denied": "不允许",
                "review.approved": "已批准记忆：{id}",
                "review.memory_missing": "未找到可见记忆",
                "review.memory_found": "记忆记录（JSON）：\n{record}",
                "review.revoke_kind": "撤销类型（intake_source/source/memory）：",
                "review.revoked": "已撤销",
                "review.visibility": "输入 hide 隐藏，show 显示：",
                "review.visibility_done": "可见性已更新",
                "review.locale": "语言（zh-CN/en-US）：",
                "review.locale_invalid": "不支持的语言",
                "review.principal": "操作系统主体标识（windows:S-1-... 或 unix:...）：",
                "review.role": "角色：1 所有者  2 审核者  3 只读者  4 工作者：",
                "review.owner_granted": "角色授权已更新",
                "review.backup_created": "备份已创建：{path}",
                "review.archive": "备份目录中的 .dump 文件名：",
                "review.restore_name": "新建的隔离测试数据库名（shiros_restore_<name>_test）：",
                "review.restored": "已恢复到隔离数据库：{name}",
                "review.goodbye": "已退出",
                "review.invalid_choice": "无效选项",
                "review.error_generic": "操作失败",
                "review.error_permission": "权限不足或身份尚未初始化",
                "review.error_identity": "本机身份无效或尚未初始化",
                "review.error_path": "文件名无效；请仅输入收件目录中的文件名",
                "review.error_type": "文件类型无效，仅支持 TXT、Markdown、JSON、CSV",
                "review.error_size": "文件超过大小限制",
                "review.error_content": "文件内容无效或为空",
                "review.error_privacy": "隐私策略不允许暂存该内容",
                "review.error_candidate": "候选项不存在或已关闭",
                "review.error_memory": "记忆不存在",
                "review.error_source": "来源不存在或已撤销",
                "review.error_backup": "备份或恢复失败，请检查本机数据库工具和配置",
                "review.json_error": "记录无法显示",
            },
            Locale.EN_US: {
                "review.title": "ShirOS Local Review",
                "review.initialized": "Local review identity initialized; scope: {scope}",
                "review.scope_required": "The review command requires --scope UUID",
                "review.menu": (
                    "1 Preview/stage | 2 Queue | 3 Inspect | 4 Edit | 5 Reject | "
                    "6 Approve | 7 Memory | 8 Revoke | 9 Hide/show | 10 Language | "
                    "11 Grant role | 12 Backup | 13 Restore | 0 Exit"
                ),
                "review.prompt": "Choose an action: ",
                "review.filename": "Import filename (in inbox only): ",
                "review.preview": (
                    "Preview: {title}\nType: {kind}\nPrivacy permits staging: {allowed}\n"
                    "Original:\n{original}\nNormalized:\n{normalized}"
                ),
                "review.confirm_stage": "Stage this preview as a candidate? (y/N) ",
                "review.confirm": "Confirmed; item ID: {id}",
                "review.cancelled": "Cancelled",
                "review.candidate_id": "Candidate UUID: ",
                "review.memory_id": "Stable memory UUID: ",
                "review.target_id": "Target UUID: ",
                "review.queue_empty": "The review queue is empty",
                "review.queue_header": "Pending review candidates:",
                "review.inspect": "Candidate record (JSON):\n{record}",
                "review.edit_text": "Replacement candidate text: ",
                "review.updated": "Updated: {status}",
                "review.status.pending": "pending",
                "review.status.edited": "edited",
                "review.status.approved": "approved",
                "review.status.rejected": "rejected",
                "review.status.invalidated": "invalidated",
                "review.privacy.allowed": "allowed",
                "review.privacy.denied": "not allowed",
                "review.approved": "Approved memory: {id}",
                "review.memory_missing": "No visible memory found",
                "review.memory_found": "Memory record (JSON):\n{record}",
                "review.revoke_kind": "Revoke kind (intake_source/source/memory): ",
                "review.revoked": "Revoked",
                "review.visibility": "Enter hide to hide or show to show: ",
                "review.visibility_done": "Visibility updated",
                "review.locale": "Language (zh-CN/en-US): ",
                "review.locale_invalid": "Unsupported language",
                "review.principal": "OS principal ID (windows:S-1-... or unix:...): ",
                "review.role": "Role: 1 owner  2 reviewer  3 reader  4 worker: ",
                "review.owner_granted": "Role grant updated",
                "review.backup_created": "Backup created: {path}",
                "review.archive": "Backup .dump filename in backup directory: ",
                "review.restore_name": (
                    "New isolated test database name (shiros_restore_<name>_test): "
                ),
                "review.restored": "Restored to isolated database: {name}",
                "review.goodbye": "Exited",
                "review.invalid_choice": "Invalid choice",
                "review.error_generic": "Operation failed",
                "review.error_permission": "Permission denied or identity is not initialized",
                "review.error_identity": "Local identity is invalid or not initialized",
                "review.error_path": "Invalid filename; enter a basename from the inbox only",
                "review.error_type": "Unsupported file type; use TXT, Markdown, JSON, or CSV",
                "review.error_size": "File exceeds the size limit",
                "review.error_content": "File content is invalid or empty",
                "review.error_privacy": "Privacy policy does not permit staging this content",
                "review.error_candidate": "Candidate does not exist or is closed",
                "review.error_memory": "Memory does not exist",
                "review.error_source": "Source does not exist or has been revoked",
                "review.error_backup": (
                    "Backup or restore failed; check local database tools and configuration"
                ),
                "review.json_error": "Record could not be displayed",
            },
        },
    }
    return TranslationCatalog(resources)
