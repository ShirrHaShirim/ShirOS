# Personal Knowledge OS

## Project Charter / 总则

Personal Knowledge OS（PKOS）是一套独立于具体 AI、平台和应用的个人数字基础设施，用于长期管理我的记忆、知识、论文、音乐、项目、人物关系、文件、AI 对话与自动化任务。

PKOS 才是长期主体。ChatGPT、Codex、Work、DeepSeek harness、Claude、Gemini、本地模型、Obsidian、VSCode 等均只是它的计算节点、数据源或前端。

---

# 1. 核心原则

1. **系统拥有状态，模型不拥有状态。**
    
2. **Conversation ≠ Memory。** 对话是 Source 或 execution trace，长期有效信息应进入结构化系统。
    
3. **Context 按需加载。** 默认流程：
    

```
Global Core → Domain Synthesis → Canonical L2 Search → Exact Retrieval / Evidence
```

4. 长期记忆采用：
    

```
L0 Global Core / 稳定且受控的全局核心
L1 Domain Synthesis / 受治理领域的综合记忆
L2 Detailed Memory / 通用记忆及专业库权威详细记录
Evidence Store / 三层之外的原始来源与证据
```

5. 重要变化进入 Event Store，并通过 Checkpoint 增量处理。
    
6. 数据必须具有稳定 ID、来源和必要的 provenance。
    
7. 模型、Provider、Harness、Embedding、UI 等都应尽量可替换。
    
8. 优先采用开放格式和标准接口，避免 vendor lock-in。
    

---

# 2. 核心架构

PKOS 至少包括：

```
Shared Memory Core
Entity / Relation Layer
Event Store
Summary Engine
Retrieval Layer
Context Compiler

Conversation Store
Conversation Gateway
Unified Conversation UI

Project / Task / Job / Run
Worker Router
Plugin System

Privacy Gate
Permission System
Artifact Store
Workspace Projection

Domain Databases
Provider / Connector Adapters
MCP / API Layer
```

共享基础对象包括：

```
Entity
Relation
Source
Artifact
Memory
Event
Summary
Snapshot

Conversation
Message

Project
Task
Job
Run
Checkpoint

Inference
```

各 Domain 可以建立自己的专业 schema，不应把所有业务塞进万能 JSON。

PostgreSQL 可作为主要结构化事实源，pgvector 用于语义检索，大型文件进入 Artifact Store。

---

# 3. Memory 与 Context

模型启动任务时不得自动读取完整历史。

Context Compiler 根据：

```
当前任务
Project / Domain
模型能力
Token budget
权限
相关实体
近期 Events
```

生成最小必要 ContextBundle。

它负责：

```
选择 Snapshot
选择 Summary
检索相关 Memory / Entity / Event
排序
去重
裁剪
附带 provenance
```

Summary 用于压缩历史，但不得替代允许长期保存的原始来源。

---

# 4. Agent、Harness 与模型调度

至少计划支持：

```
ChatGPT
Codex
ChatGPT Work
DeepSeek harness
```

未来可扩展：

```
Claude
Gemini
Local LLM
其他 Agent / Harness
```

不同模型通过统一 Provider / Worker Adapter 接入。

Worker Router 可以依据：

```
模型能力
额度
成本
可用性
任务复杂度
历史表现
工具权限
上下文限制
```

进行调度。

Codex 额度不足时，可以将适当的维护任务交给 DeepSeek 或其他 Worker。

领域维护逻辑尽量定义为模型无关 Plugin，而非绑定某个模型。

---

# 5. Human Chat 与 Agent Run 分离

必须区分：

```
Human Conversation
= 我主动与 AI 对话

Agent Run
= PKOS 自动派遣 AI 完成任务
```

自动维护任务不得依赖无限增长的长期聊天。

每次执行创建独立 Run，只读取：

```
当前任务
必要 Snapshot / Summary
新增 Events
按需检索的数据
```

