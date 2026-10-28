# 企业知识库 RAG 平台 — 产品需求文档
# Enterprise Knowledge Base RAG Platform — Product Requirements Document

| 项目 / Item | 内容 / Content |
| --- | --- |
| 文档名称 / Title | 企业知识库 RAG 平台 PRD |
| 版本 / Version | v1.8 (Draft) |
| 日期 / Date | 2026-10-28 |
| 状态 / Status | 待评审 / Pending Review |
| 需求来源 / Source | https://boilingwater.cn/solutions/enterprise-kb-rag |
| 文档负责人 / Owner | 产品经理 / Product Manager |
| 评审方 / Reviewers | 研发 / Engineering、算法 / ML、安全合规 / Security & Compliance、交付 / Delivery、法务 / Legal |

**版本记录 / Revision History**

| 版本 | 日期 | 变更 / Changes |
| --- | --- | --- |
| v1.7 | 2026-10-27 | **依外部基准与市场证据复核修订**。核心变更：① FR-04-05 融合权重给出默认取向（企业内部语料下稀疏腿更强，证据见 附录 E · EV-1），原先无默认值会被行业惯例带偏；② FR-04-08 重排由无条件启用改为**证据驱动的条件启用**，验收门槛改为阻断性；③ 新增 FR-04-17 双深度召回基线（P0），作为重排决策与漂移监测的共同基线；④ FR-05-05b 补入可引用的外部证据（EV-2：引用事实性随工具调用深度衰减约 42%），强化 NG-7 与 N-5 的论据；⑤ §10.2 新增**引用支撑率 CSR**，把原「幻觉率 ≤2%」从人工印象变为可计算指标；⑥ §13 为实体层设立**可测量的重启触发条件**，替代主观判断；⑦ 新增附录 E 外部证据等级表 —— 明确哪些外部数字可以进合同、哪些**禁止引用**。需求总数 206 → 207（新增 FR-04-17）|
| v1.8 | 2026-10-28 | **依第五方评审（MiniMax）修订**。19 条意见中 14 条成立、5 条不成立（逐条核对见决策台账）。修复：① **TD-03 加载接缝 —— 真实越权路径**：`acl_entries.inheritance_broken` 由同步层写入、`evaluate()` 从 `ObjectNode` 读取，中间的 `chain_of()` 此前只在伪代码里出现一次、无规格无测试。新增 TD-03 §3.5 `build_chain` 纯函数契约 + 性质 P-5，并补 5 条加载层测试（已做变异验证：注入该 bug 后 3 条变红，而原有 18 条求值向量**全绿** —— 证实旧套件对此完全盲视）。`AclRow` 同时删去 `inheritance_broken`，确保只有一个读取点；② **「规范化正文」无规范** —— I-7 自称事后不可补，但规则未定则不可复现。新增 TD-01 §2·5：规则集 norm-v1（NFC，**禁用 NFKC**，否则中文全角折半角会改变全部偏移量）、三元组可解释性（parser_version + normalization_version + content_hash 复现校验）、R-N1~R-N5；schema 加 `documents.parser_version` / `normalization_version`；③ 删除设计 §5.4 与 PRD 缓存语义冲突（它仍按"按权限分区"写，与 v1.7 的 14a 相反）已对齐；④ `citations.chunk_version` 悬空 —— schema 加 `chunks.version`；⑤ `principal_memberships` 加 `container_type`，并把 group/role 共用命名空间的不变量写入 TD-03 P-3；⑥ TD-01 新增 C-7（object_id 租户内唯一，TD-03 P-0）、C-1 改为**不设 DEFAULT**、C-5 声明 `audit_log.log_id` 为唯一例外；⑦ TD-02 补 `checkpoint`/`resume` 语义 S-1~S-6 与未声明能力的行为 U-1~U-3，Python 下限改 3.12；⑧ 证据编号 E-1~E-6 → **EV-1~EV-6**（E-* 是删除设计的待决问题命名空间，v1.7 撞号了），附录「14.3」改为「附录 E」。新增 Q-21~Q-23。需求总数 207（无新增 FR）|
| v1.0 | 2026-09-01 | 初稿，122 条功能需求 |
| v1.1 | 2026-09-01 | 新增可配置合规包（FR-11~FR-14）与第 8 章合规保障体系；细化国密/密评、PIPL 数据主体权利、生成式 AI 备案；RPO/RTO 分级；新增合规交付物与复测约定。已对全部法规引用做外部核查并修正 8 处（见下） |
| v1.2 | 2026-09-01 | 依《FR-12-04 贯穿式删除技术设计文档》回填：删除范围由「七处」修正为**十处**（补入备份归档、精选问答、评测集）；新增 FR-12-17 抑制清单、FR-12-18 限制处理、FR-12-19 主体反向索引、FR-12-20 请求身份核验；FR-03-08、FR-04-14、FR-10-07 增加删除链路前置约束。需求总数 176 → 180 |
| v1.6 | 2026-09-01 | **依四方评审（产品/研发/测试/运营）修订**。确认并修复 10 项缺陷：Phase 1 出口标准「七处」残留旧数据、`Citation.locator` 未结构化、**FR-09-01 记录提问原文与 FR-12-16 默认不留存直接冲突**、FR-06-05 角色数与 §3 不一致、**Self-RAG 与流式输出的结构性冲突**（拆为轻量 P0 / 重型 P1）、FR-04-14 缓存优先级错配（基础缓存升 P0）、删除证明未区分逻辑/物理完成时点、商用 API 与零外部依赖模式互斥未声明、缺离职失效需求、FR-03 验收与 NFR-P-3 口径未说明嵌套关系。新增 **FR-15 可观测性**（8 条）与 **FR-16 限流配额**（5 条）。需求总数 190 → 206 |
| v1.5 | 2026-09-01 | **修正 ACL 模型的一处安全缺陷**：`AclEntry` 原设计只能表达"允许名单"，装不下 NTFS 显式 Deny 与 SharePoint 断开继承，会导致静默越权。新增 `effect` / `scope_level` / `inherited_from` / `inheritance_broken` 四个字段与 FR-06-28 拒绝优先求值语义；新增 FR-01-19 连接器六项接口契约、FR-01-20 拒绝规则读取。需求总数 187 → 190 |
| v1.4 | 2026-09-01 | 决策 Q-11 落地：当前仅交付**企业内部档（PF-1）**，对客档作为独立产品后续分支（新增 NG-9）。新增 Phase 1 必做预留 FR-01-18 受众标签、FR-04-16 受众过滤、FR-05-20 证据快照；灾备分级表补充档次说明；建立保留编号规则。需求总数 183 → 187（含 FR-03-12 全表 tenant_id）|
| v1.3 | 2026-09-01 | 依《架构定位与 Agent 接入设计》回填**已决策部分**：产品定位改为「统一访问层」；新增 NG-7 不作为编排者、NG-8 v1–v2 不建实体层；新增 Phase 1 必做的两项可扩展性预留 FR-02-13（字符级跨度锚点）、FR-12-22（删除位置类型可扩展）及 FR-12-21（实体索引分表）。需求总数 180 → 183 |

> **待决策部分未并入本 PRD**：MCP 接入（FR-07-11~13）、身份适配层与四级能力阶梯（FR-06-18~27）、信任分级（FR-01-17、FR-04-15、FR-05-18~19）等属 Phase 3 范围，以提案形式保留在《架构定位与 Agent 接入设计》v1.1 第 8 章，待该阶段立项后再并入。
> *Phase-3 proposals are deliberately kept out of this PRD until that phase is approved.*

> **法规引用核查 / Regulatory citation review**：v1.1 的合规内容经外部检索核查，修正了 8 处引用错误，包括：《网络安全法》2025 年修正后的条款重新编号、密评强制范围（等保三级不自动触发）、TLCP 标准号（GB/T 38636 而非 GM/T 0024）、等保三级异地**实时**备份要求、身份鉴别的适用主体范围、《银行保险机构数据安全管理办法》正式名称、生成式 AI 备案的双重触发条件。
> 法规会持续修订，**本文档的引用在使用前应由法务复核**，不应直接作为合规依据。
> *Citations were externally verified and eight errors corrected. Regulations change; legal counsel should re-verify before this document is relied upon.*

> **说明 / Note**：本 PRD 基于滚水科技官网「企业知识库 RAG 解决方案」页面的能力描述反向拆解而成。页面上的指标（92.4%、<2.5s、-68% 等）为厂商自述、未披露口径，本文档将其记录为**产品目标值 (Target)**，而非已验证事实；正式立项时须重新定义测量口径。
> This PRD is reverse-engineered from the capability description published on the vendor's solution page. All figures quoted there are vendor-stated without disclosed methodology; this document records them as **product targets**, not verified facts. Measurement definitions must be re-established at project kickoff.

---

## 目录 / Table of Contents

