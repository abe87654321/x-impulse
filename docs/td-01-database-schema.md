# TD-01 数据库物理设计 / Database Physical Design

| 项 | 内容 |
| --- | --- |
| 编号 | TD-01 |
| 版本 | v1.0 |
| 上游 | PRD v1.6 §9 数据模型 · 架构 v1.4 §7.2 预留清单 R-1~R-13 · FR-12-04 删除设计 §14 |
| 目标读者 | **编码 agent** —— 本文档的 DDL 可直接执行 |
| 编号约定 | 引用的 `FR-01-17` / `FR-13-14` 是**架构文档 §8.1 的 Phase 3 提案编号**，尚未并入 PRD。相关字段本期先建、不启用 |
| 数据库 | PostgreSQL 15+（向量索引在外部 Milvus/Qdrant，本库只存 `chunk_id` 映射） |

> **本文档是规格，不是讨论。** 每条约束都可执行或可断言。设计理由只在会被误改的地方给出一行。

---

## 0. 全局约定 / Global Conventions

```
C-1  所有业务表必须有 tenant_id，NOT NULL，**且不设 DEFAULT**          -- R-9
     v1 只有一个租户（其 tenant_id 为 'default'），但列上不给默认值：
     给了默认值，漏传 tenant_id 的写入会静默落到 'default' 租户，
     等到真有第二个租户时变成跨租户数据污染，且无从追溯。显式传入。
     全局字典表例外且仅此两张：deployment_profiles、erasure_location_types
     （它们是产品级配置，跨租户共享；新增例外须经架构评审）
C-2  禁止软删除列（is_deleted / deleted_at 作为可见性开关）          -- 等保 8.1.4.10 剩余信息保护
     例外：erasure_* 与 audit_log 系列，其"删除"由加密擦除实现
C-3  枚举一律用 text + CHECK，禁止 PostgreSQL 原生 ENUM 类型        -- R-6
C-4  所有时间戳用 timestamptz，禁止 timestamp
C-5  主键统一 text（外部可读 ID，形如 'chk_01H...'），非自增整数     -- 便于跨库引用与日志排查
     **例外且仅此一张**：audit_log.log_id 用 bigserial。审计日志是只追加序列，
     需要一个单调递增的游标来做"自某条之后"的增量导出与断点续传，
     text ID 做不到这件事。该例外须在此声明，不得推广到其他表。
C-7  object_id 在单个租户内必须全局唯一、跨对象类型不重复               -- TD-03 P-0
     acl_entries.object_id 是裸 text（无 FK、无 unique，因为它要同时指向
     source / folder / document / field 四类对象），schema 无法强制这一点。
     靠 ID 前缀保证：src_… / fld_… / doc_… / fld… 。
     **求值时 acls 字典只用 object_id 做键**，撞号会让目录的 deny 被当成
     文档的 deny（或反之）—— 连接器做标识映射时必须加类型前缀。
C-6  外键一律 ON DELETE RESTRICT，删除必须走 FR-12-04 编排流程       -- 防止级联删除绕过删除链路记账
```

**C-3 的理由（会被误改，必须写明）**：PostgreSQL 原生 `ENUM` 类型**无法删除值，新增值需 `ALTER TYPE` 且在事务中受限**。R-6 要求 `erasure_scope.location_type` 可扩展 —— 用原生 ENUM 等于自废预留。统一用 `text + CHECK` 保持一致。

---

## 1. 租户与配置 / Tenancy & Configuration

```sql
CREATE TABLE tenants (
    tenant_id       text        PRIMARY KEY,
    display_name    text        NOT NULL,
    profile_id      text        NOT NULL DEFAULT 'PF-1',   -- 部署档案，架构 §7.7
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_tenants_profile CHECK (profile_id IN ('PF-1', 'PF-2'))
);

INSERT INTO tenants (tenant_id, display_name) VALUES ('default', 'Default Tenant');

-- 部署档案：产品档次 + 合规基线的配置载体（FR-13-14 / 架构 §7.7）
CREATE TABLE deployment_profiles (
    profile_id              text        PRIMARY KEY,
    audience_filter         text[]      NOT NULL,            -- 允许放行的 audience 值
    default_trust_floor     text        NOT NULL,
    dr_tier                 text        NOT NULL,
    log_retention_days      integer     NOT NULL,
    content_safety_level    text        NOT NULL,
    baselines               text[]      NOT NULL DEFAULT '{}', -- BL-1..BL-7
    CONSTRAINT ck_profiles_dr CHECK (dr_tier IN ('general', 'mlps3', 'financial'))
);

INSERT INTO deployment_profiles VALUES
 ('PF-1', ARRAY['internal_only','customer_safe'], 'internal',  'general', 180, 'standard', '{}'),
 ('PF-2', ARRAY['customer_safe'],                 'curated', 'financial', 1825, 'strict',   '{}');
```