执行结果写回 PKOS。

下一次运行重新建立 Context。

---

# 6. Unified Conversation System

PKOS 应提供统一 AI 对话系统。

我可以在同一个 UI 中新建和管理与不同 AI 的对话，例如：

```
GPT
Codex
DeepSeek
Claude
Gemini
Local models
其他 Harness
```

创建 Conversation 时选择：

```
Provider
Model
Account / Credential Profile
Project
```

一个 Conversation 创建后：

**Provider 和 Model 原则上保持固定。**

同一对话中不直接切换模型。

如果需要换模型，应：

```
Fork / Continue in New Conversation
```

新 Conversation 可以继承由 Context Compiler 生成的必要上下文，但保持独立会话身份。

---

# 7. Conversation Gateway

统一 UI 不应直接依赖各个平台的具体实现。

所有人工聊天通过：

```
Unified Conversation UI
        ↓
Conversation Gateway
        ↓
Provider Adapters
        ↓
GPT / Codex / DeepSeek / Claude / ...
```

Provider Adapter 可以使用：

```
Official API
OAuth
官方 Harness Session
其他受支持授权方式
```

不应依赖抓取浏览器 Cookie 或其他脆弱的非正式认证方式作为核心架构。

---

# 8. Conversation Store

所有已连接 AI 的会话可以进入统一 Conversation Store。

至少记录：

```
Conversation
Provider
Model
Account Profile
Project
Title
Created / Updated
Parent Conversation
Messages
Attachments
Related Entities
Generated Memories
Generated Knowledge
Generated Projects
```

支持：

```
跨模型全文搜索
按 Provider / Model / Project / Topic 分类
对话树
Fork
来源追踪
项目关联
```

Conversation Store 是历史来源，不代表所有消息自动成为长期 Memory。

---

# 9. Conversation Tree

跨模型继续讨论时应保留关系。

例如：

```
GPT 架构讨论
├── Codex 实现
└── DeepSeek 审查
    └── GPT 后续讨论
```

这样可以记录不同 AI 围绕同一问题的协作历史。

---

# 10. 多前端原则

PKOS 的 Conversation UI 和知识系统不得绑定某一个宿主。

可以拥有多个前端：

```
PKOS 独立 Web UI
Obsidian
VSCode
ChatGPT 内的 PKOS Panel / Plugin
未来其他客户端
```

这些前端访问同一 PKOS Backend。

如果某个平台允许插件、侧边栏或嵌入式应用，则可以提供 PKOS Conversation Panel，但：

**PKOS 不依赖该宿主才能运行。**

---

# 11. Workspace Projection

适合人类阅读和编辑的内容，尤其：

```
Knowledge
Papers
Projects
Selected Conversations
```

必须能够投影为开放文件系统 Workspace。

优先支持：

```
Markdown
YAML / JSON
PDF
普通附件
```

供：

```
Obsidian
VSCode
Git
CLI
Codex
普通文件管理器
```

访问。

原则：

```
Database
= structured source of truth

Workspace
= human-readable / editable projection
```

Markdown 使用稳定内部 ID、YAML frontmatter，并可支持 wikilink。

允许人工编辑的字段可以受控同步回数据库；系统管理字段不得被外部文件无条件覆盖。

---

# 12. Provenance 与事实等级

长期信息应尽量记录：

```
来源
时间
生成者
验证状态
Confidence
```

必须区分：

```
Fact
Explicit Statement
Derived Fact
Pattern
Inference
Hypothesis
Model Guess
```

Inference 必须保存 evidence 与 confidence，不得自动冒充事实。

---

# 13. Privacy First

所有长期持久化内容必须经过 Privacy Gate。

原则：

```
Readable ≠ Persistable
Transient Context ≠ Persistent Memory
Derived Data is still Data
```

真实姓名允许保留。

除姓名外，不必要的敏感信息原则上自动脱敏或泛化，包括：

