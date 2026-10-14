# ShirOS v0.6.0：统一记忆架构首个兼容增量

本版本根据 Step Five 讨论启动重构。它交付可使用的存储、接口、审核与恢复基础，不宣称整份长期路线图已经完成。原通用记忆、音乐、文件、标签及客户端身份沿用；旧接口继续提供。

## 分层与权威存储

- L2 Detailed Memory：现有正式 Memory，保留全部 ID、正文、修订、证据等级和来源。音乐档案由音乐库持有，通过 `shiros://music/UUID` 引用，绝不复制成普通 Memory。
- L1 Domain Synthesis：受治理顶层标签下的领域综述。独立保存综述修订和全部依赖引用，综合长期情况、当前状态、变化、例外与未完成事项。
- L0 Global Core：经明确确认的全局核心候选与正式修订。依赖 L1 版本；普通 L2 写入、日常维护和 L1 发布均不会自动改写它。
- Evidence Store：既有 Source、来源记录和原件文件库。它独立于三层；L1/L0 的正文是综合判断，来源通过依赖引用保留，不伪造原始对话。

旧 `Summary` / `Snapshot` 表仍供旧上下文接口兼容使用，它们**不是**本版的新 L1/L0。新入口明确返回 `legacy_summary_is_l1=false`，不会把旧截断结果包装成领域综述。

当前 Canonical L2 适配器包括通用记忆与音乐；论文、研究、Git/Obsidian 文档的外部权威对象及项目过滤仍需下一阶段适配，不假装全部专业库已经实现。

## 领域治理与客户端流程

桌面「知识维护」页可将现有顶层标签显式启用为领域。只由本机管理员治理，不自动把每个标签注册成领域。音乐档案最多绑定一个维护领域；这个映射不会更改音乐的 Genre 或私人标签。

ChatGPT、Work、Codex、DeepSeek Harness、OpenCode 使用同一维护服务和契约。身份、Scope、权限从服务端绑定，客户端不能在 payload 中指定。已有 reader/proposer/editor 授权保持原状；reader 只读，proposer/editor 可以领取任务和提交候选，正式发布由本机审核进行。

流程：检索 L2 → 查看对应权威对象 → 建立领域任务 → 领取租约 → 分页读取证据 → 保存工作进度 → 写完整综述候选 → 本机查看草稿与依赖 → 审核发布。

本版提供客户端提交的综合草稿，不内置调用付费模型，也不以拼接或 Mock 假装已生成正式 L1。尚未自动生成任何真实领域综述，也未批量改写已有记忆。

## MCP / API

新增两个 MCP 工具，原 20 个工具继续兼容，共 22 个：

| 工具 | action | 作用 |
|---|---|---|
| knowledge_query | domains | 领域目录、层级状态 |
| knowledge_query | search / object | 跨记忆与音乐检索、准确读取权威记录 |
| knowledge_query | layers | L1/L0 修订、依赖、过期状态 |
| knowledge_query | jobs / evidence | 任务元数据、草稿、分页证据与进度 |
| knowledge_query | context | 按领域编译新层级与瞬时查询上下文 |
| knowledge_maintain | enqueue | 建立幂等任务，reason 支持 manual/daily/weekly/milestone/rebuild |
| knowledge_maintain | claim / checkpoint | 领取任务、保存可恢复的工作进度 |
| knowledge_maintain | draft | 提交完整候选，等待本机审核 |
| knowledge_maintain | cancel / schedule | 取消任务；检查全部已启用领域并排队 |

本机 `/ui-api/knowledge/{action}` 使用同一服务，额外提供管理员 `domain-enable` 与人工 `publish`。MCP 不提供这两个动作。L0 发布必须另行传入 `confirm=true`，并具备管理员权限；不能借普通综述任务改写原则。