> **PF-2 的 `audience_filter` 只含 `customer_safe`** —— 这一行是 R-10 受众隔离的执行点。

---

## 2. 知识源与文档 / Sources & Documents

```sql
CREATE TABLE sources (
    source_id           text        PRIMARY KEY,
    tenant_id           text        NOT NULL REFERENCES tenants(tenant_id),
    source_type         text        NOT NULL,           -- confluence | sharepoint | notion | smb | s3 | rdbms | ticket | upload
    display_name        text        NOT NULL,
    config              jsonb       NOT NULL DEFAULT '{}',
    capabilities        jsonb       NOT NULL DEFAULT '{}', -- 连接器能力声明，见 TD-02 §2
    sync_policy         text        NOT NULL DEFAULT 'scheduled',
    sync_interval_sec   integer,
    scope_rules         jsonb       NOT NULL DEFAULT '{}', -- 白/黑名单
    trust_tier          text        NOT NULL DEFAULT 'internal',      -- FR-01-17（Phase 3 启用，字段先建）
    audience            text        NOT NULL DEFAULT 'internal_only', -- FR-01-18 / R-10
    status              text        NOT NULL DEFAULT 'active',
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_sources_sync  CHECK (sync_policy IN ('realtime','scheduled','manual')),
    CONSTRAINT ck_sources_trust CHECK (trust_tier IN ('authoritative','curated','internal','user_generated','external')),
    CONSTRAINT ck_sources_aud   CHECK (audience   IN ('internal_only','customer_safe')),
    CONSTRAINT ck_sources_state CHECK (status     IN ('active','paused','error','disabled'))
);
CREATE INDEX ix_sources_tenant ON sources (tenant_id, status);

CREATE TABLE documents (
    doc_id              text        PRIMARY KEY,
    tenant_id           text        NOT NULL REFERENCES tenants(tenant_id),
    source_id           text        NOT NULL REFERENCES sources(source_id),
    external_id         text        NOT NULL,           -- 源系统稳定标识，R-8
    title               text        NOT NULL,
    url                 text,
    author              text,
    doc_type            text,
    department          text,
    version             text        NOT NULL DEFAULT '1',
    content_hash        text        NOT NULL,           -- 全文规范化后的哈希，用于变更检测与证据快照
    audience            text        NOT NULL DEFAULT 'internal_only', -- 可覆盖 source 级
    trust_tier          text,                                          -- NULL = 继承 source
    effective_until     timestamptz,
    source_created_at   timestamptz,
    source_updated_at   timestamptz,
    ingested_at         timestamptz NOT NULL DEFAULT now(),
    status              text        NOT NULL DEFAULT 'indexed',
    CONSTRAINT ck_docs_aud   CHECK (audience IN ('internal_only','customer_safe')),
    CONSTRAINT ck_docs_state CHECK (status   IN ('parsing','indexed','failed','restricted','taken_down')),
    CONSTRAINT uq_docs_ext   UNIQUE (source_id, external_id)
);
CREATE INDEX ix_docs_tenant_src ON documents (tenant_id, source_id, status);
CREATE INDEX ix_docs_updated    ON documents (tenant_id, source_updated_at DESC);
CREATE INDEX ix_docs_effective  ON documents (tenant_id, effective_until) WHERE effective_until IS NOT NULL;
```

> `status = 'restricted'` 是 FR-12-18 限制处理态：**存储保留，但不得进入检索**。所有检索查询必须过滤 `status = 'indexed'`。

---

## 2·5. 规范化正文 / Normalized Body Text（偏移量的基准，必须先定）

`chunks.char_start / char_end` 与 `documents.content_hash` 都声明"相对**规范化正文**"，
而 I-7 同时声明这两项**事后无法补**。既然如此，**"规范化"必须是一个确定的、
可复现的、带版本号的规则集** —— 否则这条不变量是"声明了但不可验证"：
换一个实现、或同一实现升级一次依赖库，全部历史引用就静默指向错误的位置，
而没有任何东西会报错。

### 2·5.1 规则集 `norm-v1`（确定且幂等）

**版本标识符就是字符串 `norm-v1`** —— 它是 `documents.normalization_version` 的取值，不是泛指。实现时必须用这个字面量，改规则必须换标识符（见 R-N4）。

按**顺序**执行，每一步都必须幂等：