```
电话号码
邮箱
身份证 / 护照
银行卡 / 银行账户
支付账户
精确地址 / 位置
IP / 设备信息
平台账号标识
密码
Cookie
Token
API Key
验证码
其他认证信息
```

必要关联优先使用：

```
Internal ID
Salted Hash
Pseudonymous ID
```

具体交易金额默认泛化为：

```
小额 / 中额 / 较大额等
```

而不是长期保存精确数值。

---

# 14. Confidentiality

如果对话明确要求：

```
保密
不要外传
不要记录
私下说
暂时不能公开
```

或内容明显属于高度私密、第三方秘密、认证凭据等，则原则上：

```
persistence_allowed = false
```

不得进入：

```
Memory
Summary
Snapshot
Knowledge
Project
Relationship Summary
Inference
Embedding
Vector Index
长期 Run Summary
持久日志
Workspace Projection
```

模型可以在当前任务中临时使用，但不能通过：

```
摘要
推断
Embedding
日志
知识节点
项目节点
```

绕过限制。

不确定是否应该保存时，默认不保存。

---

# 15. Music Domain

音乐库是首个大型 Reference Implementation。

核心规则：

```
豆瓣五星 = 主曲库准入条件
```

豆瓣作为 curation interface。

添加、删除、升星、降星等变化进入 Event Stream。

五星降级时：

```
退出 active library
≠ 删除历史
```

永久保留：

```
历史评分
元数据
播放历史
事件历史
```

音乐模型至少区分：

```
Album
Release
Recording
Work
Movement
Track
Composer
Performer
Conductor
Ensemble
Label
SourceMapping
```

---

# 16. Music UI 与 Playback

曲库支持按：

```
作曲家
作品
体裁
时期
指挥
独奏家
乐团
录音年代
唱片公司
专辑
自定义标签
```

浏览。

支持 contextual album view。

如果一张专辑同时包含协奏曲和交响曲，从“协奏曲”进入时，只显示协奏曲部分，并提供：

```
显示全专辑
```

当前 scope 应传递给播放队列。

计划接入：

```
Apple Music
网易云音乐
Bilibili
```

通过统一 Media Adapter。

播放历史由 PKOS 自己长期保存，与具体音乐服务解耦。

---

# 17. Papers Domain

论文库逐渐支持：

```
Paper
Author
Venue
Version
PDF
Section
Equation
Figure
Theorem
Method
Dataset
Claim
Citation
Reading State
Notes
```

论文可由：

```
DOI
arXiv
PDF
网页
文件
```

进入系统。

应支持全文检索、语义检索、引用关系、阅读状态和 Knowledge linking。

论文及笔记应可通过 Workspace Projection 在 Obsidian / VSCode 中访问。

---

# 18. Knowledge Domain

知识库与论文库分离。

原则：

```
PDF ≠ Paper
Paper ≠ Note
Note ≠ Knowledge
Knowledge ≠ Memory
```

Knowledge 可以表示：

```
Concept
Definition
Theorem
Claim
Problem
Method
Idea
```

知识节点保留 provenance，并通过 Workspace Projection 开放给 Obsidian / VSCode。

---

# 19. People & Relationship Intelligence

建立人物与关系智能 Domain，作为长期社交助理。

授权聊天软件只读接入，用于：

```
人物与身份关联
关系梳理
互动历史
关系变化
共同话题
共同项目
Open Loops
重要对话提取
项目 / 思想候选
对方可能来意分析
沟通策略辅助
```

聊天软件 Connector 必须保持只读。

禁止：

```
自动发消息
自动回复
修改 / 删除消息
增删好友
修改群组
发起通话
支付
转账
收款
钱包操作
其他对外社交动作
```

---

# 20. People 数据模型

建议区分：

```
Person
Identity
Account
Conversation
Conversation Segment
Message
Relationship
Interaction
Topic
Social Event
Relationship Summary
Inference
Open Loop
```