`KnowledgeRequest` 禁止多余字段。列表默认 20、最多 100，单领域当前最多 2000 个详细对象，超限明确报错，不能把截断包当成全部领域证据。草稿正文及进度均通过 Privacy Gate。

### 维护候选示例

首先调用 `knowledge_query("domains", {})` 获得已经由本机启用的领域 ID。

```json
{"action":"enqueue","payload":{"domain_id":"领域 UUID","request_id":"新请求 UUID","reason":"manual"}}
```

之后用返回的任务 UUID 调用 `claim`。调用 `evidence` 时必须遍历分页；每条依赖包含 canonical owner、对象 ID、revision、Source ID。音乐具体评价与历史可按对象调用 `object`。

`draft` 的 body 必须包含五个非空段落：

```json
{
  "id":"任务 UUID",
  "body":{
    "long_term":"有依据的长期规律、目标与发展脉络",
    "current":"当前状态和证据支持的综合判断",
    "changes":"重要变化、演变与纠正，保留时间和前后关系",
    "exceptions":"例外、矛盾、约束和证据不足之处",
    "unfinished":"仍未完成或需要核验的事项"
  }
}
```

五段是完整综述的必要结构，不是五段截取 L2。提交后状态为 `awaiting_review`，不会产生正式修订。批准只说明允许持久化，仍保留 `model_guess` 证据等级，不提高真实性。

## 过期、版本与恢复

依赖指纹覆盖领域配置、可见 L2 集合、对象修订、来源及音乐证据可用性。隐藏、撤销、增删关联或 L2/音乐修订后，查询立即显示受影响综述过期；不需要模型先发现变化。

过期综述正文不进入新上下文。旧历史正文也逐版验证依赖，不能在新综述生效后通过旧版本读出已隐藏证据。保留修订、依赖和审核历史。

同一领域只允许一个活动任务；相同 request_id 返回同一任务，改变请求内容拒绝。新依赖使旧任务失效时，保留旧任务与审计，取消旧任务并建立新任务。领取租约为 15 分钟；过期后其他授权客户端可接力，保存的 progress 保留。发布前重新比较依赖及基础 revision，拒绝覆盖已改变的结果。

progress 是工作进度，不是成功检查点。只有正式发布才推进 head checkpoint。每日无变化不产生新任务；每周可建立审核任务，但相同正文和依赖不重复产生修订。任务、草稿及动作日志持久化，综述修订和审计采用不可变历史表。

当前提供版本历史与重建；没有一键回滚到旧正文，避免绕开当前依赖审核。失败原因通过接口返回，尚未提供完整的运营告警面板或模型语义冲突检测器。

## 主题修复

配色从仅依赖 localStorage 改为由 `workspace_preferences` 按 scope + actor 保存。工作台 POST `/ui-api/preferences/theme` 读取，传入 `theme` 保存；仅允许五种配色，沿用本机会话鉴权。新桌面进程、新端口、新浏览环境都能读取同一个用户偏好。

localStorage 保留作为启动缓存。桌面 WebView 使用专用持久目录，减少其他外观偏好丢失；授权会话仍使用既有短期 token，不把 token 放进报告或同步到 GitHub。

## 验收与后续

新增迁移 `0014_knowledge_layers` 是附加表，不修改任何旧记录。升级前真实备份已在独立数据库恢复，七类旧数据校验一致；在恢复副本上验证升级、回退、再升级，旧数据校验仍一致。

已通过真实 PostgreSQL 测试：跨会话主题保留、L1 依赖与审核、L0 确认、隐藏证据阻断历史正文、任务幂等、五种客户端身份、并发租约争抢、过期接力和音乐权威引用。真实 WebView 验证已选择主题后完全退出，在全新临时 WebView 进程中仍恢复相同主题。

仍待后续：模型自动生成与质量评估、专业论文/研究适配、项目过滤、自动候选提取与语义冲突、完整差异预览、跨系统投影，以及 ChatGPT Tasks 真实定时执行环境的接入验收。