```
N-1  Unicode 规范化：NFC。
     ★ 禁止 NFKC。NFKC 会把全角折成半角（"ＡＢ１" → "AB1"）、
     折叠兼容字符与连字。对中文文档这会直接改变字符数，
     使同一份文档在两种实现下产出完全不同的偏移量。
     NFC 是幂等的、且保留原文字形。

N-2  换行统一为 LF：CRLF → LF，单独的 CR → LF。

N-3  删除零宽与不可见控制字符：
     U+200B ZWSP、U+200C ZWNJ、U+200D ZWJ、U+FEFF BOM、U+00AD SOFT HYPHEN、
     以及 U+0000–U+0008、U+000B、U+000C、U+000E–U+001F。
     （保留 U+0009 TAB 与 U+000A LF。）

N-4  **不折叠空白。** 不把连续空格/制表符压成一个，不去首尾空白。
     理由：折叠规则的任何差异都会改变偏移量，而折叠本身对检索质量
     几乎没有贡献 —— 它属于切分与索引阶段的处理，不属于偏移量基准。

N-5  **不做任何内容性删除**：不去页眉页脚、不去水印、不去目录。
     这些由解析器决定并体现在"解析器输出"里，不属于规范化。
```

### 2·5.2 偏移量只在三元组下有意义

规范化正文**不落库**（落库等于多出一处 PII 明文副本，会把 FR-12-04 的
十处清除变成十一处）。因此偏移量的可解释性靠**可复现**保证：

```
R-N1  documents 必须记录 parser_version 与 normalization_version。
      解析器输出是规范化的输入，它变了偏移量一样会变 —— 两个版本号都要记。

R-N2  documents.content_hash = SHA-256(规范化正文)。
      它是**复现校验锚**：按记录的 (parser_version, normalization_version)
      从留存的原文重新生成，哈希对得上，偏移量才可信。

R-N3  哈希对不上时，该文档上所有 citation 的字符偏移量必须被标记为
      **不可验证**，而不是当作正确。页码/条款号等粗粒度锚点仍可用。

R-N4  规则集变更必须升 normalization_version 并记入迁移文件，
      **不得就地修改 v1 的规则**。历史文档保留其原有版本号；
      要让旧文档获得新偏移量，只能重新解析并生成新的 chunk 与 citation。

R-N5  主信任锚是 citations.chunk_content_hash（FR-05-20），不是字符偏移量。
      偏移量是定位便利；内容哈希才是"当时依据了什么"的证据。
      这个分层是有意的 —— 偏移量脆弱，哈希不脆弱。
```

---

## 3. 切分与锚点 / Chunks & Anchors

```sql
CREATE TABLE chunks (
    chunk_id        text        PRIMARY KEY,
    tenant_id       text        NOT NULL REFERENCES tenants(tenant_id),
    doc_id          text        NOT NULL REFERENCES documents(doc_id),
    seq             integer     NOT NULL,
    content         text        NOT NULL,
    content_hash    text        NOT NULL,               -- R-11 证据快照锚
    token_count     integer     NOT NULL,

    -- 位置锚点（FR-02-09 + FR-02-13 / R-1）
    page_no         integer,
    section_path    text,
    clause_no       text,
    char_start      integer     NOT NULL,               -- 相对规范化正文的字符偏移，基准见 §2·5
    char_end        integer     NOT NULL,

    chunker_version text        NOT NULL,               -- 切分策略版本；重切分时用于识别
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_chunks_span CHECK (char_end > char_start),
    CONSTRAINT uq_chunks_seq  UNIQUE (doc_id, seq)
);
CREATE INDEX ix_chunks_doc    ON chunks (doc_id, seq);
CREATE INDEX ix_chunks_tenant ON chunks (tenant_id);

-- 向量索引在外部向量库；本表只记映射与物理删除状态（FR-12-05 双时点）
CREATE TABLE chunk_vectors (
    chunk_id            text        PRIMARY KEY REFERENCES chunks(chunk_id),
    tenant_id           text        NOT NULL REFERENCES tenants(tenant_id),
    model_name          text        NOT NULL,
    model_version       text        NOT NULL,
    vector_store        text        NOT NULL,           -- milvus | qdrant
    collection          text        NOT NULL,
    indexed_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_cv_model ON chunk_vectors (tenant_id, model_name, model_version);

-- R-2 旁路标注：结构先建，v1 不写入
-- 锚定到「文档 + 字符跨度」，不锚定 chunk —— 重新切分不丢标注
CREATE TABLE annotations (
    annotation_id   text        PRIMARY KEY,
    tenant_id       text        NOT NULL REFERENCES tenants(tenant_id),
    doc_id          text        NOT NULL REFERENCES documents(doc_id),
    char_start      integer     NOT NULL,
    char_end        integer     NOT NULL,
    kind            text        NOT NULL,               -- pii | entity_mention | sensitivity | review_state
    payload         jsonb       NOT NULL DEFAULT '{}',
    confidence      real,
    created_by      text        NOT NULL,               -- model:<name>@<ver> | user:<id>
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_ann_span CHECK (char_end > char_start)
);
CREATE INDEX ix_ann_doc_span ON annotations (doc_id, char_start, char_end);
```