低置信度身份匹配不得自动合并。

分析人物时优先：

```
Relationship Snapshot
→ Relationship Summary
→ Relevant Conversation Segment
→ Exact Messages
```

避免重复读取完整聊天历史。

---

# 21. Valuable Conversation Extraction

聊天可以产生：

```
MemoryCandidate
KnowledgeCandidate
ProjectCandidate
ConversationInsight
OpenLoop
```

例如：

```
Conversation → ProjectCandidate → Project
Conversation → KnowledgeCandidate → KnowledgeNode
```

所有 Candidate 必须经过 Privacy Gate。

明确保密内容不得因此进入其他 Domain。

---

# 22. Cross-Domain Graph

不同 Domain 可以建立关系，例如：

```
Paper → discusses → Concept
Concept → relevant_to → Project
Person → collaborator_on → Project
Conversation → generated → Knowledge
Conversation → generated → Project
Person → recommended → Album
Conversation → references → Paper
```

最终形成机器可读的 Personal Graph。

不要求所有数据都使用图数据库。

---

# 23. Unified UI

PKOS 最终提供统一入口：

```
Conversations
Music
Papers
Knowledge
People
Projects
Files
Activity
Search
```

统一支持：

```
Search
Filter
Sort
Timeline
Relation / Graph View
AI Query
```

当前页面、Entity、筛选条件和 Project 都可以成为 Context Compiler 的输入。

---

# 24. 权限与安全

所有 Agent 遵守最小权限。

标准访问路径：

```
Agent
→ Tool / API
→ Validation
→ Permission Check
→ Privacy Gate
→ Service Layer
→ Database
```

不得默认允许 Agent 直接修改生产数据库。

Secrets 只能通过安全运行环境提供，不得进入：

```
Git
Memory
Event
Summary
Embedding
Run Trace
普通日志
Workspace
```

---

# 25. 开发原则

系统采用渐进式开发。

优先：

```
Correctness
Privacy
Data Integrity
Clarity
Testability
Maintainability
Extensibility
```

避免：

```
Conversation 充当数据库
万能 JSON Schema
无限增长的维护对话
Agent 直接 SQL
模型 / Provider 锁定
未经验证的推断变成事实
敏感数据进入日志
为了未来需求过度设计
```

---

# 26. 长期目标

最终 PKOS 应保证：

```
Data persists.
Models are replaceable.
Conversations are unified but independent.
Context is loaded lazily.
Memory is summarized continuously.
Events are processed incrementally.
Runs are isolated.
Facts have provenance.
Inference stays inference.
Sensitive data is minimized.
Confidential data does not become memory.
Knowledge remains available in open formats.
Agents operate through controlled interfaces.
```

即使未来更换模型、Harness、音乐平台、聊天平台或 UI，我的：

```
AI 对话历史
记忆
知识
论文
音乐收藏
聆听历史
项目
人物关系上下文
重要对话成果
文件
历史事件
```

仍然属于 PKOS，并可继续被下一代工具和模型读取、检索、维护与使用。

## 2026-10-09 分层修订与当前交付边界

按对象确定 Canonical Owner：通用 Memory、音乐等专业库、Git 与 Obsidian 分别持有自己的权威数据，其他系统通过受控引用访问。既有 Memory 作为 L2，保留 ID、修订、正文和来源；旧 Summary/Snapshot 不直接提升为新 L1/L0。普通 L2 写入使相关 L1 依赖过期；L1 正式发布不自动改写 L0。来源置于独立 Source/Provenance/Evidence Store。模型是可替换计算节点，所有客户端使用同一维护服务、服务端身份和 Scope。

v0.6.0 交付首个兼容增量，真实实施范围、接口与尚未完成事项以 docs/knowledge-v060.md 为准，不把长期原则当成已交付功能。已有架构原则记忆及审核历史沿用，不重复建立或覆盖。