1. [产品概述 / Product Overview](#1-产品概述--product-overview)
2. [目标与非目标 / Goals & Non-Goals](#2-目标与非目标--goals--non-goals)
3. [目标用户与角色 / Users & Personas](#3-目标用户与角色--users--personas)
4. [核心场景 / Key Use Cases](#4-核心场景--key-use-cases)
5. [产品架构总览 / Product Architecture Overview](#5-产品架构总览--product-architecture-overview)
6. [功能需求 / Functional Requirements](#6-功能需求--functional-requirements)
7. [非功能需求 / Non-Functional Requirements](#7-非功能需求--non-functional-requirements)
8. [合规与安全保障体系 / Compliance & Assurance Framework](#8-合规与安全保障体系--compliance--assurance-framework)
9. [数据模型 / Data Model](#9-数据模型--data-model)
10. [成功指标 / Success Metrics](#10-成功指标--success-metrics)
11. [发布计划 / Release Plan](#11-发布计划--release-plan)
12. [风险与依赖 / Risks & Dependencies](#12-风险与依赖--risks--dependencies)
13. [待定问题 / Open Questions](#13-待定问题--open-questions)
14. [附录 / Appendix](#14-附录--appendix)

---

## 1. 产品概述 / Product Overview

### 1.1 背景 / Background

企业知识天然是分散的：制度写在 Confluence，合同存在 SharePoint，会议纪要在 Notion，历史资料躺在共享盘，业务事实在数据库，问题处理经验在工单系统里。员工要回答一个问题，往往需要跨 3–5 个系统检索，或者直接去问某个"活字典"同事。

Enterprise knowledge is inherently fragmented across wikis, document stores, shared drives, business databases and ticketing systems. Answering a single question typically requires searching 3–5 systems, or interrupting a subject-matter expert (SME).

通用大模型（ChatGPT、通义等）无法解决这个问题，原因有三：

Generic LLMs do not solve this, for three reasons:

1. **不知道 / No knowledge** — 模型对企业私有知识一无所知。
2. **不承认 / No honesty** — 模型不会说"我不知道"，而是生成听起来合理但完全错误的内容（幻觉 / hallucination）。
3. **不可用 / No governance** — 数据出企业、无权限控制、无审计留痕，在受监管行业根本无法上线。

### 1.2 产品定位 / Positioning

> 把分散的企业知识，变成**可问、可信、可追溯**的私有 AI 专家。
> Turn fragmented enterprise knowledge into a **queryable, trustworthy, traceable** private AI expert.

**架构定位 / Architectural positioning：**

> 本系统不是一个面向员工的问答应用，而是**企业知识的统一访问层（unified knowledge access layer）**。它把分散在多个源系统中的知识，收敛为一组**带权限、带引用、可审计**的检索能力，并通过多种协议对外供给：对人通过 IM 与 Web，对应用通过 REST API，对 AI Agent 通过 MCP（Phase 3）。上层的人、应用与 Agent 因此不必各自实现权限继承、文档切分、混合检索与引用绑定 —— 这些横切关注点在本层收敛一次。
>
> *This is not an employee-facing Q&A application. It is a unified knowledge access layer: knowledge scattered across source systems is consolidated into permission-aware, citation-bearing, auditable retrieval capabilities, served to humans via IM and web, to applications via REST, and to AI agents via MCP. Cross-cutting concerns — ACL inheritance, chunking, retrieval, citation — are solved once, here.*

本产品是一套**私有化部署**的检索增强生成（RAG）平台，具备三项不可妥协的产品特征：

This product is a **self-hosted** Retrieval-Augmented Generation platform with three non-negotiable characteristics:

| 特征 / Characteristic | 含义 / Meaning |
| --- | --- |
| **可追溯 / Traceable** | 每条事实性陈述必须附带可点击的引用，链接到源文档的具体段落。无引用则不输出。 Every factual claim carries a clickable citation to the exact source passage. No citation, no answer. |
| **可管控 / Governed** | 权限继承自源系统，用户只能检索到他本来就有权限看的内容；所有提问与命中留痕可审计。 Permissions inherit from source systems; every query and every retrieved chunk is logged. |
| **可私有 / Private** | 支持私有云 / 混合云 / 信创环境部署，可做到零外部 API 依赖。 Deployable on private cloud, hybrid cloud, or domestic (信创) stacks with zero external API dependency. |

### 1.3 与通用方案的差异 / Differentiation

| 维度 / Dimension | 通用大模型 / Generic LLM | 传统企业搜索 / Enterprise Search | 本产品 / This Product |
| --- | --- | --- | --- |
| 私有知识 / Private knowledge | ❌ 不具备 | ✅ 具备 | ✅ 具备 |
| 直接给答案 / Direct answer | ✅ 但可能是幻觉 | ❌ 只给文档列表 | ✅ 且强制引用 |
| 可追溯 / Traceability | ❌ | ⚠️ 文档级 / doc-level | ✅ 段落级 / passage-level |
| 权限控制 / Permissions | ❌ | ✅ | ✅ 继承 + 字段级 |
| 数据不出企业 / Data residency | ❌ | ✅ | ✅ |
| 审计 / Audit | ❌ | ⚠️ 部分 | ✅ 全量 |

---

## 2. 目标与非目标 / Goals & Non-Goals

### 2.1 产品目标 / Product Goals

| 编号 / ID | 目标 / Goal | 衡量方式 / Measure |
| --- | --- | --- |
| G-1 | 让员工用自然语言一次性获得跨源答案 | 跨源问题一次性解决率 ≥ 70% / Cross-source one-shot resolution ≥ 70% |
| G-2 | 让答案可被验证，杜绝不可核查的输出 | 引用覆盖率 100% / Citation coverage 100% |
| G-3 | 显著降低对专家的打扰 | 专家咨询量下降 ≥ 50%（90 天对比）/ SME consultation −50% over 90 days |
| G-4 | 缩短新人上手时间 | 新员工独立上岗周期下降 ≥ 50% / Onboarding time −50% |
| G-5 | 满足受监管行业的上线门槛 | 支撑客户系统通过等保三级测评与密评；控制项自评清单 100% 有明确责任方 |
| G-6 | 合规能力可按辖区与行业按需装配 | 支持至少 4 套合规基线包，切换后配置核查 0 项不达标 |

### 2.2 非目标 / Non-Goals（v1.0 不做 / Out of scope for v1.0）

| 编号 / ID | 非目标 / Non-Goal | 原因 / Rationale |
| --- | --- | --- |
| NG-1 | 训练或微调基座模型 / Base model training or fine-tuning | v1.0 采用检索增强而非参数注入；微调列入 v2.0 评估 |
| NG-2 | 通用闲聊与开放域问答 / Open-domain chit-chat | 产品定位是企业知识专家，超出知识库范围应明确拒答 |
| NG-3 | 反向写入源系统 / Write-back to source systems | 只读接入，避免污染权威数据源；系统不持有任何可执行业务动作的凭证 |
| NG-4 | 自动执行业务动作 / Agentic task execution | **被 Agent 调用 ≠ 成为 Agent**。系统保持只读、被动；行动由调用方 Agent 完成，责任亦由其承担 |
| NG-5 | 替代源系统成为知识的权威存储 / Replacing systems of record | 本产品是索引层，不是存储层 |
| NG-6 | 多模态生成（图/视频输出）/ Multimodal generation | v1.0 仅做多模态**理解**（OCR、表格、图片文字） |
| NG-7 | **不作为编排者 / Not an orchestrator** | 不驱动跨系统流程、不调用业务系统。在 Agent 生态中仅作为**被调用者（callee）**提供知识检索能力。详见《架构定位与 Agent 接入设计》第 2 章 |
| NG-9 | **不做对客产品档次 / No customer-facing tier** | 当前仅交付**企业内部档（PF-1）** —— 员工问、员工看。对客档（PF-2）作为**独立产品**后续分支，当前只保留四项低成本预留（受众标签、证据快照、服务无状态、组件选型约束），**不为其提前投入多活容灾、延迟优化、计费或对客前端**。见架构文档 §7.6 |
| NG-8 | **v1–v2 不建实体/本体层 / No entity or ontology layer** | 不做实体解析、不做图存储、不做本体 schema 设计。**但当前设计不得关闭通往该能力的门** —— 可扩展性预留见该文档第 7 章，其中 FR-02-13 与 FR-12-22 为 Phase 1 必须落地项 |

---

## 3. 目标用户与角色 / Users & Personas

| 角色 / Persona | 描述 / Description | 核心诉求 / Core Need | 主要功能 / Primary Features |
| --- | --- | --- | --- |
| **P-1 普通员工 / Employee** | 一线业务、销售、客服、新人 | 快速拿到准确答案，不想读 20 页文档 | 问答、引用查看、追问 |
| **P-2 领域专家 / SME** | 被反复打扰的资深员工 | 让系统替我回答重复问题；纠正错误答案 | 反馈纠错、答案审核、精选问答 |
| **P-3 知识管理员 / Knowledge Admin** | 知识运营、内容治理 | 管理知识源、发现知识盲区与冲突 | 知识源管理、盲区报告、冲突检测 |
| **P-4 系统管理员 / IT Admin** | 企业 IT / 运维 | 部署、接入、权限同步、稳定运行 | 连接器配置、权限映射、监控告警 |
| **P-5 安全与合规官 / Security & Compliance** | 风控、法务、审计 | 数据不外泄、行为可审计 | 审计日志、敏感词管控、数据边界报告 |
| **P-6 实施顾问 / Delivery Consultant** | 乙方交付人员 | 快速完成 POC 与调优 | 检索诊断面板、评测集、参数调优 |

---

## 4. 核心场景 / Key Use Cases

### 4.1 场景清单 / Scenario List

| 编号 / ID | 场景 / Scenario | 主要角色 / Persona | 目标指标 / Target | 优先级 / Priority |
| --- | --- | --- | --- | --- |
| S-01 | 新员工入职问答 / Employee onboarding | P-1 | 上岗周期 −60% | P0 |
| S-02 | IT 与流程自助 / IT & process self-service | P-1, P-4 | IT 工单量 −55% | P0 |
| S-03 | 销售知识助手 / Sales knowledge assistant | P-1 | 首次响应时间 −70% | P1 |
| S-04 | 客服一线支撑 / Tier-1 customer support | P-1 | 一次解决率 +40% | P1 |
| S-05 | 法务合规查询 / Legal & compliance lookup | P-2, P-5 | 审查时长 −50% | P1 |

### 4.2 场景详述 / Scenario Detail

#### S-01 新员工入职问答 / Employee Onboarding

**用户故事 / User Story**
作为一名入职第 3 天的新员工，我想直接提问"我们的报销标准是什么、超标了怎么办"，而不用翻遍 HR 手册和财务制度，这样我就能立刻开始工作而不打扰同事。

*As a new hire on day 3, I want to ask "what is our expense policy and what happens if I exceed it" instead of reading through the HR handbook and finance policy, so I can start working without interrupting colleagues.*

**流程 / Flow**
1. 员工在企业微信中向知识助手提问。
2. 系统跨 HR 手册、财务制度、历史工单三源检索。
3. 返回结构化答案 + 3 条引用（制度文件第 4.2 条、财务 FAQ、类似工单）。
4. 员工点击引用查看原文，确认标准。
5. 追问"那国际差旅呢"，系统保持上下文继续回答。

**验收标准 / Acceptance Criteria**
- 常见入职问题（预置 100 题评测集）一次性正确率 ≥ 85%
- 每条答案至少 1 条可点击引用
- 支持连续 5 轮上下文追问

#### S-02 IT 与流程自助 / IT & Process Self-Service

**用户故事 / User Story**
作为员工，我想在提工单之前先问助手"VPN 连不上怎么办"，直接拿到分步操作指引，这样简单问题不必等 IT 排期。

**验收标准 / Acceptance Criteria**
- 与工单系统打通，助手未解决时可一键转工单并带上会话上下文
- Top-50 高频 IT 问题的自助解决率 ≥ 60%
- 分步操作类答案必须保持源文档的步骤顺序与编号

#### S-03 销售知识助手 / Sales Knowledge Assistant

**用户故事 / User Story**
作为销售，我在客户现场被问到"你们和竞品 X 在数据合规上的差异"，我想 30 秒内拿到带出处的对比说法，这样我不必说"我回去确认一下"。

**验收标准 / Acceptance Criteria**
- 支持产品资料、竞品分析、报价规则、成功案例四类知识源
- 答案标注内容时效（如"此报价规则更新于 2026-06"）
- 移动端可用，响应 P95 ≤ 4.0s

#### S-04 客服一线支撑 / Tier-1 Customer Support

**用户故事 / User Story**
作为一线客服，我想在与客户通话时实时获得答案建议与话术，这样我不必转接二线。

**验收标准 / Acceptance Criteria**
- 提供坐席侧边栏模式，可接收当前会话上下文
- 答案区分「可直接对客」与「仅内部参考」两类知识
- 低置信度时明确提示转人工，不得猜测

#### S-05 法务合规查询 / Legal & Compliance Lookup

**用户故事 / User Story**
作为法务，我想检索"我们签过的合同中含有仲裁条款的有哪些、条款措辞如何"，并要求每条结论都能定位到合同原文页码。

**验收标准 / Acceptance Criteria**
- 引用必须精确到文件名 + 页码/条款号
- 支持按合同类型、签署时间、主体等元数据过滤
- 无充分依据时必须显式回答"未在知识库中找到依据"，禁止推测

---

## 5. 产品架构总览 / Product Architecture Overview

### 5.1 五层架构 / Five-Layer Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  L5 应用层 / Application                                      │
│  Web 门户 · 企业微信 · 钉钉 · Slack · 开放 API · 坐席侧边栏      │
├──────────────────────────────────────────────────────────────┤
│  L4 生成层 / Generation                                       │
│  LLM 推理 · 强制引用 · 置信度评分 · Self-RAG 校验 · 幻觉过滤     │
├──────────────────────────────────────────────────────────────┤
│  L3 检索层 / Retrieval                                        │
│  向量库 · BM25 全文 · 混合召回 · 元数据过滤 · Cross-Encoder 重排 │
├──────────────────────────────────────────────────────────────┤
│  L2 处理层 / Processing                                       │
│  OCR · 版面还原 · 语义切分 · 多语言 Embedding · 双索引构建       │
├──────────────────────────────────────────────────────────────┤
│  L1 接入层 / Ingestion                                        │
│  Confluence · SharePoint · Notion · 共享盘 · 业务库 · 工单系统   │
│  实时同步 · Webhook · 增量抓取 · ACL 同步                       │
└──────────────────────────────────────────────────────────────┘
        ↕ 贯穿全层 / Cross-cutting: 权限 ACL · 审计 Audit · 监控 Observability
```

### 5.2 五阶段处理流水线 / Five-Stage Pipeline

| 阶段 / Stage | 输入 / Input | 处理 / Processing | 输出 / Output |
| --- | --- | --- | --- |
| ① 接入 Ingestion | 源系统 | API/Webhook 拉取、增量识别、ACL 同步 | 原始文档 + 元数据 + 权限标签 |
| ② 解析切分 Parsing & Chunking | 原始文档 | OCR、版面还原、表格抽取、按语义边界切分 | Chunk 集合 |
| ③ 向量化索引 Embedding & Indexing | Chunk | 多语言 Embedding、倒排索引构建 | 向量索引 + 全文索引 |
| ④ 检索重排 Retrieval & Rerank | 用户问题 | 查询改写、并行召回、元数据过滤、Cross-Encoder 重排 | Top-K 证据 |
| ⑤ 生成引用 Generation & Citation | 证据 + 问题 | 受约束生成、引用绑定、Self-RAG 校验、置信度打分 | 带引用的答案 |

---

## 6. 功能需求 / Functional Requirements

### 需求约定 / Conventions

- **优先级 / Priority**：`P0` = v1.0 必须交付；`P1` = v1.0 期望交付；`P2` = 后续版本。
- **编号规则 / ID scheme**：`FR-<模块号>-<序号>`，例如 `FR-01-03`。
- **保留编号 / Reserved IDs**：`FR-01-17`、`FR-03-11`、`FR-04-15`、`FR-05-18`、`FR-05-19`、`FR-06-18`~`FR-06-27`、`FR-07-11`~`FR-07-13`、`FR-13-13`、`FR-13-14` 已被《架构定位与 Agent 接入设计》的 Phase 3 提案占用，**本 PRD 中暂缺，不得挪作他用**。本行是保留编号的**唯一权威清单**；`AGENTS.md` 第三节的列举以「等」结尾，是指针不是清单，两者不一致时以本行为准。
- 每个模块含：模块目标、功能点表、验收标准。

---

### FR-01 数据接入与同步 / Data Ingestion & Sync

**模块目标 / Objective**：让企业已有的知识源在不迁移、不改造的前提下接入平台，并保持持续同步。
*Connect existing knowledge sources without migration or modification, and keep them continuously in sync.*

| 编号 / ID | 功能点 / Function | 描述 / Description | 优先级 |
| --- | --- | --- | --- |
| FR-01-01 | Confluence 连接器 | 通过 Confluence API 接入空间/页面/附件，含页面层级与标签 | P0 |
| FR-01-02 | SharePoint / OneDrive 连接器 | 通过 Microsoft Graph API 接入站点、文档库、文件 | P0 |
| FR-01-03 | 共享盘连接器 | 支持 SMB/NFS/S3/FTP 路径挂载，递归扫描 PDF/Word/Excel/PPT | P0 |
| FR-01-04 | Notion 连接器 | 通过 Notion API 接入 Teamspace、Database、Page | P1 |
| FR-01-05 | 业务数据库连接器 | 支持 MySQL/PostgreSQL，按配置的 SQL 视图周期性抽取，字段可标记为敏感 | P1 |
| FR-01-06 | 工单系统连接器 | 接入工单标题、描述、解决方案字段，仅索引已关闭工单 | P1 |
| FR-01-07 | 手动上传 | 管理员可直接上传文件与文件夹，作为兜底接入方式 | P0 |
| FR-01-08 | 增量同步 | 基于源系统的修改时间/版本号识别增量，避免全量重跑 | P0 |
| FR-01-09 | Webhook 实时同步 | 源系统支持 Webhook 时，文档变更后触发实时更新 | P1 |
| FR-01-10 | 同步调度 | 支持按连接器配置同步频率（实时 / 15min / 小时 / 天） | P0 |
| FR-01-11 | 删除传播 | 源文档被删除或归档时，索引与检索结果须同步失效 | P0 |
| FR-01-12 | ACL 同步 | 拉取文档的源系统权限（用户/组/角色），随文档一并入库；**须含显式拒绝规则与继承状态**，见 FR-01-20 | P0 |
| FR-01-13 | 接入范围控制 | 支持按空间/目录/标签设置白名单与黑名单，排除私人与草稿内容 | P0 |
| FR-01-14 | 同步状态看板 | 展示每个连接器的最近同步时间、成功/失败/跳过文档数、错误明细 | P0 |
| FR-01-15 | 失败重试与告警 | 同步失败自动重试（指数退避），连续失败触发告警 | P1 |
| FR-01-16 | 自定义连接器 SDK | 提供标准接口规范，客户可自行开发私有系统连接器 | P2 |
| FR-01-19 | **连接器接口标准契约** | 每个连接器须实现六项：①全量拉取 ②增量识别 ③**删除检测** ④ACL 读取与映射 ⑤元数据抽取 ⑥断点续传与幂等。并须**声明式描述自身能力**（哪几项该源做不到），核心据此选择同步策略。**接口应由最复杂的连接器定义** —— 建议首个实现选 Confluence 或 SharePoint 这类有完整 ACL 与增量语义的源，而非本地文件夹，否则接口会被定义得过窄，后续每加一个源都要改接口与已有实现 | P0 |
| FR-01-20 | **ACL 拒绝规则读取** | 连接器输出的 ACL 必须能表达**显式拒绝**，而非仅"允许名单"。共享盘（NTFS）有显式 Deny ACE 且优先于 Allow；SharePoint 通过断开继承实现差异化权限 —— 表达不了这些，后果不是功能缺失而是**越权** | P0 |
| FR-01-18 | **受众标签 `audience`** | 知识源与文档标注 `internal_only` / `customer_safe`，**默认 `internal_only`**，可按目录细化。将来分支对客产品档时无需回溯分类存量文档 —— 回溯分类既贵且危险（错标一次即对外泄露内部制度） | P0 |

**验收标准 / Acceptance Criteria**
- 单连接器可在 2 小时内完成配置并跑通首次全量同步（10 万文档规模内）。
- 源文档更新后，实时模式下 ≤ 5 分钟、定时模式下在下一周期内反映到检索结果。
- 源文档删除后 ≤ 15 分钟内不再出现在任何用户的检索结果中。
- 同步任务中断后可断点续传，不产生重复索引。

---

### FR-02 文档解析与切分 / Parsing & Chunking

**模块目标 / Objective**：把各种格式的原始文档，转换成保留结构与语义完整性的可检索单元。
*Convert heterogeneous raw documents into retrievable units that preserve structure and semantic integrity.*

| 编号 / ID | 功能点 / Function | 描述 / Description | 优先级 |
| --- | --- | --- | --- |
| FR-02-01 | 多格式解析 | 支持 PDF、Word、Excel、PPT、Markdown、HTML、TXT、CSV、图片 | P0 |
| FR-02-02 | 版面还原 | 识别标题层级、正文、页眉页脚、脚注、多栏排版，剔除噪声 | P0 |
| FR-02-03 | OCR | 扫描件与图片文字识别，支持中英混排（PaddleOCR / Unstructured 类方案） | P0 |
| FR-02-04 | 表格抽取 | 表格转为结构化表示，保留表头，不被切分打散 | P0 |
| FR-02-05 | 语义切分 | 按语义边界（章节、段落、条款）切分，而非固定字数硬切 | P0 |
| FR-02-06 | 切分参数可配 | 目标 chunk 长度、重叠比例、最小/最大长度可按知识源配置 | P0 |
| FR-02-07 | 上下文注入 | 每个 chunk 附带文档标题与所属章节路径，提升独立可读性 | P0 |
| FR-02-08 | 元数据抽取 | 抽取作者、创建/更新时间、部门、文档类型、版本、有效期 | P0 |
| FR-02-09 | 位置锚点 | 每个 chunk 记录页码 / 段落序号 / 条款号，供引用精确定位 | P0 |
| FR-02-13 | **字符级跨度锚点** | 每个 chunk 额外记录相对源文档的 `char_start` / `char_end`。**必须在 Phase 1 落地**：将来任何基于文本跨度的标注（实体、敏感度、审核状态）都依赖它，缺失则须重新解析全部文档 | P0 |
| FR-02-10 | 去重 | 识别跨源重复文档与近似 chunk，合并或标记主副本 | P1 |
| FR-02-11 | 图片理解 | 对流程图、截图生成文字描述并索引 | P2 |
| FR-02-12 | 解析质量报告 | 输出解析失败、疑似乱码、OCR 低置信文档清单供人工处理 | P1 |

**验收标准 / Acceptance Criteria**
- 中文 PDF 正文抽取准确率 ≥ 95%（以人工抽检 100 份文档为准）。
- 表格在检索命中时结构完整，表头与数据行不分离。
- 每个 chunk 均可反查到源文件与页码/条款号，缺失率为 0。
- 解析失败的文档必须出现在质量报告中，不得静默丢弃。

---

### FR-03 向量化与索引 / Embedding & Indexing

**模块目标 / Objective**：为混合检索构建向量与全文双索引，并支持在不停服的前提下更新与重建。

| 编号 / ID | 功能点 / Function | 描述 / Description | 优先级 |
| --- | --- | --- | --- |
| FR-03-01 | 多语言 Embedding | 采用支持中英混合的 Embedding 模型（如 BGE-M3 级别） | P0 |
| FR-03-02 | 向量索引 | 基于向量数据库（Milvus / Qdrant 级别）建立 ANN 索引 | P0 |
| FR-03-03 | 全文索引 | 建立 BM25 倒排索引，含中文分词与同义词词典 | P0 |
| FR-03-04 | 元数据索引 | 元数据字段可作为过滤条件参与检索 | P0 |
| FR-03-05 | 增量索引 | 仅对变更 chunk 重新向量化，避免全量重建 | P0 |
| FR-03-06 | 索引热更新 | 索引更新过程中检索服务不中断 | P0 |
| FR-03-07 | 模型可替换 | Embedding 模型可配置替换，并支持双索引灰度切换 | P1 |
| FR-03-08 | 索引重建任务 | 支持全量重建，带进度、可暂停、可回滚；**重建前必须加载抑制清单（FR-12-17）为前置步骤**，否则重建会把已依法删除的内容全部捞回 | P1 |
| FR-03-09 | 多租户隔离 | 不同租户/业务域的索引物理或逻辑隔离。**v1.0 按单租户单实例交付（决策 Q-1），但全部表结构必须携带 `tenant_id`（恒为默认值）** —— 后补需改全表、回填数据并逐条审计租户过滤，属安全级返工 | P1 |
| FR-03-10 | 索引容量监控 | 监控向量数量、磁盘占用、索引延迟并告警 | P1 |
| FR-03-12 | **全表携带 `tenant_id`** | v1 按单租户交付，但**所有表结构必须带 `tenant_id` 字段**（恒为默认值）。后补需改全表、回填全部数据并逐条审计每个查询是否漏了租户过滤 —— 漏一条即跨租户泄露，属**安全级返工** | P0 |

**验收标准 / Acceptance Criteria**
- 100 万 chunk 规模下，向量检索单次耗时 ≤ 200ms（P95）。**该指标仅含向量 ANN 查询，不含 BM25、融合、权限过滤与重排**；四者合计的检索总耗时口径见 NFR-P-3（≤ 500ms）。二者为嵌套关系而非并列。
- 更换 Embedding 模型后可通过灰度对比评测集验证效果再切换。
- 增量索引对在线检索的可用性影响为 0。

---

### FR-04 混合检索与重排 / Hybrid Retrieval & Reranking

**模块目标 / Objective**：在准确率与召回率之间取得平衡，并让检索过程对实施人员**可解释、可调优**。
*Balance precision and recall, and make retrieval explainable and tunable for delivery engineers.*

| 编号 / ID | 功能点 / Function | 描述 / Description | 优先级 |
| --- | --- | --- | --- |
| FR-04-01 | 查询理解 | 对用户问题做意图识别、指代消解、时间归一化 | P0 |
| FR-04-02 | 查询改写 | 结合会话上下文将追问改写为独立完整查询 | P0 |
| FR-04-03 | 查询扩展 | 同义词/缩写/企业黑话扩展（可配置企业术语表） | P1 |
| FR-04-04 | 并行混合召回 | BM25 全文与向量检索并行执行 | P0 |
| FR-04-05 | 结果融合 | 采用 RRF 或加权融合合并两路结果，权重可配。**默认取向须偏向 BM25 一侧**，不得沿用「向量为主、关键词为辅」的行业惯例默认值（如 α=0.75 偏稠密）——在企业内部语料上稀疏腿通常是更强的一条腿（证据 附录 E · EV-1）。实际权重**必须在客户自有评测集上标定后写入交付方案**，默认值只是安全起点，不是结论 | P0 |
| FR-04-06 | 元数据过滤 | 支持按部门、时间范围、文档类型、有效期过滤 | P0 |
| FR-04-07 | 权限过滤 | 在召回阶段即按用户权限过滤，禁止先召回后遮蔽 | P0 |
| FR-04-08 | Cross-Encoder 重排 | 对融合结果做精排，输出 Top-K 证据。**能力为 P0，默认启用状态为"按证据决定"**：须先由 FR-04-17 测出 recall@10 与 recall@50 的差值，差值显著才启用。重排器的规模与质量决定收益正负 —— 弱重排器会把已召回的正确证据往下压，造成净损失。**不得以"业界标准做法"为由默认开启** | P0 |
| FR-04-17 | **双深度召回基线** | 在客户自有评测集上同时测量 recall@10 与 recall@50 并记录。二者差值是重排决策的唯一依据：差值大说明"召回到了但排不上去"，重排有收益；差值小说明瓶颈在召回本身，重排无用甚至有害。该基线同时作为知识库漂移的监测基准，须纳入 FR-10-03 回归对比 | P0 |
| FR-04-09 | 相关性阈值 | 低于阈值的结果不进入生成，触发"未找到依据"路径 | P0 |
| FR-04-10 | 时效性加权 | 同等相关度下，更新时间更近的内容排序更高 | P1 |
| FR-04-11 | 权威度加权 | 管理员可标记权威文档（如现行制度）提升其排序 | P1 |
| FR-04-12 | 检索诊断面板 | 可视化展示查询改写结果、两路召回明细与得分、重排前后排序变化 | P0 |
| FR-04-13 | 参数在线调优 | 融合权重、Top-K、阈值可在后台调整并即时生效 | P1 |
| FR-04-16 | **按受众过滤** | 检索时按 `audience` 标签过滤，规则由部署档案（PF-1 内部档 / PF-2 对客档）配置，不得硬编码 | P0 |
| FR-04-14a | **检索结果缓存（基础）** | 缓存「问题哈希 → 已召回 chunk_id 列表」，**不按用户权限分区**；命中后仍走 FR-06-03 实时权限二次校验与 FR-06-28 拒绝优先求值。**必须支持按 chunk_id 定点失效**（chunk_id → cache_key 反向映射，量级 = chunk 数 × 平均复用次数，与用户数无关）。**优先级 P0** —— 删除链路（位置 4）将其作为硬前置 | P0 |
| FR-04-14b | 按权限分区的结果缓存 | 缓存已过滤后的完整结果集，省去二次校验开销。反向映射规模随权限组数增长，**须先做容量评估再决定是否实现** | P2 |

**验收标准 / Acceptance Criteria**
- 在客户提供的 ≥ 200 题评测集上，Top-3 命中率达到约定目标（页面自述基准 92.4%，实际以合同约定为准）。
- **重排启用与否须有数据支撑（阻断性）**：验收报告须同时给出 recall@10 / recall@50 基线、开启与关闭重排两种配置下的指标对照。**若开启重排无可测量提升，则必须以关闭状态交付**，不得「先开着再说」。
- 融合权重的最终取值及其标定过程须写入交付方案，不得只写"可配"。
- 检索诊断面板可对任意一次查询回放完整链路。
- 无权限内容在任何配置下均不出现在召回结果中（安全红线 / hard security boundary）。

---

### FR-05 生成与引用 / Generation & Citation

**模块目标 / Objective**：生成的每一句事实性陈述都必须可核查；宁可拒答，不可编造。
*Every factual statement must be verifiable. Refusing to answer is always preferable to fabricating one.*

| 编号 / ID | 功能点 / Function | 描述 / Description | 优先级 |
| --- | --- | --- | --- |
| FR-05-01 | 受约束生成 | 严格基于召回证据生成，禁止引入模型内部知识 | P0 |
| FR-05-02 | 强制引用 | 每个事实性句子绑定至少一个证据 chunk，无引用不输出 | P0 |
| FR-05-03 | 引用可点击 | 点击引用展开原文片段，并可跳转源系统原文位置 | P0 |
| FR-05-04 | 引用粒度 | 引用精确到页码 / 条款号 / 段落，而非仅文档名 | P0 |
| FR-05-05a | **引用绑定校验**（轻量，P0） | 逐句校验该句是否绑定到证据 chunk，绑不上即删除该句。**逐句进行，可与流式输出并行，不阻塞首字**。**约束**：将来若引入任何多步/迭代检索，本校验必须**每步执行**，不得只在最终答案上做一次 —— 衰减发生在过程中，终点校验抓不到（附录 E · EV-2） | P0 |
| FR-05-05b | Self-RAG 语义蕴含校验（重型） | 用 LLM-as-judge 或 NLI 模型判断陈述是否被证据**语义支撑**（而非仅绑定）。**与流式输出存在结构性冲突**（见 §7.1 时延说明）：v1.0 不实现，降为 P1，实现路径列入 §13 待定。**另有外部证据支持推迟**：引用的事实准确性会随检索/工具调用轮次加深而显著衰减（约 −42%），而链接有效性与主题相关性几乎不变 —— 即引用"看起来仍然对"但已经不成立（附录 E · EV-2）。这同时是 NG-7（不作为编排者）的论据 | P1 |
| FR-05-06 | 置信度评分 | 输出答案置信度，低置信答案显式标注并给出理由 | P0 |
| FR-05-07 | 无依据拒答 | 证据不足时明确回答"知识库中未找到依据"，并给出最接近的相关文档 | P0 |
| FR-05-08 | 冲突提示 | 多份文档结论冲突时并列呈现并标注各自出处与时间，不擅自裁决 | P0 |
| FR-05-09 | 时效提示 | 引用文档超过配置期限（如 12 个月未更新）时提示内容可能过期 | P1 |
| FR-05-10 | 多轮会话 | 支持上下文追问，保留至少 10 轮会话记忆 | P0 |
| FR-05-11 | 答案结构化 | 根据问题类型输出恰当结构（步骤 / 对比表 / 要点列表 / 直答） | P1 |
| FR-05-12 | 流式输出 | 首字输出 ≤ 1.5s，逐字流式返回 | P0 |
| FR-05-13 | 追问建议 | 结合已召回内容推荐 3 条相关追问 | P2 |
| FR-05-14 | 敏感内容管控 | 命中敏感词/敏感字段时按策略脱敏或拒答，并记录 | P0 |
| FR-05-15 | 模型可插拔 | 支持接入自部署开源模型（DeepSeek / Qwen / Llama 级别）或商用 API，可按场景路由。**商用 API 默认数据出域，与 FR-06-08 零外部依赖模式互斥**：零外部依赖模式下商用 API 通道强制禁用且不可绕过；启用商用 API 须为客户显式配置操作、须接受数据出境评估（FR-12-13）、须入审计 | P0 |
| FR-05-16 | 答案反馈 | 用户可对答案点赞/点踩并填写原因，进入优化闭环 | P0 |
| FR-05-17 | 精选答案 | 专家可将审核过的答案固化为标准答案，命中同类问题时优先返回 | P1 |
| FR-05-20 | **证据快照** | `Citation` 记录 chunk 的**内容哈希 + 版本号**（版本号对应 `chunks.version`；字符偏移量的基准与可验证条件见 TD-01 §2·5），而非仅指向当前 chunk，使历史答案"当时依据了什么"可追溯。**此项不可事后补做** —— 未记录的历史数据不会因将来加字段而变得可追溯 | P0 |

**验收标准 / Acceptance Criteria**
- **引用覆盖率 100%**：抽检 200 条答案，事实性陈述无引用的比例为 0。
- 幻觉率（有引用但引用不支撑该陈述）≤ 2%，以人工评审为准。
- 证据不足场景下拒答率 ≥ 95%，不得强行作答。
- 端到端响应 P50 ≤ 2.5s、P95 ≤ 4.0s（不含超大文档首次解析）。

---

### FR-06 身份、权限与访问控制 / Identity, Permissions & Access Control

**模块目标 / Objective**：让"谁能看到什么"与源系统完全一致，且全过程可审计。
*Ensure "who can see what" exactly mirrors the source systems, with full auditability.*

> 密码与数据保护见 FR-11，隐私与数据主体权利见 FR-12，合规包与审计支撑见 FR-13。
> Cryptography → FR-11; privacy & data subject rights → FR-12; compliance pack → FR-13.

| 编号 / ID | 功能点 / Function | 描述 / Description | 优先级 |
| --- | --- | --- | --- |
| FR-06-01 | 身份对接 | 支持 LDAP / AD / OAuth2 / OIDC / 企业微信 / 钉钉 单点登录 | P0 |
| FR-06-02 | ACL 继承 | 文档权限继承自源系统，源端变更后同步更新 | P0 |
| FR-06-03 | 权限实时校验 | 检索与展示时二次校验用户当前权限，防止同步延迟导致越权 | P0 |
| FR-06-29 | **离职/转岗失效** | 支持从 HR 或 IdP 接收离职/转岗事件并**立即**失效该账号的会话、令牌与缓存；不依赖下一次 ACL 周期同步。**若源系统本身有延迟（如 HR 月结后才同步），须支持管理员手动即时停用**，并在管理后台暴露"最近一次 HR 同步时间"以便发现滞后 | P0 |
| FR-06-04 | 字段级权限 | 数据库来源内容支持字段级授权（如薪资字段仅 HR 可见） | P1 |
| FR-06-05 | 角色管理 | 内置六类角色，与 §3 的 P-1~P-6 一一对应：员工(P-1) / 专家(P-2) / 知识管理员(P-3) / 系统管理员(P-4) / 安全审计员(P-5) / **实施顾问(P-6)**。实施顾问为**限时角色**：POC 期可授予诊断与配置权限，**上线后自动降级为只读诊断**，降级触发条件与流程须在交付方案中写明 | P0 |
| FR-06-06 | 传输与存储加密 | 全链路与静态加密，算法与密码模块要求见 FR-11 | P0 |
| FR-06-07 | 最小权限 | 系统内部服务账号遵循最小权限原则，禁用默认口令与共享账号 | P0 |
| FR-06-08 | 数据边界 | 支持零外部 API 依赖部署模式，可出具数据不出域说明 | P0 |
| FR-06-09 | 敏感信息识别 | 入库时识别身份证、银行卡、手机号等 PII 并按策略脱敏（分级规则见 FR-12-02） | P0 |
| FR-06-10 | 会话隔离 | 用户会话与个人上传内容互不可见 | P0 |
| FR-06-11 | 双因素认证 | 管理员与审计员账号强制 MFA。**注意**：GB/T 22239-2019 三级身份鉴别 d) 是针对"用户"而非仅管理员，且要求两种以上鉴别技术中**至少一种以密码技术实现**（如数字证书/SM2）——仅短信或 TOTP 不满足。普通用户的双因素范围须在定级测评前与客户确认 | P0 |
| FR-06-12 | 信创适配 | 支持国产化 CPU / OS / 数据库 / 中间件 / 模型的部署组合 | P1 |
| FR-06-13 | 越权检测 | 系统自检并告警任何越权召回，目标为零发生 | P0 |
| FR-06-14 | 口令与登录策略 | 口令复杂度、有效期、历史重用限制、连续失败锁定可配置 | P0 |
| FR-06-15 | 会话安全 | 会话超时自动登出、并发会话数限制、登录地异常提示 | P0 |
| FR-06-16 | 特权操作双人复核 | 高危操作（批量下架、导出日志、变更合规基线）支持双人审批 | P1 |
| FR-06-17 | 剩余信息保护 | 存储空间与内存释放前清除敏感残留（等保三级要求） | P1 |
| FR-06-28 | **拒绝优先的权限求值** | 求值顺序固定为：①收集适用于该用户的全部 AclEntry（直接的 + 经组/角色继承的）②**任一 deny 命中即拒绝**（不论其 scope_level 高低）③否则至少一条 allow 命中则允许 ④否则**默认拒绝**。目录级 deny 优先于文件级 allow；断开继承的对象不再适用上级条目 | P0 |

**验收标准 / Acceptance Criteria**
- 权限渗透测试：构造 ≥ 50 组越权用例，越权命中数必须为 0。**其中必须包含拒绝规则用例**：目录级 deny + 文件级 allow、断开继承后父级权限失效、用户同时属于被允许组与被拒绝组 —— 三类场景下 deny 均须生效。
- 源系统权限回收后 ≤ 5 分钟内，用户不再能检索到相关内容。
- 可提供完整的数据流向说明，证明零外部依赖模式下无数据出域。
- 管理员账号在未完成 MFA 的情况下无法登录，且该策略不可被单人绕过。

---

### FR-07 用户端与接入渠道 / User Channels

**模块目标 / Objective**：让用户在他已有的工作界面里提问，而不是多打开一个系统。

| 编号 / ID | 功能点 / Function | 描述 / Description | 优先级 |
| --- | --- | --- | --- |
| FR-07-01 | Web 问答门户 | 独立 Web 应用，含问答、历史、收藏、引用查看 | P0 |
| FR-07-02 | 企业微信应用 | 以应用/机器人形式接入，支持单聊与群聊 @ | P0 |
| FR-07-03 | 钉钉应用 | 同上 | P1 |
| FR-07-04 | Slack 应用 | 同上，面向外企与出海团队 | P1 |
| FR-07-05 | 开放 API | 提供问答、检索、引用查询 REST API，含鉴权与限流 | P0 |
| FR-07-06 | 坐席侧边栏 | 可嵌入客服工作台的侧边栏组件，支持接收当前会话上下文 | P1 |
| FR-07-07 | 移动端适配 | Web 与 IM 端在移动设备上完整可用 | P0 |
| FR-07-08 | 历史与收藏 | 保存提问历史，可收藏与分享答案（分享须做权限校验） | P1 |
| FR-07-09 | 会话导出 | 导出问答记录为 Markdown / PDF | P2 |
| FR-07-10 | 多语言界面 | 界面与答案支持中英切换 | P1 |

**验收标准 / Acceptance Criteria**
- Web 与至少一个 IM 渠道在 v1.0 交付并达成同等功能。
- 开放 API 提供完整文档与示例，第三方系统可在 1 天内完成对接。
- 分享出去的答案对无权限用户不可见。

---

### FR-08 管理后台 / Admin Console

**模块目标 / Objective**：让知识管理员和 IT 管理员无需研发介入即可完成日常运营。

| 编号 / ID | 功能点 / Function | 描述 / Description | 优先级 |
| --- | --- | --- | --- |
| FR-08-01 | 知识源管理 | 增删改查连接器、配置同步策略与接入范围 | P0 |
| FR-08-02 | 文档管理 | 浏览已索引文档，查看解析结果与 chunk，支持单篇重跑 | P0 |
| FR-08-03 | 文档屏蔽 | 一键下架特定文档或目录，立即从检索中移除 | P0 |
| FR-08-04 | 术语表管理 | 维护企业专有名词、缩写、同义词 | P1 |
| FR-08-05 | 敏感词管理 | 维护敏感词与处理策略（脱敏 / 拒答 / 告警） | P0 |
| FR-08-06 | 精选问答管理 | 维护标准问答对，支持审核与失效时间 | P1 |
| FR-08-07 | 反馈处理 | 查看点踩记录，分配给专家处理，跟踪闭环 | P0 |
| FR-08-08 | 知识盲区报告 | 汇总高频无答案问题，指出知识库缺口 | P1 |
| FR-08-09 | 冲突检测报告 | 发现内容互相矛盾的文档并提示治理 | P2 |
| FR-08-10 | 用量分析看板 | 提问量、活跃用户、场景分布、解决率、专家咨询量对比 | P0 |
| FR-08-11 | 参数配置 | 检索与生成参数的可视化配置与灰度发布 | P1 |
| FR-08-12 | 系统监控 | 服务健康、队列积压、GPU/CPU 占用、响应耗时 | P0 |

**验收标准 / Acceptance Criteria**
- 知识管理员可在不接触命令行的情况下完成新增知识源到上线的全流程。
- 文档下架操作 ≤ 1 分钟内在所有渠道生效。
- 用量看板支持按时间、部门、场景三个维度下钻。

---

### FR-09 审计与日志 / Audit & Logging

**模块目标 / Objective**：任何一次提问、任何一条被读取的知识，事后都能查得到。

| 编号 / ID | 功能点 / Function | 描述 / Description | 优先级 |
| --- | --- | --- | --- |
| FR-09-01 | 提问日志 | 记录用户、时间、渠道；**问题原文字段受 FR-12-16 管辖 —— 默认配置下不落原文，仅存归一化哈希与匿名化特征（长度、语种、意图类别）**。客户显式开启留存后方记录原文，开启动作本身入审计。审计可追溯性由 query_id 链路保证，不依赖原文 | P0 |
| FR-09-02 | 检索日志 | 记录召回的 chunk ID、得分、是否进入生成 | P0 |
| FR-09-03 | 答案日志 | 记录生成答案、引用列表、置信度、使用的模型 | P0 |
| FR-09-04 | 管理操作日志 | 记录所有后台配置变更（操作人、前后值） | P0 |
| FR-09-05 | 权限事件日志 | 记录 ACL 同步、拒绝访问、越权告警 | P0 |
| FR-09-06 | 审计员视图 | 审计员角色可检索日志，但不可修改与删除 | P0 |
| FR-09-07 | 日志留存 | 留存周期按合规基线自动设定：通用/等保三级 ≥ 6 个月（《网络安全法》2025 年修正第 23 条下限；2016 年原文为第 21 条），金融基线按客户监管要求可配至 ≥ 5 年；到期归档不删除 | P0 |
| FR-09-08 | 日志导出 | 支持按条件导出 CSV / JSON，供外部审计 | P1 |
| FR-09-09 | 日志防篡改 | 日志写入后不可修改，可选哈希链校验 | P1 |
| FR-09-10 | SIEM 对接 | 支持 Syslog / Kafka 推送到企业安全平台 | P2 |

**验收标准 / Acceptance Criteria**
- 任意一次历史提问可完整回放：问题 → 召回 → 证据 → 答案 → 引用。
- 审计员账号无法修改任何日志记录。
- 日志本身受权限保护，普通管理员不可见他人提问内容。

---

### FR-10 评测与质量运营 / Evaluation & Quality Operations

**模块目标 / Objective**：把"效果好不好"从主观感受变成可测量、可回归的工程指标。
*Turn "does it work well" from subjective impression into a measurable, regression-testable engineering metric.*

| 编号 / ID | 功能点 / Function | 描述 / Description | 优先级 |
| --- | --- | --- | --- |
| FR-10-01 | 评测集管理 | 维护「问题—标准答案—期望证据」三元组评测集 | P0 |
| FR-10-02 | 自动评测 | 一键跑批，输出命中率、引用正确率、幻觉率、拒答率 | P0 |
| FR-10-03 | 回归对比 | 参数或模型变更前后的效果对比报告 | P0 |
| FR-10-04 | 线上抽检 | 按比例抽取线上答案送人工评审并记录评分 | P1 |
| FR-10-05 | 分场景报表 | 按业务场景（S-01~S-05）分别输出质量指标 | P1 |
| FR-10-06 | 坏例归因 | 对失败样例自动归类（召回失败 / 重排失败 / 生成失败 / 知识缺失） | P1 |
| FR-10-07 | 从反馈生成评测集 | 用户点踩样例可一键加入评测集；**入集前强制 PII 脱敏**（此路径会把真实问答对沉淀进长期保留、反复使用、可导出的数据集，是删除权的高风险泄漏点） | P1 |
| FR-10-08 | 评测集版本管理 | 评测集可版本化，保证不同时期结果可比 | P2 |

**验收标准 / Acceptance Criteria**
- 交付时须附评测报告，报告须写明评测集规模、构造方式与各项指标口径。
- 任何参数变更上线前必须有回归对比结果，指标不得回退超过约定阈值。
- 坏例归因能明确指出问题出在流水线的哪一阶段。

---

### FR-11 密码与数据保护 / Cryptography & Data Protection

**模块目标 / Objective**：让加密方案能通过**商用密码应用安全性评估（密评）**，而不只是"用了 AES-256"。
*Ensure the cryptographic design can pass China's commercial cryptography assessment (密评), not merely "we use AES-256".*

> **关键认知 / Key insight**：密评的**法定强制对象**是关键信息基础设施，以及其他"法律、行政法规和国家有关规定要求使用商用密码保护"的系统（《商用密码管理条例》第 38 条、《商用密码应用安全性评估管理办法》第 6 条）。**等保三级本身并不自动触发密评**——征求意见稿中"三级以上网络"的表述在 2023 年正式文本中已删除。但等保测评本身含密码应用测评项，且金融、政务等行业主管部门常另行要求密评。是否适用须按客户所属行业与 CII 认定结果**逐案判断**。
>
> 一旦适用，密评要求使用**国密算法 SM2/SM3/SM4** 及取得**商用密码产品认证证书**的密码模块（2020 年起型号证书制已改为商用密码产品认证）。仅用 AES-256 + TLS 1.3 无法通过密评。因此加密算法必须是**可切换配置项**，而非硬编码。
>
> *Mandatory 密评 attaches to critical information infrastructure and systems otherwise required by law to use commercial cryptography — Level-3 MLPS does not by itself trigger it, though MLPS testing includes cryptography controls and sector regulators (finance, government) often impose it separately. Applicability must be assessed case by case. Where it applies, SM-series algorithms and certified crypto modules are required; AES-256 + TLS 1.3 alone will not pass. Algorithm choice must therefore be configurable, not hard-coded.*

| 编号 / ID | 功能点 / Function | 描述 / Description | 优先级 |
| --- | --- | --- | --- |
| FR-11-01 | 国密算法支持 | 支持 SM2（非对称）、SM3（摘要）、SM4（对称）用于签名、完整性与加解密 | P0 |
| FR-11-02 | 算法套件可切换 | 国际算法套件（RSA/AES/SHA-2）与国密套件可按部署整体切换，业务代码无感 | P0 |
| FR-11-03 | 国密传输协议 | 支持 TLCP（GB/T 38636-2020 传输层密码协议）作为 TLS 1.3 的替代选项；SSL VPN 产品对接参见 GM/T 0024 | P0 |
| FR-11-04 | 密码模块对接 | 支持对接取得**商用密码产品认证证书**的密码机 / 加密卡 / 软件密码模块（HSM、KMS） | P0 |
| FR-11-05 | 密钥分层管理 | 根密钥、数据密钥分层；根密钥存于密码模块，禁止落盘明文 | P0 |
| FR-11-06 | 密钥轮换 | 支持密钥定期轮换与紧急轮换，轮换不中断服务、历史数据可解密 | P0 |
| FR-11-07 | 存储加密分级 | 原始文档、向量、日志、备份分别可配置加密强度与算法 | P0 |
| FR-11-08 | 完整性保护 | 重要数据与审计日志的传输存储完整性校验（HMAC-SM3 / HMAC-SHA256） | P0 |
| FR-11-09 | 证书管理 | 支持国密双证书体系（签名证书 + 加密证书）与证书到期告警 | P1 |
| FR-11-10 | 备份加密 | 备份数据加密存储，恢复需独立授权 | P0 |
| FR-11-11 | 密评支撑材料 | 输出密码应用方案说明、算法清单、密钥生命周期说明，供密评机构审阅 | P0 |
| FR-11-12 | 加密性能基线 | 提供国密与国际算法在同等负载下的性能对比数据，供客户容量规划 | P1 |

**验收标准 / Acceptance Criteria**
- 在纯国密模式下，全链路（传输、存储、签名、完整性）无任何国际算法残留，可由工具扫描证明。
- 算法套件切换后系统功能完整可用，且已有数据可正常解密访问。
- 可出具密码应用方案文档，其结构满足密评机构的受理要求。
- 根密钥在任何日志、配置文件、内存转储中均不以明文出现。

---

### FR-12 隐私与数据主体权利 / Privacy & Data Subject Rights

**模块目标 / Objective**：满足 PIPL / 数据安全法 / GDPR 对个人信息处理的要求，其中**删除权在 RAG 架构下是一条需要专门设计的链路**。
*Meet PIPL / DSL / GDPR obligations — where, in a RAG architecture, the right to erasure requires a purpose-built pipeline.*

> **关键认知 / Key insight**：删除一份源文档 ≠ 删除其中的个人信息。PII 一旦进入系统，会散落在**十个位置**：原文缓存、chunk、向量索引、检索缓存、会话历史、答案与引用记录、审计日志、**备份与归档、精选问答、评测集**。后三处最隐蔽——它们是产品自身功能（FR-05-17、FR-10-07）主动把内容复制出主链路造成的。若不设计贯穿式删除，"我们支持删除权"就是空话，且在监管检查时无法举证。
> Deleting a source document does not delete the personal information inside it. Once ingested, PII scatters across ten locations — raw cache, chunks, vector index, retrieval cache, session history, answers and citations, audit logs, backups, curated Q&A, and evaluation sets. The last three are the sneakiest: the product's own features copy content out of the main pipeline into them. Without an end-to-end erasure pipeline, "we support the right to erasure" is unprovable under inspection.
>
> 详细设计见《FR-12-04 贯穿式删除技术设计文档》。/ See the dedicated design document for FR-12-04.

| 编号 / ID | 功能点 / Function | 描述 / Description | 优先级 |
| --- | --- | --- | --- |
| FR-12-01 | 个人信息识别 | 入库与生成两端识别 PII（身份证、手机、银行卡、住址、生物特征等） | P0 |
| FR-12-02 | 数据分类分级 | 支持按 JR/T 0197（金融数据五级）、个人金融信息 C1/C2/C3、客户自定义规则打标 | P0 |
| FR-12-03 | 分级处置策略 | 不同等级数据对应不同的检索可见性、脱敏方式、留存期与导出限制 | P0 |
| FR-12-04 | **贯穿式删除** | 一次删除请求级联清除**十处**：原文缓存 → chunk → 向量索引（须强制 compaction，软删除不算）→ 检索缓存 → 会话历史 → 答案与引用 → 审计日志（加密擦除）→ 备份归档（加密擦除）→ 精选问答 → 评测集 | P0 |
| FR-12-05 | 删除证明 | 删除完成后生成可核验的删除报告（核查范围与方法、对象清单、时间、执行人、校验值），**必须含"未覆盖范围声明"**——声称 100% 彻底删除在核查发现残留时会反成不利证据。**每个位置须分别记录 `logical_completed_at`（逻辑删除）与 `physical_completed_at`（物理移除，如向量库 compaction 完成）；两者均完成方可标记 CERTIFIED**——向量库 compaction 异步且可能耗时数十分钟，证明早于物理完成即为不实陈述 | P0 |
| FR-12-06 | 删除时限保障 | 删除请求在约定时限内完成（默认 ≤ 15 个工作日，可配置） | P0 |
| FR-12-07 | 数据主体查询 | 支持查询"系统中与某主体相关的数据有哪些、来自哪些源" | P1 |
| FR-12-08 | 更正与限制处理 | 支持标记数据为"有争议/限制使用"，该数据不参与生成但保留 | P1 |
| FR-12-09 | 可携权导出 | 按数据主体导出其相关数据为结构化格式 | P2 |
| FR-12-10 | 处理活动记录 ROPA | 自动维护处理活动记录（目的、类别、接收方、留存期、跨境情况） | P1 |
| FR-12-11 | 影响评估支撑 | 输出 DPIA / 个人信息保护影响评估所需的系统说明材料 | P1 |
| FR-12-12 | 最小必要 | 连接器可配置字段级采集范围，非必要字段不入库 | P0 |
| FR-12-13 | 跨境标识与管控 | 标识数据存储与处理的地理位置；可强制禁止数据出境；出境场景输出评估材料 | P0 |
| FR-12-14 | 泄露检测与通知 | 异常导出/批量访问触发告警；提供泄露事件登记与通知时限跟踪（GDPR 72h） | P1 |
| FR-12-15 | 自动化决策说明 | 用户可要求解释某条答案的依据与生成过程（复用 FR-04-12 诊断链路） | P1 |
| FR-12-16 | 员工提问隐私 | 提问原文的留存与可见范围可配置，支持匿名化统计模式。**默认不留存原文**（决策 Q-6/Q-13），仅保留匿名化统计；客户放宽须为显式配置操作并留痕 | P0 |
| FR-12-17 | **抑制清单（墓碑）** | 已删除对象写入抑制清单，连接器同步前与索引重建前各校验一次，防止源系统未删导致"删了又长回来" | P0 |
| FR-12-18 | **限制处理状态** | 与法定保留期冲突时转入限制处理（依 PIPL 第 47 条第二款）：停止检索、生成、导出、统计，仅保留存储与安全措施；保留期届满**自动**触发真删，无需主体二次申请 | P0 |
| FR-12-19 | 主体反向索引 | 入库时维护「主体标识 HMAC → 出现位置」索引，避免删除请求退化为全库扫描；索引本身存 HMAC 不存明文 | P0 |
| FR-12-20 | 删除请求身份核验 | 走客户已有可信通道核验；响应不区分"未找到"与"已受理"，防止通过删除接口刺探某人是否为本机构客户 | P0 |
| FR-12-21 | 实体索引分表 | `SubjectIndex`（个人信息，存 HMAC，受删除权约束）与 `BusinessEntityIndex`（业务实体，可存明文）分表，按 FR-12-02 分级路由。**跨越两者的实体（如"客户联系人张三"）默认走 HMAC 表（决策 A-8，从严）** —— 分错的代价不对称：把个人信息当业务实体处理是合规事故，反之仅功能受限 | P1 |
| FR-12-22 | **删除位置类型可扩展** | `ErasureScope.location_type` 设计为可扩展枚举，不硬编码当前十值。**必须在 Phase 1 落地**：将来新增存储位置（如实体表）时无需重做删除链路与 DL-8 验证 | P0 |

**验收标准 / Acceptance Criteria**
- 删除测试（Canary 探针法）：注入合成主体 → 使其遍历全部十处 → 发起删除 → 直接探针（逐位置技术回查）与间接探针（诱导性提问）召回均为 0，并能输出删除报告。
- 负向用例必须通过：删除后触发源同步不重新入库；删除后索引全量重建不捞回；删除中途中断可幂等恢复。
- 分级规则变更后，存量数据可重新打标，且处置策略随之生效。
- 纯境内部署模式下，可证明不存在任何数据出境路径。
- 匿名化统计模式下，管理员无法还原任何单个员工的提问内容。

---

### FR-13 合规包与审计支撑 / Compliance Pack & Audit Support

**模块目标 / Objective**：把合规从"交付时突击准备材料"变成**产品内置的、可配置、可自检、可举证**的能力。
*Turn compliance from a scramble at delivery time into a built-in, configurable, self-checking, evidence-producing capability.*

| 编号 / ID | 功能点 / Function | 描述 / Description | 优先级 |
| --- | --- | --- | --- |
| FR-13-01 | 合规基线包 | 预置基线包：等保三级包、密评包、金融行业包、GDPR 包、信创包，可组合启用 | P0 |
| FR-13-02 | 基线一键应用 | 选定基线后自动套用对应的配置项（加密算法、留存期、认证强度、日志级别等） | P0 |
| FR-13-03 | 合规状态看板 | 实时展示每个控制项的达标状态：已满足 / 未启用 / 需客户侧配合 / 不适用 | P0 |
| FR-13-04 | 配置漂移检测 | 定期核查实际配置与基线的偏差，偏离即告警并记录 | P0 |
| FR-13-05 | 控制项映射清单 | 输出「监管条款 ↔ 产品功能 ↔ 责任方」三列对照表，可导出 | P0 |
| FR-13-06 | 责任矩阵 RACI | 明确每个控制项由产品、客户组织、客户基础设施中的哪一方负责 | P0 |
| FR-13-07 | 证据自动收集 | 为审计自动导出配置快照、日志样本、权限清单、加密算法清单 | P0 |
| FR-13-08 | SBOM 软件物料清单 | 输出全部组件、版本、开源许可证清单，支持标准格式（SPDX / CycloneDX） | P0 |
| FR-13-09 | 漏洞扫描与披露 | 定期扫描组件 CVE，输出漏洞状态与修复计划 | P0 |
| FR-13-10 | 模型来源合规 | 记录所用模型的来源、版本、许可证与商用限制，可追溯 | P0 |
| FR-13-11 | 变更合规影响评估 | 版本升级时标注哪些控制项受影响、是否需要复测 | P1 |
| FR-13-12 | 多辖区并存 | 同一实例内不同租户/业务域可应用不同的合规基线 | P2 |

**验收标准 / Acceptance Criteria**
- 切换合规基线包后，配置漂移检测报告显示 0 项不达标。
- 控制项映射清单中，每一项必须有明确责任方，**不允许出现"待定"**。
- SBOM 可导出为标准格式，且开源许可证清单中标明每个组件的商用限制。
- 审计员可在不联系研发的情况下，自助导出一次审计所需的全部证据材料。

---

### FR-14 生成式 AI 专项合规 / Generative AI Compliance

**模块目标 / Objective**：应对针对生成式 AI 的专门监管要求，这是传统企业软件合规框架不覆盖的部分。
*Address regulation aimed specifically at generative AI — a gap that conventional enterprise software compliance frameworks do not cover.*

> **关键认知 / Key insight**：《生成式人工智能服务管理暂行办法》的备案义务需**同时满足两个条件**：
> ① 向**境内公众**提供生成式 AI 服务（第 2 条明确"未向境内公众提供……的，不适用本办法"）；
> ② 该服务**具有舆论属性或者社会动员能力**（第 17 条，按《互联网信息服务算法推荐管理规定》履行算法备案）。
>
> 企业内部知识助手通常两条**均不满足**，但边界并不总是清晰——助手一旦嵌入对外客服，两条可能同时触发。产品应支持**适用性判定与备案材料输出**，而不是替客户下结论。
>
> *Filing obligations require both conditions: the service is offered to the public in China (Art. 2 excludes non-public-facing use), AND it has public-opinion attributes or social-mobilization capacity (Art. 17). An internal assistant normally meets neither — but embedding it in customer-facing support can trigger both. The product supports applicability assessment and filing materials; it does not decide for the customer.*

| 编号 / ID | 功能点 / Function | 描述 / Description | 优先级 |
| --- | --- | --- | --- |
| FR-14-01 | 适用性判定辅助 | 依据部署形态与使用范围，提示是否可能触发生成式 AI 备案义务 | P1 |
| FR-14-02 | 备案材料输出 | 输出模型清单、语料来源说明、安全措施说明等备案所需材料 | P1 |
| FR-14-03 | 内容安全过滤 | 对输入与输出做违法不良信息检测与拦截，策略可配置 | P0 |
| FR-14-04 | 拦截留痕 | 所有拦截事件记录并可审计，支持误拦申诉 | P0 |
| FR-14-05 | 知识来源合规记录 | 记录每个知识源的授权依据与使用范围，防止未授权内容入库 | P0 |
| FR-14-06 | AIGC 标识 | 对外输出场景下标注内容由 AI 生成，标识方式可配置 | P1 |
| FR-14-07 | 人工干预机制 | 支持专家复核、答案下架、紧急停用某类问答 | P0 |
| FR-14-08 | 安全边界策略 | 可配置禁答话题清单，越界时按策略拒答并记录 | P0 |
| FR-14-09 | 模型行为可解释 | 对任一答案可回放完整生成依据（复用 FR-04-12、FR-09） | P0 |
| FR-14-10 | 未成年人与特殊场景 | 若适用，支持相应的使用限制策略 | P2 |

**验收标准 / Acceptance Criteria**
- 内容安全过滤对约定测试集的拦截率与误拦率均达到合同约定阈值。
- 任一答案均可回放：问题 → 召回证据 → 生成 → 引用，链路完整无断点。
- 知识源无授权依据记录时，系统应阻止其进入生产索引。

---

### FR-15 可观测性 / Observability

**模块目标 / Objective**：让故障可定位、性能可回归、安全事件可溯源 —— 这三件事在 NFR-D-4 里只有一句话，不构成设计。

| 编号 / ID | 功能点 / Function | 描述 / Description | 优先级 |
| --- | --- | --- | --- |
| FR-15-01 | 结构化日志 schema | 定义各类日志的必含字段（trace_id、tenant_id、principal、actor、query_id、耗时分段），字段缺失即为缺陷 | P0 |
| FR-15-02 | 全链路追踪 | 一次提问的 trace 贯穿改写→召回→重排→生成→引用，各段耗时可查（OpenTelemetry 格式） | P0 |
| FR-15-03 | 核心指标暴露 | Prometheus 格式暴露：各段延迟分位、召回命中率、引用覆盖率、拒答率、缓存命中率、token 消耗 | P0 |
| FR-15-04 | **删除链路可观测** | 十处清除各自的耗时、成功率、失败原因；**限制处理对象数**；抑制清单命中数 | P0 |
| FR-15-05 | **安全告警** | ACL 同步滞后、越权拦截、**抑制清单命中数异常增长**（可能是删除滥用）、批量抽取特征、认证失败聚集 | P0 |
| FR-15-06 | 运营视角看板 | 面向管理员：答案空答率、低置信占比、点踩率、检索失败模式 Top-N、知识盲区 —— 与 FR-08-10 用量看板互补 | P1 |
| FR-15-07 | SLO 定义与燃尽 | 为可用性、端到端延迟、引用覆盖率定义 SLO 与错误预算，超支即告警 | P1 |
| FR-15-08 | 向量库健康 | segment 数、compaction 积压、内存占用、副本一致性 —— 直接关系 FR-12-04 的物理删除时点 | P1 |

**验收标准 / Acceptance Criteria**
- 任取一次线上提问，可凭 trace_id 还原全链路各段耗时与召回明细。
- 删除请求执行中，可实时观察十处的进度与失败项。
- 告警规则可在不改代码的前提下调整阈值。

---

### FR-16 限流与配额 / Rate Limiting & Quota

**模块目标 / Objective**：Agent 通道每秒可发起数十次调用，人不会 —— 缺限流会被单个失控调用方拖垮，且**批量抽取本身就是数据泄露向量**。设计见架构文档 §5.13。

| 编号 / ID | 功能点 / Function | 描述 / Description | 优先级 |
| --- | --- | --- | --- |
| FR-16-01 | 分层限流 | 按用户、按渠道、按 `(principal, actor)` 二元组分别限流，阈值可配 | P0 |
| FR-16-02 | **批量抽取检测** | 单会话时间窗内召回的 **unique chunk 数**超阈值即告警并降速；24h 触达文档数超历史基线数倍即通知安全 | P0 |
| FR-16-03 | 优雅拒绝 | 触发限流时返回明确错误码与重试建议，不静默丢弃 | P0 |
| FR-16-04 | 配额与计量 | 按租户/部门统计调用量与 token 消耗，为将来按量计费预留计量基础 | P1 |
| FR-16-05 | 索引侧资源隔离 | OCR / Embedding / 生成推理共享 GPU 时的优先级与批处理窗口，**保证查询侧 P95 不被索引任务拖垮** | P0 |

**验收标准 / Acceptance Criteria**
- 单个调用方无法通过高频调用使其他用户的 P95 退化超过约定阈值。
- 构造批量抽取场景（单会话遍历 ≥10% 语料）必须触发告警。
- NFR-P-5 索引吞吐的验收须在"查询 P95 不退化"前提下进行。

---

## 7. 非功能需求 / Non-Functional Requirements

### 7.1 性能 / Performance

| 编号 / ID | 需求 / Requirement | 目标 / Target |
| --- | --- | --- |
| NFR-P-1 | 端到端响应时间 / End-to-end latency | P50 ≤ 2.5s，P95 ≤ 4.0s |
| NFR-P-2 | 首字输出时间 / Time to first token | ≤ 1.5s |
| NFR-P-3 | 检索耗时 / Retrieval latency | ≤ 500ms（含召回 + 重排，P95） |
| NFR-P-4 | 并发能力 / Concurrency | 单节点支持 50 并发会话，可水平扩展 |
| NFR-P-5 | 索引吞吐 / Indexing throughput | ≥ 10,000 页/小时（标准配置），**且在查询侧 P95 不退化的前提下**（见 FR-16-05 GPU 资源隔离）。"标准配置"须在交付方案中给出具体硬件规格 |

### 7.2 规模 / Scale

| 编号 / ID | 需求 / Requirement | 目标 / Target |
| --- | --- | --- |
| NFR-S-1 | 文档规模 / Document volume | 单实例支持 ≥ 100 万文档 / 1,000 万 chunk |
| NFR-S-2 | 用户规模 / User base | 支持 ≥ 10,000 注册用户、1,000 日活 |
| NFR-S-3 | 知识源数量 / Sources | 单实例支持 ≥ 20 个知识源接入 |
| NFR-S-4 | 单文档大小 / Max file size | 支持 ≥ 500MB 单文件解析 |

### 7.3 可用性与可靠性 / Availability & Reliability

| 编号 / ID | 需求 / Requirement | 目标 / Target |
| --- | --- | --- |
| NFR-A-1 | 服务可用性 / Uptime | 通用 ≥ 99.5%；金融基线 ≥ 99.9%（均不含计划内维护） |
| NFR-A-2 | 数据持久性 / Durability | 原始文档与配置数据不丢失，支持定期备份与异地备份 |
| NFR-A-3 | 优雅降级 / Graceful degradation | 生成服务不可用时，退化为"只给引用文档列表"模式 |
| NFR-A-4 | 灾难恢复 / DR — **按基线分级** | 见下表 / See tiering below |
| NFR-A-5 | 备份可恢复性验证 / Restore drills | 至少每季度执行一次恢复演练并出具记录 |

> **关于 Self-RAG 与流式输出的结构性冲突 / On the Self-RAG vs streaming conflict**
>
> FR-05-12 要求流式输出（首字 ≤1.5s），FR-05-05b 的语义蕴含校验要求**生成完成后**再判断。二者不可同时满足：先流式则用户已读到未校验内容（违反 NFR-U-3 诚实表达），先校验则首字延迟被生成全长决定（违反 NFR-P-2）。
>
> **v1.0 的处理**：只做 FR-05-05a **引用绑定校验** —— 逐句检查该句是否绑定到证据，绑不上即丢弃。这是廉价的、逐句的，**可与流式并行**。重型的语义蕴含校验（FR-05-05b）降为 P1，若将来实现，须采用**分段生成—分段校验—分段输出**，并重新核算首字预算。
>
> *Streaming and post-hoc semantic verification cannot both hold. v1.0 ships the cheap per-sentence citation-binding check, which parallelises with streaming; semantic entailment verification is deferred to P1.*

**灾备分级 / DR Tiering**

| 合规基线 / Baseline | RPO | RTO | 说明 / Note |
| --- | --- | --- | --- |
| 通用企业 / General | ≤ 24h | ≤ 4h | 知识助手非业务关键路径 |
| 等保三级 / MLPS L3 | 重要数据 ≈ 0；其他数据 ≤ 4h | ≤ 2h | GB/T 22239-2019 三级要求**异地实时备份**（利用通信网络实时批量传送至备用场地）+ 重要数据处理系统**热冗余**。标准本身未规定 RPO/RTO 数值，此处为据该控制项推导的工程目标 |
| 金融行业 / Financial | ≤ 30min | ≤ 30min | 若助手进入对客业务链路，须按客户核心系统等级另行约定 |

> **档次说明 / Tier note**：按决策 Q-11，当前交付的**企业内部档（PF-1）**采用「通用企业」或「等保三级」行，**不做多活/异地容灾**。「金融行业」行仅在将来分支**对客档（PF-2）**时适用 —— 它需要真实的多活架构，属于无法预留、将来必须真做的工作（架构文档 §7.6.3）。
>
> **提醒 / Caveat**：v1.0 原定的 RPO ≤ 24h / RTO ≤ 4h 对金融客户是不达标的。金融基线的灾备目标会显著改变架构（多活/异地容灾）与成本，须在报价前确认助手是否进入对客链路。
> The original v1.0 targets do not meet financial-sector expectations. The financial tier materially changes architecture and cost, and must be confirmed before quoting.

### 7.4 部署与运维 / Deployment & Operations

| 编号 / ID | 需求 / Requirement | 说明 / Detail |
| --- | --- | --- |
| NFR-D-1 | 部署形态 / Deployment models | 私有云、混合云、信创环境三选一 |
| NFR-D-2 | 容器化 / Containerization | 全组件容器化，支持 Docker Compose 与 K8s 两种编排 |
| NFR-D-3 | 离线安装 / Air-gapped install | 支持完全离线环境的镜像与模型离线包安装 |
| NFR-D-4 | 可观测性 / Observability | 提供指标（Prometheus 格式）、日志、链路追踪 |
| NFR-D-5 | 升级 / Upgrade | 支持滚动升级，索引数据向后兼容 |

### 7.5 可用性设计 / Usability

| 编号 / ID | 需求 / Requirement | 说明 / Detail |
| --- | --- | --- |
| NFR-U-1 | 零培训上手 / Zero-training | 员工端界面无需培训即可使用 |
| NFR-U-2 | 引用可读性 / Citation legibility | 引用展开后须显示足够上下文，用户不必跳转即可判断 |
| NFR-U-3 | 诚实表达 / Honest UX | 不确定的答案必须在视觉上与高置信答案明显区分 |
| NFR-U-4 | 无障碍 / Accessibility | 满足 WCAG 2.1 AA 级基本要求 |

---

## 8. 合规与安全保障体系 / Compliance & Assurance Framework

### 8.1 措辞原则：为什么不写"全面支持" / Why We Do Not Say "Fully Compliant"

「等保三级 / GDPR / ISO 27001 / 金融监管**全面支持**」这句话在市场材料中常见，但**不应原样进入 PRD 与合同**，原因是这四者的性质根本不同：

*"Full support for MLPS L3 / GDPR / ISO 27001 / financial regulation" is common marketing copy but must not enter the PRD or contract verbatim, because these four are fundamentally different things:*

| 项 / Item | 认证对象 / What is certified | 谁持证 / Who holds it | 产品能做什么 / Product's actual role |
| --- | --- | --- | --- |
| **等保三级 / MLPS L3** | **已部署的信息系统**，由测评机构现场测评 | 客户（系统运营者） | 产品**不可能**持有等保三级；只能"支撑客户系统通过测评" |
| **密评 / Crypto Assessment** | 已部署系统的密码应用 | 客户 | 提供国密能力与密码应用方案材料 |
| **ISO 27001** | **组织的信息安全管理体系 ISMS** | 乙方公司或客户公司 | 产品不是认证对象；乙方可持证以证明研发过程受控 |
| **GDPR / PIPL** | 无认证机构，是**法律义务** | 数据控制者/处理者 | 提供履行义务所需的技术能力（删除、导出、留痕等） |
| **金融行业监管** | 非单一标准，随机构类型与业务而异 | 客户（持牌机构） | 按客户所适用的具体条款提供对应控制项 |

**因此本文档采用的标准表述 / The wording this document adopts：**

> 产品提供**可配置的合规能力集合**，并对每一条控制项标明**由谁负责**。
> 乙方承诺：产品侧控制项 100% 实现并可举证；客户侧控制项提供配置指引与配合测评。
> 乙方不承诺客户系统一定通过任何认证——通过与否取决于客户的组织制度、机房环境、运维流程等产品无法覆盖的因素。
>
> *The product provides a configurable set of compliance capabilities, with a named owner for every control. The vendor warrants full implementation and evidence for product-side controls, and provides configuration guidance plus assessment support for customer-side controls. The vendor does not warrant that the customer's system will pass any certification — that depends on organizational policy, facilities and operational process outside the product's scope.*

### 8.2 合规基线包设计 / Compliance Baseline Packs

对应 FR-13-01。基线包是**配置集合 + 控制项清单 + 证据模板**三件套，可叠加启用。

| 基线包 / Pack | 覆盖 / Covers | 关键强制项 / Key enforced settings |
| --- | --- | --- |
| **BL-1 等保三级** | GB/T 22239 三级要求 | MFA 强制、日志 ≥6 个月、剩余信息保护、异地备份、双人复核 |
| **BL-2 密评** | GB/T 39786 商用密码应用 | 国密算法全链路、认证密码模块、密钥分层与轮换 |
| **BL-3 中国数据法** | PIPL / 数据安全法 / 网络安全法 | 分类分级、贯穿式删除、最小必要、禁止出境、ROPA |
| **BL-4 金融行业** | JR/T 0197-2020 金融数据分级、JR/T 0171-2020 个人金融信息、《银行保险机构数据安全管理办法》（金规〔2024〕24 号） | 五级数据分级、C1/C2/C3 处置、日志长留存、金融灾备等级 |
| **BL-5 GDPR** | EU 2016/679 | 数据主体权利全集、DPIA 支撑、72h 泄露通知、跨境传输机制 |
| **BL-6 信创** | 国产化替代要求 | 国产 CPU/OS/DB/中间件/模型组合验证 |
| **BL-7 ISO 27001 支撑** | Annex A 相关技术控制项 | 访问控制、加密、日志、变更管理的证据输出 |

**冲突处理 / Conflict handling**：基线包之间可能冲突（例如 BL-5 的可携权导出 vs BL-4 的导出限制）。系统须在启用时检测冲突并提示，由客户合规官裁决，裁决结果记入配置日志。

### 8.3 合规交付物 / Compliance Deliverables

以下为**合同附件级交付物**，随版本更新：

| 编号 / ID | 交付物 / Deliverable | 首次交付时点 / First delivery | 更新频率 / Cadence | 提供方 / Provider |
| --- | --- | --- | --- | --- |
| DL-1 | **合规控制项检查清单**（条款 ↔ 功能 ↔ 责任方 RACI） | 生产上线前 | 每次大版本 | 乙方 |
| DL-2 | **第三方渗透测试报告**（摘要版对客，详版受控） | 生产上线前 | 每年 + 重大版本变更后 | 第三方机构，乙方委托 |
| DL-3 | **权限越权专项测试报告**（≥50 组用例） | MVP 验收 | 每次大版本 | 乙方 |
| DL-4 | **密码应用方案说明**（供密评受理） | 密评启动前 | 算法或密钥体系变更时 | 乙方 |
| DL-5 | **SBOM 与开源许可证清单** | 生产上线前 | 每次发版 | 乙方（自动生成） |
| DL-6 | **漏洞扫描报告与修复计划** | 生产上线前 | 每季度 | 乙方 |
| DL-7 | **数据流向说明**（含是否出境） | 生产上线前 | 架构变更时 | 乙方 |
| DL-8 | **删除权链路验证报告** | 生产上线前 | 每年 | 乙方 |
| DL-9 | **模型来源与许可合规说明** | 生产上线前 | 换模型时 | 乙方 |
| DL-10 | **安全应急响应预案与演练记录** | 生产上线后 30 天内 | 每半年 | 双方 |

**关于渗透测试报告的两点约定 / Two conditions on the pen test report：**

1. **范围必须写明 / Scope must be stated.** 报告须注明测试对象是「产品标准版本」还是「客户实际部署环境」——二者结论不能互相替代。客户环境的测试通常需客户授权与窗口期。
2. **详版受控分发 / Detailed version is controlled.** 完整渗透报告含可利用的漏洞细节，默认仅提供**摘要版 + 修复确认**；详版需签署保密附件并限定接收人。这不是推诿，而是避免报告本身成为攻击面。

### 8.4 复测节奏 / Retesting Cadence

| 触发条件 / Trigger | 需复测项 / Items to retest |
| --- | --- |
| 每年定期 / Annual | DL-2 渗透测试、DL-8 删除权验证 |
| 大版本发布 / Major release | DL-1 清单更新、DL-3 越权测试、DL-5 SBOM |
| 架构或数据流变更 | DL-7 数据流向说明、DL-2 补测 |
| 更换基座模型 | DL-9 模型合规说明、FR-14-03 内容安全阈值重测 |
| 加密算法或密钥体系变更 | DL-4 密码应用方案 |
| 新增知识源类型 | 权限继承专项验证 |
| 合规基线包切换 | FR-13-04 配置漂移全量核查 |
| 发现高危漏洞 / Critical CVE | 24h 内评估，按修复窗口补测 |

**漏洞修复窗口 / Vulnerability SLA**

| 等级 / Severity | 响应 / Response | 修复 / Fix |
| --- | --- | --- |
| 严重 Critical | ≤ 24h | ≤ 7 天 |
| 高 High | ≤ 72h | ≤ 30 天 |
| 中 Medium | ≤ 7 天 | 下个版本 |
| 低 Low | 记录 | 择机 |

### 8.5 责任划分示例 / Responsibility Split (Sample RACI)

完整清单见 DL-1，此处示例说明划分逻辑：

| 控制项 / Control | 产品 / Product | 客户组织 / Customer Org | 客户基础设施 / Customer Infra |
| --- | --- | --- | --- |
| 身份鉴别与 MFA | **R** 提供能力 | A 制定策略 | C 提供 IdP |
| 数据加密（国密） | **R** 实现算法 | I | **A** 提供认证密码模块 |
| 机房物理安全 | — | I | **R/A** |
| 安全管理制度与人员 | — | **R/A** | I |
| 日志留存与审计 | **R** 生成与保护 | **A** 定期审阅 | C 提供存储 |
| 等保测评组织与受理 | C 配合 | **R/A** | C |
| 数据分类分级规则 | **R** 提供工具 | **A** 定义业务规则 | — |
| 灾备与演练 | C 提供方案 | A | **R** 执行 |

> R = 执行 Responsible，A = 负责 Accountable，C = 被咨询 Consulted，I = 被告知 Informed
>
> **注意**：表中带 **A** 的项若落在客户侧，产品再完善也无法代替。这正是不能承诺"客户一定过等保"的原因。
> Where **A** sits on the customer side, no amount of product capability substitutes for it — which is precisely why certification outcomes cannot be warranted.

---

## 9. 数据模型 / Data Model

### 9.1 核心实体 / Core Entities

| 实体 / Entity | 说明 / Description | 关键字段 / Key Fields |
| --- | --- | --- |
| `Source` 知识源 | 一个已接入的系统或目录 | source_id, type, config, sync_policy, scope_rules, status |
| `Document` 文档 | 源系统中的一篇内容 | doc_id, source_id, external_id, title, url, author, created_at, updated_at, version, doc_type, effective_until, status |
| `Chunk` 片段 | 文档切分后的检索单元 | chunk_id, doc_id, seq, text, page_no, section_path, clause_no, token_count, hash |
| `Embedding` 向量 | chunk 的向量表示 | chunk_id, model_name, model_version, vector |
| `AclEntry` 权限项 | 文档的访问控制条目 | doc_id, principal_type(user/group/role/everyone), principal_id, **effect(allow/deny)**, permission, **scope_level(source/folder/document/field)**, **inherited_from**(上级对象 ID，NULL=本级直接设置), **inheritance_broken**(该对象是否断开继承) |
| `Query` 提问 | 一次用户提问 | query_id, user_id, channel, session_id, raw_text, rewritten_text, created_at |
| `Retrieval` 检索记录 | 一次提问的召回明细 | query_id, chunk_id, bm25_score, vector_score, fused_score, rerank_score, used_in_generation |
| `Answer` 答案 | 一次生成结果 | answer_id, query_id, text, confidence, model_name, latency_ms, refused, refusal_reason |
| `Citation` 引用 | 答案中某句话与证据的绑定 | citation_id, answer_id, sentence_index, chunk_id, doc_id, **locator{page, section_path, clause, char_start, char_end}**（结构化，与 FR-02-13 字符锚点、架构 §4.3 证据信封一致；亦是删除链路对答案与会话做定点脱敏的锚点）, **chunk_content_hash**, **chunk_version**（证据快照，FR-05-20） |
| `Feedback` 反馈 | 用户对答案的评价 | feedback_id, answer_id, user_id, rating, reason, status |
| `CuratedQA` 精选问答 | 专家固化的标准答案 | qa_id, question_patterns, answer, citations, owner, effective_until |
| `EvalCase` 评测样例 | 评测集中的一条 | case_id, set_id, question, expected_answer, expected_chunk_ids, scenario |
| `AuditLog` 审计日志 | 不可变操作记录 | log_id, actor_id, action, target, before, after, timestamp, hash_prev |

> **`AclEntry` 的四个加粗字段是 Phase 1 必须落地的表结构项。** 只有"允许名单"的模型装不下 NTFS 的显式 Deny 与 SharePoint 的断开继承 —— 后补的代价不是功能缺失，而是在补上之前一直存在**静默越权**。求值语义见 FR-06-28。

### 9.2 关键关系 / Key Relationships

```
Source 1──N Document 1──N Chunk 1──1 Embedding
                 │              │
                 └──N AclEntry  └──N Retrieval ──N Query 1──N Answer 1──N Citation
                                                              │
                                                              └──N Feedback
```

### 9.3 数据保留 / Data Retention

| 数据类型 / Type | 默认保留期 / Default Retention | 可配置 / Configurable |
| --- | --- | --- |
| 原始文档缓存 / Raw doc cache | 与源同步，源删即删 | 否 / No |
| 提问与答案 / Queries & answers | 180 天 | 是 / Yes |
| 审计日志 / Audit logs | 365 天后归档 | 是 / Yes |
| 反馈与评测集 / Feedback & eval sets | 永久 / Permanent | 是 / Yes |

---

## 10. 成功指标 / Success Metrics

### 10.1 产品北极星指标 / North Star

> **有引用的一次性解决率 / Cited One-Shot Resolution Rate**
> 用户提问后，无需追问、无需转人工、且答案带有效引用的比例。
> The share of questions answered in one shot, with valid citations, and without escalation.

### 10.2 指标体系 / Metric Framework

| 类别 / Category | 指标 / Metric | 目标值 / Target | 备注 / Note |
| --- | --- | --- | --- |
| 质量 / Quality | Top-3 检索命中率 | 约定基准（页面自述 92.4%） | 需以客户评测集重新标定 |
| 质量 / Quality | 引用覆盖率 | 100% | 硬性要求 / Hard requirement |
| 质量 / Quality | 幻觉率 | ≤ 2% | 人工评审；口径等价于 1 − CSR，见下行 |
| 质量 / Quality | **引用支撑率 CSR** | ≥ 0.98 | **Citation Support Ratio = 被证据支撑的引用数 / 引用总数**。逐条引用做二值判定（支撑 / 不支撑，含"矛盾、缺失、无法判定"三类均记为不支撑），判定器须用 50–100 条人工标注校准后方可投入批量评测。此指标使「幻觉率」从主观印象变为可复算的数字 |
| 质量 / Quality | recall@10 / recall@50 | 按客户评测集标定 | FR-04-17 基线；二者差值决定是否启用重排，并作为漂移监测基准 |
| 质量 / Quality | 应拒答而未拒答率 | ≤ 5% | |
| 性能 / Performance | 端到端 P50 / P95 | ≤ 2.5s / ≤ 4.0s | |
| 采纳 / Adoption | 月活占比 | ≥ 40% 目标用户 | 上线 3 个月内 |
| 采纳 / Adoption | 人均月提问量 | ≥ 8 次 | |
| 业务 / Business | 专家咨询量变化 | −50%~−68%（90 天对比） | 需上线前建立基线 |
| 业务 / Business | IT 工单量变化 | −55% | 场景 S-02 |
| 业务 / Business | 新人上岗周期 | −60% | 场景 S-01 |
| 满意 / Satisfaction | 答案点赞率 | ≥ 80% | |
| 安全 / Security | 越权访问事件 | 0 | 硬性要求 |
| 合规 / Compliance | 控制项责任方明确率 | 100% | 不允许"待定" |
| 合规 / Compliance | 产品侧控制项实现率 | 100% | 客户侧控制项不计入 |
| 合规 / Compliance | 配置漂移未达标项 | 0 | 按基线包核查 |
| 合规 / Compliance | 严重/高危漏洞未修复数 | 0 | 超出 SLA 窗口即计为不达标 |
| 合规 / Compliance | 删除请求按时完成率 | 100% | 默认 ≤15 个工作日 |

> **测量前提 / Measurement prerequisite**：所有业务指标（专家咨询量、工单量、上岗周期）必须在系统上线前建立至少 30 天的基线数据，否则"下降 X%"无法成立。
> All business metrics require at least 30 days of pre-launch baseline data; without it, any "−X%" claim is unfalsifiable.

---

## 11. 发布计划 / Release Plan

### Phase 1 — MVP（第 1–8 周 / Weeks 1–8）

**目标 / Goal**：单一部门、2 个知识源跑通"提问→带引用答案"的完整闭环。

- FR-01：手动上传、共享盘、Confluence 连接器；增量与全量同步
- FR-02：多格式解析、OCR、语义切分、位置锚点
- FR-03：向量 + 全文双索引
- FR-04：混合召回 + 重排 + 权限过滤 + 诊断面板
- FR-05：强制引用、拒答、置信度、流式输出、反馈
- FR-06：SSO、ACL 继承、MFA、口令与会话策略、越权检测
- FR-07：Web 门户
- FR-09：全量日志
- FR-10：评测集与自动评测
- FR-11：加密算法套件可切换架构（**国密实现可在 Phase 2 完成，但架构必须 Phase 1 定型**，否则后期返工成本极高）
- FR-12：PII 识别、贯穿式删除链路、最小必要采集
- FR-13：SBOM 自动生成、控制项映射清单初版

**出口标准 / Exit criteria**：200 题评测集达成约定命中率；引用覆盖率 100%；越权用例 0 命中；删除链路**十处**残留验证通过（DL-8 初版，Canary 探针法）。

### Phase 2 — 生产就绪（第 9–16 周 / Weeks 9–16）

- FR-01：SharePoint、Notion、数据库、工单系统连接器；Webhook 实时同步
- FR-05：精选答案、冲突提示、时效提示
- FR-06：字段级权限、双人复核、剩余信息保护、信创适配
- FR-07：企业微信 / 钉钉 / 开放 API
- FR-08：完整管理后台
- FR-10：线上抽检、分场景报表、坏例归因
- FR-11：国密算法全链路、密码模块对接、密钥分层与轮换、密评材料输出
- FR-12：数据分类分级、删除证明、跨境管控、ROPA
- FR-13：合规基线包、状态看板、漂移检测、责任矩阵 RACI
- FR-14：内容安全过滤、知识来源合规记录、人工干预机制

**出口标准 / Exit criteria**：三个业务场景（S-01、S-02 + 一个客户选定场景）上线；全员可用；业务指标开始采集；**DL-1~DL-9 合规交付物全部产出**；第三方渗透测试完成且严重/高危漏洞清零。

### Phase 3 — 深化（第 17 周起 / Week 17+）

- 多租户、Slack、坐席侧边栏、知识盲区与冲突报告
- 自定义连接器 SDK、SIEM 对接、图片理解
- Agent 化能力评估（超出 v1.0 范围 / beyond v1.0 scope）

### 交付节奏 / Delivery Cadence

| 里程碑 / Milestone | 时间 / Timing |
| --- | --- |
| 初步方案与报价 / Preliminary proposal & quote | 需求确认后 5 个工作日内 |
| POC 完成 / POC complete | 第 4 周 |
| MVP 上线 / MVP live | 第 8 周 |
| 合规交付物齐备 / Compliance deliverables ready | 第 15 周（生产上线前） |
| 生产上线 / Production launch | 第 16 周 |
| 效果复盘 / Results review | 上线后第 90 天 |
| 等保/密评测评窗口 / Assessment window | 由客户安排，乙方配合（建议上线后 3–6 个月） |
| 首次年度复测 / First annual retest | 上线后 12 个月 |

---

## 12. 风险与依赖 / Risks & Dependencies

### 12.1 风险 / Risks

| 编号 / ID | 风险 / Risk | 影响 / Impact | 缓解措施 / Mitigation |
| --- | --- | --- | --- |
| R-1 | **知识源质量差** — 文档过时、互相矛盾、大量草稿 | 高 / High | 上线前做知识源体检；FR-05-08 冲突提示；FR-08-08 盲区报告；把"内容治理"写入交付责任划分 |
| R-2 | **源系统权限模型复杂或不可导出** | 高 / High | 前置权限调研；无法继承时降级为按目录手工授权，并明确告知客户风险 |
| R-3 | **业务指标无基线** — 无法证明 −55%、−68% 等效果 | 中 / Medium | 上线前强制采集 30 天基线；未采集则改用过程指标验收 |
| R-4 | **用户不信任 AI 答案** | 中 / Medium | 引用可点击 + 低置信显式标注 + 专家精选答案背书 |
| R-5 | **私有模型效果不及商用 API** | 中 / Medium | FR-05-15 模型可插拔 + 按场景路由；在评测集上做选型对比 |
| R-6 | **GPU 资源不足或成本超预期** | 中 / Medium | 提供量化版模型部署方案；明确不同并发规模的硬件配置基线 |
| R-7 | **扫描件与复杂表格解析质量不达标** | 中 / Medium | FR-02-12 质量报告 + 人工兜底流程；高价值文档人工校验 |
| R-8 | **场景铺得太开、无一深入** | 中 / Medium | Phase 1 只做 1–2 个场景，做透再扩 |
| R-9 | **「全面支持合规」措辞导致无限责任** — 客户审计师可提出任意控制项 | **高 / High** | 见 §8.1：合同只写「产品侧控制项 100% 实现 + 责任矩阵」，不承诺认证结果；DL-1 清单是责任边界的书面依据 |
| R-10 | **密评不通过** — 仅用 AES-256/TLS 1.3，无国密与认证密码模块 | **高 / High** | FR-11 全模块；加密算法做成可切换配置；密码模块采购与选型须在 Phase 1 启动，交期长 |
| R-11 | **删除权无法举证** — PII 残留在向量/缓存/日志中 | **高 / High** | FR-12-04 贯穿式删除 + FR-12-05 删除证明；DL-8 每年验证；此项在 GDPR/PIPL 检查中是高频失分点 |
| R-12 | **开源模型许可证不允许商用或有条件限制** — 如 Llama 系列并非标准开源许可 | 中 / Medium | FR-13-10 模型来源合规；选型阶段由法务出具许可意见；备选纯 Apache-2.0 / MIT 许可模型 |
| R-13 | **生成式 AI 备案义务被误判** — 助手嵌入对客场景后触发备案 | 中 / Medium | FR-14-01 适用性判定；边界变化（如接入对客客服）须重新评估，写入变更管理流程 |
| R-14 | **金融灾备目标推翻现有架构** — RPO 30min 需多活或异地容灾 | 中 / Medium | §7.3 灾备分级；报价前确认助手是否进入对客业务链路，成本差异显著 |
| R-15 | **渗透测试详版报告外泄** 成为攻击面 | 中 / Medium | §8.3：默认只发摘要版 + 修复确认；详版签保密附件、限定接收人 |
| R-16 | **合规基线包之间相互冲突**（如可携权导出 vs 金融导出限制） | 低 / Low | FR-13 启用时冲突检测；由客户合规官裁决并记入配置日志 |
| R-17 | **差异化定位已被国内竞品占据** — 「Schema 优先解析为实体图 + ChangeSet 可追溯/可回滚的知识更新」这一组合，与本产品预留的实体层 + 版本化证据方向高度重合，国内已有厂商以此为主打定位 | 中 / Medium | 本产品的护城河应压在**权限求值的正确性**（I-1/I-2 拒绝优先与断开继承）与**贯穿式删除可举证**（FR-12-04/05）上 —— 这两项竞品普遍只做到"有这个功能"而非"可审计地正确"。实体层本身不构成差异化，不应作为卖点投入 |
| R-18 | **开源对标产品的归属变化** — 本产品常被与开源自托管方案对标，而该类项目可能被大厂收购，其长期许可与治理承诺存在不确定性 | 低 / Low | 不在架构上依赖任何单一外部开源产品；对标说辞须注明时点。报价场景如引用竞品状态，须复核当期事实 |

### 12.2 外部依赖 / External Dependencies

| 编号 / ID | 依赖 / Dependency | 责任方 / Owner |
| --- | --- | --- |
| D-1 | 源系统 API 账号与读取权限 | 客户 IT / Customer IT |
| D-2 | SSO / LDAP 对接配置 | 客户 IT |
| D-3 | GPU 服务器或推理资源 | 客户基础设施 / Customer Infra |
| D-4 | 业务评测集（≥ 200 题）与标准答案 | 客户业务方 + 乙方共建 |
| D-5 | 上线前 30 天业务基线数据 | 客户业务方 |
| D-6 | 安全审查与合规审批窗口 | 客户安全合规 |
| D-7 | 认证密码模块 / 密码机（密评所需） | 客户基础设施（采购交期长，需提前启动） |
| D-8 | 第三方渗透测试机构选定与授权 | 客户安全 + 乙方（DL-2） |
| D-9 | 数据分类分级业务规则定义 | 客户业务方 + 合规（产品只提供工具） |
| D-10 | 等保测评 / 密评机构对接与受理 | 客户（系统运营者，乙方仅配合） |
| D-11 | 若涉数据出境：出境安全评估申报 | 客户法务合规 |

---

## 13. 待定问题 / Open Questions

> **状态图例**：✅ 已决 · 🔶 待客户 / 报价前 · ⏸ 挂起（Phase 2/3 再议）
> 全量 32 项（含另两份文档的 E-* 与 A-*）见《决策台账》。

| 编号 / ID | 状态 / Status | 问题 / Question | 结论或待决 / Resolution | 责任方 / Owner |
| --- | --- | --- | --- | --- |
| Q-1 | ✅ 已决 | v1.0 是否支持多租户？ | **单租户单实例交付**；但所有表**必须带 `tenant_id` 字段**（v1 恒为默认值）。理由见架构文档 R-9：后补需改全表 + 回填 + 逐条审计租户过滤，属安全级返工 | 产品 + 交付 |
| Q-2 | ⏸ 挂起 | 基座模型选型：自部署开源 vs 商用 API vs 混合路由？ | 依赖 Q-9 的合规基线结论；POC 前再定 | 算法 + 客户安全 |
| Q-3 | 🔶 报价前 | 检索指标（如 92.4%）的口径如何定义并写入合同？ | **高风险**：在给一个数字设定可验证边界，含糊过去即埋雷 | 产品 + 商务 |
| Q-4 | ⏸ 挂起 | 业务库接入是走离线抽取还是实时 Text-to-SQL？ | Phase 2 前再定 | 架构 |
| Q-5 | ⏸ 挂起 | 多份文档冲突时是否允许"权威源优先自动裁决"？ | Phase 2 再定 | 产品 + 客户业务方 |
| Q-6 | ✅ 已决 | 日志中是否保存问题原文？ | **做成可配置，默认不留存原文**，仅保留匿名化统计（FR-12-16）。默认从严；客户放宽须为显式操作并留痕 | 安全合规 |
| Q-7 | 🔶 报价前 | 定价模型：按用户数 / 按文档量 / 按部署包一次性？ | — | 商务 |
| Q-8 | 🔶 报价前 | 内容治理（清理过时文档）由客户还是乙方负责，是否计入报价？ | — | 商务 + 交付 |
| Q-9 | 🔶 待客户 | 首批交付客户适用哪几个合规基线包？ | 全部支持 ≠ 全部同时交付。**Q-10 密码模块选型依赖本项结论** | 商务 + 产品 |
| Q-10 | ⏸ 挂起 | 密码模块选型：软件密码模块 vs 加密卡 vs 密码机？ | **依赖 Q-9** —— 仅走密评的客户需要，而是否需密评本身要逐案判断（见 FR-11） | 架构 + 客户基础设施 |
| Q-11 | ✅ 已决 | 知识助手是否会进入对客业务链路？ | **当前只做企业内部档（PF-1）** —— 员工问、员工看，灾备按通用/等保基线，不触发生成式 AI 备案。**对客档（PF-2）作为独立产品后续分支**，当前仅保留四项低成本预留（FR-01-18 受众标签、FR-05-20 证据快照、服务无状态、组件选型约束）。**不 fork 代码** —— 档次以部署档案承载，复用 FR-13 基线包机制 | 产品 + 商务 |
| Q-12 | 🔶 报价前 | 渗透测试对象是产品标准版本还是客户部署环境？ | 后者需客户授权与窗口期，费用归属需明确 | 商务 + 客户安全 |
| Q-13 | ✅ 已决 | 员工提问原文是否留存？（工会/劳动法） | **同 Q-6**：可配置，默认不留存。客户如需留存，由其 HR 与合规出具依据 | 客户 HR + 合规 |
| Q-14 | 🔶 报价前 | 合规能力是打包在标准版，还是作为付费模块单独定价？ | — | 商务 |
| Q-15 | ⏸ 挂起 | FR-05-05b 语义蕴含校验的实现路径：LLM-as-judge vs 专用 NLI 模型 vs 不实现？ | 与流式输出结构性冲突（见 §7.1）；v1.0 已降为 P1，实现前须重算首字预算 | 算法 + 产品 |
| Q-16 | ⏸ 挂起 | Cross-Encoder 重排模型与 Top-K 的默认值？ | 直接决定 NFR-P-3 能否达成。评审建议 `bge-reranker-base` + Top-K=20 作为基线，须实测验证 | 算法 |
| Q-17 | 🔶 待定 | FR-04-14b 按权限分区缓存是否实现？ | 反向映射规模随权限组数增长，**须先做容量评估**。FR-04-14a 基础缓存已升 P0 并满足删除链路前置 | 架构 |
| Q-18 | ⏸ 挂起 | 信创适配矩阵：哪些 CPU/OS/DB/中间件/模型组合必须覆盖？ | FR-06-12 仅说方向。依赖 Q-9 首批客户的合规基线结论 | 研发 + 交付 |
| Q-19 | ⏸ 挂起 | NG-8 实体层何时重启评估？ | **改为可测量的触发条件，不再凭判断**：当 FR-10-06 坏例归因中「实体混淆」（同名不同实体、需跨文档上下文才能消歧）占失败样例 **≥ 15%**，且该比例在连续两次回归中不下降时，重启评估。schema 侧已由 FR-12-21 预留，届时无需返工。**在触发之前不得以"行业趋势"为由提前开工** | 算法 + 产品 |
| Q-21 | ⏸ 挂起 | `acls` 字典是否改为按 `(object_type, object_id)` 做键？ | 现状靠 ID 前缀保证 object_id 租户内唯一（TD-01 C-7 / TD-03 P-0），**schema 无法强制**。改成复合键可从结构上消除撞号越权，代价是求值接口与 26 条向量都要改。**第一个连接器落地后再定** —— 届时能看到真实源系统的标识形态 | 架构 + 安全 |
| Q-22 | ⏸ 挂起 | group 与 role 是否按类型分别匹配？ | 现状 `memberships` 是两者的并集，靠 `grp_` / `role_` 前缀防撞号（TD-03 P-3）。严格做法是按类型分别匹配，代价同 Q-21。**与 Q-21 一并决定** | 架构 + 安全 |
| Q-23 | ⏸ 挂起 | 数据分级字段何时落地？ | TD-03 D-3 要求「L0 → audience='customer_safe' 且分级 ≤2」，但 schema 无分级字段（只有部署档案上的 `content_safety_level` 与 `annotations.kind='sensitivity'`）。D-3 属 **Phase 3**（身份阶梯 FR-06-18~27），当期不阻塞。真要做时按 JR/T 0197 五级或客户自有分级建字段，**届时是加列而非改结构**，无返工风险 | 产品 + 安全 |
| Q-20 | 🔶 待客户 | 是否需要新增 **BL-6 欧盟 AI 法案包**？ | 仅当客户或其集团受欧盟管辖时才需要。**注意本产品的适用性判断**：企业内部知识助手一般不属高风险，但若被用于招聘、绩效、晋升等人事决策的依据，可能落入附件 III 高风险清单，义务与时点完全不同。**时点已变更**：数字综合法案（Regulation (EU) 2026/1744）将附件 III 高风险义务由 2026-08-02 推迟至 **2027-12-02**，附件 I 高风险至 2028-08-02，公共机构 AI 至 2030-08-02；第 50(2) 条透明度过渡期为 2026-12-02。引用前须由法务复核当期文本 | 商务 + 法务 |

---

## 14. 附录 / Appendix

### E. 外部证据等级 / External Evidence Grading

> 本节存在的理由：v1.7 的技术决策引用了外部基准与行业数据。**外部数字一旦进了 PRD，就可能被复制进标书与合同**，所以必须标明每一条的可信级别与可用范围。
> **规则：只有 A 级可以进客户材料与合同；B 级可以作为内部设计依据但须注明出处与时点；C 级禁止以任何形式引用。**

| 编号 | 结论 | 来源与性质 | 级别 | 允许的用法 |
| --- | --- | --- | --- | --- |
| **EV-1** | 在企业内部语料上，**BM25 的正确率显著高于稠密向量检索**（68.8% vs 51.4%；文档召回 68.4% vs 46.0%；"信息冲突"类问题 90% vs 60%） | *EnterpriseRAG-Bench*，arXiv:2605.05253，2026-05-05。Onyx + 加州大学伯克利分校。约 51.2 万文档合成语料（Slack/Gmail/Drive/GitHub/Jira/Confluence 等 9 类源）+ 500 题 | **B** | 可作为 FR-04-05 默认权重取向的设计依据。**不得当作普适结论**：①语料为合成，非真实企业数据；②第一作者所属公司提供基于 BM25 的混合检索产品，存在利益相关（已公开披露）；③单一论文、未经同行评审。**客户现场一律以其自有评测集的实测结果为准** |
| **EV-2** | **引用的事实准确性随工具/检索调用轮次加深而显著衰减**（工具调用从 2 次增至 150 次，事实核验通过率平均下降约 42%），而链接有效性与主题相关性保持在 92% 以上 | arXiv:2605.06635，2026-05-07，普华永道商业技术与创新办公室。130 个研究问题 × 14 个模型 | **B** | 可作为 N-5（推迟 FR-05-05b）、NG-7（不作为编排者）与 FR-05-05a「逐步校验」约束的依据。**边界**：该研究针对面向公开网页的深度研究 Agent，非企业内部 RAG，方向性可借鉴、数值不可直接搬用 |
| **EV-3** | 加入重排后 **Recall@10 由 74% 提升至 89%**（+15 个百分点）；**收益集中在 recall@10 低而 recall@50 高的场景** | Databricks 官方博客，2025-08-18（2026-06 更新）。厂商自有基准，数据集未公开，重排模型未具名 | **C（数值）/ B（机理）** | **数值禁止引用**（厂商自测、不可复现）。但"收益取决于 recall@10 与 recall@50 的差值"这一**机理**可用，它正是 FR-04-17 的设计依据 |
| **EV-4** | 生产环境法律 RAG 系统幻觉率 **17%–33%** | 斯坦福 RegLab / HAI，预注册研究，*Journal of Empirical Legal Studies*。原文给出的是**区间**，未按产品拆分 | **A（作区间引用）** | 可用于向客户说明"RAG 降低但不消除幻觉"。**不得拆成"某产品 17%、某产品 33%"** —— 那是二手材料的加工。另须注明研究时点早于当前系统 |
| **EV-5** | **95% 的企业生成式 AI 试点未产生可测量的损益影响** | MIT NANDA《State of AI in Business 2025》，2025-08 | **B** | 可用作立项叙事。**须注明年份**，且该结论的方法论受到较多公开质疑，不宜作为唯一论据 |
| **EV-6** | 「ERAA-2026」企业 RAG 准确性审计：38% 引用不支撑、CSR 0.62、时序幻觉 29%、17% 系统存在注入风险、72% 错误答案使用高置信措辞、年均敞口 230 万美元、实体消歧错误率 44% | **无法验证**。仅见于单篇二手博客（ragaboutit.com，2026-07-12）。文中称由"三家主要云厂商与两所学术实验室"完成但**未具名任何一方**；**无论文、无 arXiv 编号、无仓库、无发布渠道**；唯一外链指向一个厂商站点。文中若干配套数字另注明并非出自该基准 | **C — 禁止引用** | **任何场合均不得使用，包括内部文档。** 本 PRD 中与这些数字主题相关的需求（CSR、注入防护、时序）均已改挂 EV-1/EV-2 或产品自身验收口径 |

> **方法学提示**：EV-6 这类"数字很完整、来源很模糊"的材料，在 AI 领域的二手内容中占比不低。**判别方法**：能否在 30 秒内找到原始论文或数据集。找不到即按 C 级处理，无论数字看起来多合理。

---

### A. 术语表 / Glossary

| 术语 / Term | 说明 / Definition |
| --- | --- |
| RAG | Retrieval-Augmented Generation，检索增强生成。先检索企业知识，再让模型基于检索到的证据作答。 |
| Chunk | 文档切分后的最小检索单元。 |
| Embedding | 把文本映射为向量的过程或结果，用于语义相似度检索。 |
| BM25 | 经典全文检索算法，基于词频与逆文档频率，擅长精确关键词匹配。 |
| 混合检索 / Hybrid retrieval | 同时使用全文检索与向量检索并融合结果。 |
| RRF | Reciprocal Rank Fusion，一种按排名倒数融合多路检索结果的方法。 |
| Rerank / 重排 | 用更精细的模型对初步召回结果重新排序。 |
| Cross-Encoder | 将问题与候选文本一起输入模型打分的重排模型，精度高于双塔但更慢。 |
| Self-RAG | 生成后自我校验，检查每条陈述是否被证据支撑的机制。 |
| CSR | Citation Support Ratio，引用支撑率 = 被证据支撑的引用数 / 引用总数。见 §10.2。 |
| recall@k | 正确答案出现在前 k 条检索结果中的查询占比。本文档同时测 k=10 与 k=50，二者差值决定是否启用重排（FR-04-17）。 |
| 幻觉 / Hallucination | 模型输出听起来合理但没有事实依据的内容。 |
| ACL | Access Control List，访问控制列表。 |
| 信创 | 信息技术应用创新，指国产化软硬件替代。 |
| 等保三级 | 网络安全等级保护第三级。对**已部署信息系统**的定级备案与等级测评（非产品认证、无认证证书），责任主体是系统运营者。详见 §8.1。 |
| PII | Personally Identifiable Information，个人可识别信息。 |
| P50 / P95 | 中位数 / 95 分位响应时间。 |
| 密评 | 商用密码应用安全性评估。法定强制对象为关键信息基础设施等（等保三级不自动触发，详见 FR-11），适用时要求国密算法与经商用密码产品认证的密码模块。 |
| 国密 / SM 系列 | 国家密码管理局发布的商用密码算法：SM2（非对称）、SM3（摘要）、SM4（对称）。 |
| TLCP | 传输层密码协议（**GB/T 38636-2020** 国家标准），国密体系下的 TLS 替代方案，采用签名+加密双证书。注意 GM/T 0024 是《SSL VPN 技术规范》行业标准，二者相关但非同一文件。 |
| HSM | Hardware Security Module，硬件密码模块 / 密码机，用于保护根密钥。 |
| PIPL | 《个人信息保护法》，中国个人信息处理的基础法律。 |
| DSL | 《数据安全法》，规定数据分类分级与重要数据保护义务。 |
| ROPA | Records of Processing Activities，处理活动记录（GDPR 第 30 条 / PIPL 对应要求）。 |
| DPIA / PIA | 数据保护影响评估 / 个人信息保护影响评估。 |
| 数据主体权利 | 查询、更正、删除、可携、限制处理等个人对其信息享有的权利。 |
| JR/T 0197 | 《金融数据安全 数据安全分级指南》，将金融数据分为五级。 |
| JR/T 0171 | 《个人金融信息保护技术规范》，将个人金融信息分为 C1/C2/C3 三类敏感度。 |
| SBOM | Software Bill of Materials，软件物料清单，列出全部组件、版本与许可证。 |
| SPDX / CycloneDX | 两种主流 SBOM 标准格式。 |
| RACI | 责任分配矩阵：Responsible 执行、Accountable 负责、Consulted 被咨询、Informed 被告知。 |
| RPO / RTO | 恢复点目标（可容忍的数据丢失量）/ 恢复时间目标（可容忍的中断时长）。 |
| AIGC | AI Generated Content，人工智能生成内容。 |
| 生成式 AI 办法 | 《生成式人工智能服务管理暂行办法》，对向公众提供的生成式 AI 服务规定备案等义务。 |
| MFA | Multi-Factor Authentication，多因素认证。等保三级身份鉴别要求针对**用户**（非仅管理员）采用两种以上鉴别技术，且至少一种以密码技术实现。 |
| BL / DL | 本文档编号：BL = 合规基线包（§8.2），DL = 合规交付物（§8.3）。 |

### B. 需求索引 / Requirement Index

| 模块 / Module | 编号范围 / ID Range | 需求数 / Count | P0 数 / P0 Count |
| --- | --- | --- | --- |
| FR-01 数据接入 / Ingestion | FR-01-01 ~ FR-01-20 | 19 | 13 |
| FR-02 解析切分 / Parsing | FR-02-01 ~ FR-02-13 | 13 | 10 |
| FR-03 向量索引 / Indexing | FR-03-01 ~ FR-03-12 | 11 | 7 |
| FR-04 检索重排 / Retrieval | FR-04-01 ~ FR-04-17 | 17 | 12 |
| FR-05 生成引用 / Generation | FR-05-01 ~ FR-05-20 | 19 | 14 |
| FR-06 身份权限 / Access Control | FR-06-01 ~ FR-06-29 | 19 | 15 |
| FR-07 接入渠道 / Channels | FR-07-01 ~ FR-07-10 | 10 | 4 |
| FR-08 管理后台 / Admin | FR-08-01 ~ FR-08-12 | 12 | 7 |
| FR-09 审计日志 / Audit | FR-09-01 ~ FR-09-10 | 10 | 7 |
| FR-10 评测质量 / Evaluation | FR-10-01 ~ FR-10-08 | 8 | 3 |
| FR-11 密码保护 / Cryptography | FR-11-01 ~ FR-11-12 | 12 | 10 |
| FR-12 隐私权利 / Privacy | FR-12-01 ~ FR-12-22 | 22 | 14 |
| FR-13 合规包 / Compliance Pack | FR-13-01 ~ FR-13-12 | 12 | 10 |
| FR-14 生成式 AI 合规 / GenAI | FR-14-01 ~ FR-14-10 | 10 | 6 |
| FR-15 可观测性 / Observability | FR-15-01 ~ FR-15-08 | 8 | 5 |
| FR-16 限流配额 / Rate Limiting | FR-16-01 ~ FR-16-05 | 5 | 4 |
| **合计 / Total** | | **207** | **141** |

### C. 合规交付物与基线包索引 / Compliance Deliverables & Baseline Index

| 类别 / Category | 编号 / IDs | 位置 / Location |
| --- | --- | --- |
| 合规交付物 / Deliverables | DL-1 ~ DL-10 | §8.3 |
| 合规基线包 / Baseline packs | BL-1 ~ BL-7 | §8.2 |
| 复测触发条件 / Retest triggers | 8 项 | §8.4 |
| 漏洞修复 SLA / Vulnerability SLA | 4 级 | §8.4 |

### D. 参考来源 / Reference

- 滚水科技「企业知识库 RAG 解决方案」产品页 — https://boilingwater.cn/solutions/enterprise-kb-rag
  （本 PRD 中所有引用自该页面的指标均为厂商自述，未经独立验证 / All figures cited from this page are vendor-stated and independently unverified.）

---

*文档结束 / End of document*