> **`annotations` 锚定文档跨度而非 chunk_id** —— 这是 R-2 的全部要点。若锚 chunk，改切分策略即毁全部标注，而切分策略在调优期会反复改。

---

## 4. 访问控制 / Access Control

```sql
CREATE TABLE acl_entries (
    acl_id              text        PRIMARY KEY,
    tenant_id           text        NOT NULL REFERENCES tenants(tenant_id),
    object_type         text        NOT NULL,           -- source | folder | document | field
    object_id           text        NOT NULL,
    principal_type      text        NOT NULL,           -- user | group | role | everyone
    principal_id        text        NOT NULL,           -- everyone 时填 '*'
    effect              text        NOT NULL,           -- allow | deny        ★ FR-01-20 / FR-06-28
    permission          text        NOT NULL DEFAULT 'read',
    scope_level         text        NOT NULL,           -- 与 object_type 同义，冗余以便求值时无需 join
    inherited_from      text,                           -- 上级对象 ID；NULL = 本级直接设置
    inheritance_broken  boolean     NOT NULL DEFAULT false,  -- 该对象是否断开了向上继承
    source_id           text        NOT NULL REFERENCES sources(source_id),
    synced_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_acl_effect CHECK (effect         IN ('allow','deny')),
    CONSTRAINT ck_acl_ptype  CHECK (principal_type IN ('user','group','role','everyone')),
    CONSTRAINT ck_acl_otype  CHECK (object_type    IN ('source','folder','document','field')),
    CONSTRAINT ck_acl_scope  CHECK (scope_level    IN ('source','folder','document','field'))
);
CREATE INDEX ix_acl_object    ON acl_entries (tenant_id, object_type, object_id);
CREATE INDEX ix_acl_principal ON acl_entries (tenant_id, principal_type, principal_id, effect);
-- deny 查询是求值热路径（任一 deny 即拒绝），单独建部分索引
CREATE INDEX ix_acl_deny      ON acl_entries (tenant_id, object_id, principal_id) WHERE effect = 'deny';

-- 组成员关系快照（求值时展开；实时二次校验见 FR-06-03）
CREATE TABLE principal_memberships (
    tenant_id       text        NOT NULL REFERENCES tenants(tenant_id),
    member_id       text        NOT NULL,               -- user 或嵌套 group
    group_id        text        NOT NULL,
    synced_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, member_id, group_id)
);
CREATE INDEX ix_memb_group ON principal_memberships (tenant_id, group_id);
```

> **`ix_acl_deny` 部分索引不是优化，是语义**：求值算法第一步就是"任一 deny 命中即拒绝"（TD-03 §3），这条路径必须廉价。

---

## 5. 会话、答案与引用 / Sessions, Answers, Citations

```sql
CREATE TABLE sessions (
    session_id      text        PRIMARY KEY,
    tenant_id       text        NOT NULL REFERENCES tenants(tenant_id),
    principal_id    text        NOT NULL,
    channel         text        NOT NULL,               -- web | wecom | dingtalk | slack | api | mcp
    started_at      timestamptz NOT NULL DEFAULT now(),
    last_active_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_sessions_principal ON sessions (tenant_id, principal_id, last_active_at DESC);

CREATE TABLE queries (
    query_id            text        PRIMARY KEY,
    tenant_id           text        NOT NULL REFERENCES tenants(tenant_id),
    session_id          text        NOT NULL REFERENCES sessions(session_id),
    principal_id        text        NOT NULL,           -- 问责主体（人）
    actor_id            text,                           -- 代劳 Agent；NULL = 人直接提问
    assurance_level     text,                           -- L0..L3，架构 §5.3
    channel             text        NOT NULL,

    -- FR-09-01 × FR-12-16：默认不落原文
    raw_text            text,                           -- 默认 NULL；仅当 profile 允许留存时写入
    text_hash           text        NOT NULL,           -- 归一化后哈希，恒有
    text_length         integer     NOT NULL,
    lang                text,
    intent_class        text,
    rewritten_text      text,                           -- 改写后查询，同受留存策略管辖

    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_queries_assur CHECK (assurance_level IS NULL OR assurance_level IN ('L0','L1','L2','L3'))
);
CREATE INDEX ix_queries_principal ON queries (tenant_id, principal_id, created_at DESC);
CREATE INDEX ix_queries_actor     ON queries (tenant_id, actor_id, created_at DESC) WHERE actor_id IS NOT NULL;

CREATE TABLE answers (
    answer_id       text        PRIMARY KEY,
    tenant_id       text        NOT NULL REFERENCES tenants(tenant_id),
    query_id        text        NOT NULL REFERENCES queries(query_id),
    text            text        NOT NULL,
    confidence      real,
    refused         boolean     NOT NULL DEFAULT false,
    refusal_reason  text,
    model_name      text        NOT NULL,
    model_version   text        NOT NULL,
    latency_ms      integer     NOT NULL,
    ttft_ms         integer,                            -- 首字延迟，NFR-P-2
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_answers_query ON answers (query_id);

CREATE TABLE citations (
    citation_id         text        PRIMARY KEY,
    tenant_id           text        NOT NULL REFERENCES tenants(tenant_id),
    answer_id           text        NOT NULL REFERENCES answers(answer_id),
    sentence_index      integer     NOT NULL,
    chunk_id            text        REFERENCES chunks(chunk_id),  -- 可为 NULL：chunk 已删除后引用悬空
    doc_id              text,

    -- 结构化 locator（评审 M-5；与 FR-02-13、架构 §4.3 对齐）
    page_no             integer,
    section_path        text,
    clause_no           text,
    char_start          integer,
    char_end            integer,

    -- R-11 证据快照：即使 chunk 被删或改，仍可证明"当时依据了什么"
    chunk_content_hash  text        NOT NULL,
    chunk_version       text        NOT NULL,
    doc_version         text        NOT NULL,

    target_type         text        NOT NULL DEFAULT 'chunk',   -- R-5 多态：chunk | entity | relation
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_cit_target CHECK (target_type IN ('chunk','entity','relation'))
);
CREATE INDEX ix_cit_answer ON citations (answer_id, sentence_index);
CREATE INDEX ix_cit_chunk  ON citations (chunk_id) WHERE chunk_id IS NOT NULL;
```

> **`citations.chunk_id` 允许 NULL 而 `chunk_content_hash` NOT NULL** —— chunk 被删除后引用悬空，但快照哈希必须留存（R-11）。UI 显示「引用来源已依法删除」，不报错。

---

## 6. 派生资产 / Derived Assets

删除链路的位置 9、10 —— 产品自身功能把内容复制出主链路之处。

```sql
CREATE TABLE curated_qa (
    qa_id           text        PRIMARY KEY,
    tenant_id       text        NOT NULL REFERENCES tenants(tenant_id),
    question_pattern text       NOT NULL,
    answer_text     text        NOT NULL,
    citations       jsonb       NOT NULL DEFAULT '[]',
    owner_id        text        NOT NULL,
    effective_until timestamptz,
    review_state    text        NOT NULL DEFAULT 'active',
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_cqa_state CHECK (review_state IN ('active','needs_review','retired'))
);

CREATE TABLE eval_sets (
    set_id      text        PRIMARY KEY,
    tenant_id   text        NOT NULL REFERENCES tenants(tenant_id),
    name        text        NOT NULL,
    version     text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE eval_cases (
    case_id             text        PRIMARY KEY,
    tenant_id           text        NOT NULL REFERENCES tenants(tenant_id),
    set_id              text        NOT NULL REFERENCES eval_sets(set_id),
    question            text        NOT NULL,
    expected_answer     text,
    expected_chunk_ids  text[]      NOT NULL DEFAULT '{}',
    scenario            text,
    pii_redacted        boolean     NOT NULL DEFAULT false,  -- FR-10-07 强制脱敏
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_eval_redacted CHECK (pii_redacted = true)  -- 未脱敏不得入库
);
```

> **`ck_eval_redacted` 是数据库层的强制**。FR-10-07 要求入集前脱敏 —— 靠代码自觉会漏，靠 CHECK 不会。

---

## 7. 审计 / Audit

```sql
CREATE TABLE audit_log (
    log_id              bigserial   PRIMARY KEY,
    tenant_id           text        NOT NULL,
    ts                  timestamptz NOT NULL DEFAULT now(),
    category            text        NOT NULL,           -- query | retrieval | answer | admin | acl | erasure
    principal_id        text,
    actor_id            text,
    delegation_chain    text[]      NOT NULL DEFAULT '{}',
    assurance_level     text,
    action              text        NOT NULL,
    target_type         text,
    target_id           text,
    payload             jsonb       NOT NULL DEFAULT '{}',   -- PII 字段以 subject_key 加密存放
    prev_hash           text,
    row_hash            text        NOT NULL,               -- FR-09-09 哈希链
    CONSTRAINT ck_audit_cat CHECK (category IN ('query','retrieval','answer','admin','acl','erasure','security'))
);
CREATE INDEX ix_audit_ts        ON audit_log (tenant_id, ts DESC);
CREATE INDEX ix_audit_principal ON audit_log (tenant_id, principal_id, ts DESC);
CREATE INDEX ix_audit_target    ON audit_log (tenant_id, target_type, target_id);

REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;   -- FR-09-09：写入后不可改
```

> 审计日志的"删除"由加密擦除实现（删除设计 §6）：`payload` 中的 PII 用主体密钥加密，销毁密钥即不可读，**哈希链与留存义务均不受影响**。

---

## 8. 删除与合规 / Erasure & Compliance

```sql
-- 位置类型登记表 —— R-6 可扩展的执行方式
CREATE TABLE erasure_location_types (
    location_type   text        PRIMARY KEY,
    display_name    text        NOT NULL,
    purge_method    text        NOT NULL,               -- hard_delete | crypto_shred | redact
    is_active       boolean     NOT NULL DEFAULT true,
    CONSTRAINT ck_elt_method CHECK (purge_method IN ('hard_delete','crypto_shred','redact'))
);

INSERT INTO erasure_location_types VALUES
 ('raw_object',   '原文缓存',     'hard_delete', true),
 ('chunk',        'Chunk 存储',   'hard_delete', true),
 ('vector',       '向量索引',     'hard_delete', true),
 ('cache',        '检索缓存',     'hard_delete', true),
 ('session',      '会话历史',     'redact',      true),
 ('answer',       '答案与引用',   'redact',      true),
 ('audit',        '审计日志',     'crypto_shred',true),
 ('backup',       '备份与归档',   'crypto_shred',true),
 ('curated_qa',   '精选问答',     'redact',      true),
 ('eval_case',    '评测集',       'hard_delete', true);
-- 将来新增位置（如实体表）只需 INSERT 一行，无需改表结构或删除编排代码

CREATE TABLE subject_index (            -- 个人信息主体（HMAC，不可枚举）—— FR-12-19 / A-8
    tenant_id       text        NOT NULL REFERENCES tenants(tenant_id),
    subject_key     text        NOT NULL,               -- HMAC-SM3(normalized_pii, pepper)
    subject_type    text        NOT NULL,
    location_type   text        NOT NULL REFERENCES erasure_location_types(location_type),
    location_id     text        NOT NULL,
    confidence      real        NOT NULL,
    norm_version    text        NOT NULL,               -- 归一化规则版本；变更须重建
    first_seen_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, subject_key, location_type, location_id)
);
CREATE INDEX ix_subj_key ON subject_index (tenant_id, subject_key);

CREATE TABLE business_entity_index (    -- 业务实体（明文，可枚举可关联）—— A-8 分表
    tenant_id       text        NOT NULL REFERENCES tenants(tenant_id),
    entity_key      text        NOT NULL,               -- 规范化名称，明文
    entity_type     text        NOT NULL,               -- org | product | contract | policy
    canonical_id    text,                               -- R-3 预留：实体消歧后的主 ID
    alias_of        text,                               -- R-3 预留
    location_type   text        NOT NULL REFERENCES erasure_location_types(location_type),
    location_id     text        NOT NULL,
    confidence      real        NOT NULL,
    PRIMARY KEY (tenant_id, entity_key, entity_type, location_type, location_id)
);

CREATE TABLE subject_keys (             -- 加密擦除的密钥引用 —— 删除设计 §6
    tenant_id           text        NOT NULL REFERENCES tenants(tenant_id),
    subject_key         text        NOT NULL,
    dek_ref             text        NOT NULL,           -- 密码模块中的 DEK 句柄，非密钥本身
    kek_ref             text        NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    destroyed_at        timestamptz,
    destruction_evidence text,                          -- 密码模块出具的销毁凭证
    PRIMARY KEY (tenant_id, subject_key)
);

CREATE TABLE erasure_requests (
    request_id      text        PRIMARY KEY,
    tenant_id       text        NOT NULL REFERENCES tenants(tenant_id),
    trigger_type    text        NOT NULL,               -- T1 | T2 | T3 | T4
    subject_key     text,                               -- HMAC；T2/T3/T4 可为 NULL
    requester_verified boolean  NOT NULL DEFAULT false,
    state           text        NOT NULL DEFAULT 'RECEIVED',
    received_at     timestamptz NOT NULL DEFAULT now(),
    deadline_at     timestamptz NOT NULL,
    completed_at    timestamptz,
    CONSTRAINT ck_er_trigger CHECK (trigger_type IN ('T1','T2','T3','T4')),
    CONSTRAINT ck_er_state   CHECK (state IN
        ('RECEIVED','VERIFYING','SCOPING','SCOPED','EXECUTING','PARTIAL_FAILED',
         'VERIFYING2','RESTRICTED','CERTIFIED','CLOSED','REJECTED'))
);
CREATE INDEX ix_er_state ON erasure_requests (tenant_id, state, deadline_at);

CREATE TABLE erasure_scopes (
    scope_id                text        PRIMARY KEY,
    tenant_id               text        NOT NULL REFERENCES tenants(tenant_id),
    request_id              text        NOT NULL REFERENCES erasure_requests(request_id),
    location_type           text        NOT NULL REFERENCES erasure_location_types(location_type),
    location_id             text        NOT NULL,
    action                  text        NOT NULL,       -- purge | crypto_shred | restrict | redact
    status                  text        NOT NULL DEFAULT 'PENDING',
    attempts                integer     NOT NULL DEFAULT 0,
    logical_completed_at    timestamptz,                -- ★ 评审 C-D-02：逻辑删除完成
    physical_completed_at   timestamptz,                -- ★ 物理移除完成（如向量库 compaction）
    last_error              text,
    CONSTRAINT ck_es_status CHECK (status IN ('PENDING','LOGICAL_DONE','PHYSICAL_DONE','FAILED','SKIPPED')),
    CONSTRAINT ck_es_action CHECK (action IN ('purge','crypto_shred','restrict','redact')),
    -- 物理完成必然晚于或等于逻辑完成
    CONSTRAINT ck_es_order CHECK (
        physical_completed_at IS NULL
        OR (logical_completed_at IS NOT NULL AND physical_completed_at >= logical_completed_at))
);
CREATE UNIQUE INDEX uq_es_target ON erasure_scopes (request_id, location_type, location_id);

CREATE TABLE suppression_list (         -- 墓碑，FR-12-17
    suppression_id  text        PRIMARY KEY,
    tenant_id       text        NOT NULL REFERENCES tenants(tenant_id),
    scope_type      text        NOT NULL,               -- document | subject | chunk_hash
    scope_key       text        NOT NULL,               -- HMAC，不存明文
    reason          text        NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz,                        -- NULL = 永久
    source_confirmed boolean    NOT NULL DEFAULT false,
    CONSTRAINT ck_sl_scope CHECK (scope_type IN ('document','subject','chunk_hash'))
);
CREATE UNIQUE INDEX uq_sl_key ON suppression_list (tenant_id, scope_type, scope_key);

CREATE TABLE restriction_holds (        -- FR-12-18 限制处理
    hold_id         text        PRIMARY KEY,
    tenant_id       text        NOT NULL REFERENCES tenants(tenant_id),
    location_type   text        NOT NULL REFERENCES erasure_location_types(location_type),
    location_id     text        NOT NULL,
    reason          text        NOT NULL,               -- statutory_retention | legal_hold | technical
    statutory_until timestamptz,
    auto_purge_scheduled boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_rh_until ON restriction_holds (tenant_id, statutory_until)
    WHERE auto_purge_scheduled = true;

CREATE TABLE erasure_certificates (
    cert_id                     text        PRIMARY KEY,
    tenant_id                   text        NOT NULL REFERENCES tenants(tenant_id),
    request_id                  text        NOT NULL REFERENCES erasure_requests(request_id),
    scope_summary               jsonb       NOT NULL,
    method_statement            text        NOT NULL,   -- 核查范围与方法
    uncovered_scope_statement   text        NOT NULL,   -- ★ FR-12-05：不得为空
    executor_id                 text        NOT NULL,
    reviewer_id                 text        NOT NULL,
    issued_at                   timestamptz NOT NULL DEFAULT now(),
    cert_hash                   text        NOT NULL,
    CONSTRAINT ck_cert_uncovered CHECK (length(trim(uncovered_scope_statement)) > 0)
);
```

> **`ck_cert_uncovered` 强制"未覆盖范围声明"非空** —— 一份声称 100% 彻底删除的证明，在核查发现残留时会从有利证据变成不利证据（删除设计 §11）。数据库层拦住比靠流程规范可靠。

---

## 9. 反馈与同步记录 / Feedback & Sync Runs

```sql
CREATE TABLE feedback (
    feedback_id text        PRIMARY KEY,
    tenant_id   text        NOT NULL REFERENCES tenants(tenant_id),
    answer_id   text        NOT NULL REFERENCES answers(answer_id),
    principal_id text       NOT NULL,
    rating      smallint    NOT NULL,                   -- 1 = up, -1 = down
    reason      text,
    status      text        NOT NULL DEFAULT 'open',
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_fb_rating CHECK (rating IN (1, -1)),
    CONSTRAINT ck_fb_status CHECK (status IN ('open','assigned','resolved','wontfix'))
);

CREATE TABLE sync_runs (
    run_id          text        PRIMARY KEY,
    tenant_id       text        NOT NULL REFERENCES tenants(tenant_id),
    source_id       text        NOT NULL REFERENCES sources(source_id),
    mode            text        NOT NULL,               -- full | incremental | webhook
    cursor_before   text,
    cursor_after    text,
    docs_added      integer     NOT NULL DEFAULT 0,
    docs_updated    integer     NOT NULL DEFAULT 0,
    docs_deleted    integer     NOT NULL DEFAULT 0,
    docs_suppressed integer     NOT NULL DEFAULT 0,     -- 被抑制清单挡下的数量
    docs_failed     integer     NOT NULL DEFAULT 0,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    status          text        NOT NULL DEFAULT 'running',
    error           text,
    CONSTRAINT ck_sr_status CHECK (status IN ('running','succeeded','failed','cancelled'))
);
CREATE INDEX ix_sr_source ON sync_runs (tenant_id, source_id, started_at DESC);
```

> `docs_suppressed` 是 FR-15-05 的告警数据源：**抑制清单命中数异常增长可能是删除滥用**。

---

## 10. 六问清单逐表核对 / Schema Self-Check (架构 §7.8)

| 表 | ①tenant_id | ②PII | ③字符锚点 | ④否定表达 | ⑤audience | ⑥历史证明 |
| --- | --- | --- | --- | --- | --- | --- |
| `sources` | ✅ | — | — | 由 acl_entries 承载 | ✅ | — |
| `documents` | ✅ | 经 subject_index 登记 | 见 chunks | 同上 | ✅ | `content_hash` + `version` |
| `chunks` | ✅ | ✅ 登记 | ✅ `char_start/end` | — | 继承 doc | `content_hash` |
| `annotations` | ✅ | ✅ | ✅ | — | 继承 doc | — |
| `acl_entries` | ✅ | — | — | ✅ **`effect`** | — | — |
| `queries` | ✅ | ✅（默认不落原文） | — | — | — | `text_hash` |
| `answers` | ✅ | ✅ 登记 | — | — | — | 经 citations |
| `citations` | ✅ | — | ✅ | — | — | ✅ **`chunk_content_hash`+`chunk_version`** |
| `curated_qa` | ✅ | ✅ 登记 | — | — | — | — |
| `eval_cases` | ✅ | ✅ 强制脱敏 | — | — | — | — |
| `audit_log` | ✅ | ✅ 加密擦除 | — | — | — | `row_hash` 链 |
| `erasure_*` | ✅ | ✅ HMAC | — | — | — | 证明归档 |
| `suppression_list` | ✅ | ✅ HMAC | — | — | — | — |
| `service_settings` | ✅ | — | — | — | — | `version` + `updated_by`，变更入 audit_log |

**未通过项：0。**

---

## 11. 预留项落地核对 / Reservation Coverage

| 预留 | 落地位置 |
| --- | --- |
| R-1 字符锚点 | `chunks.char_start/char_end`、`citations.char_start/char_end` |
| R-2 旁路标注 | `annotations`（结构已建，v1 不写入） |
| R-3 实体索引预留字段 | `business_entity_index.canonical_id / alias_of` |
| R-5 Citation 多态 | `citations.target_type` |
| R-6 可扩展删除位置 | `erasure_location_types` 表 + text/CHECK（非原生 ENUM） |
| R-8 稳定文档标识 | `documents.external_id` + `uq_docs_ext` |
| R-9 全表 tenant_id | 所有业务表 |
| R-10 受众标签 | `sources.audience`、`documents.audience`、`deployment_profiles.audience_filter` |
| R-11 证据快照 | `citations.chunk_content_hash / chunk_version / doc_version` |
| A-8 实体分表 | `subject_index`（HMAC）/ `business_entity_index`（明文） |

R-4（检索契约结构化）、R-7（MCP 能力协商）、R-12（服务无状态）、R-13（组件选型）不落在 schema，见 TD-02 与架构文档。

---

## 12. 迁移与冻结 / Migration & Freeze

```
M-1  本 DDL 为 Phase 1 基线，编号 0001_baseline.sql
M-2  后续变更一律用增量迁移文件，禁止修改基线文件
M-3  新增表必须先过 §10 六问清单，未过不得合入
M-4  任何 ALTER 涉及 tenant_id / acl_entries.effect / char_start / audience
     / content_hash 五类字段的，须经架构评审 —— 它们是安全或不可逆预留
```
