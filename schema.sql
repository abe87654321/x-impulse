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
    content_hash        text        NOT NULL,           -- SHA-256(规范化正文)，复现校验锚，见 TD-01 §2·5
    parser_version      text        NOT NULL,           -- 解析器版本；它是规范化的输入，变了偏移量就变
    normalization_version text      NOT NULL DEFAULT 'norm-v1', -- 规范化规则集版本，见 TD-01 §2·5.1
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
    char_start      integer     NOT NULL,               -- 相对源文档规范化正文的字符偏移
    char_end        integer     NOT NULL,

    chunker_version text        NOT NULL,               -- 切分【策略】版本（全局），非本行版本
    version         integer     NOT NULL DEFAULT 1,     -- 本 chunk 的内容版本；同一 chunk_id 被重切分/
                                                        -- 重解析覆盖时自增。citations.chunk_version 存的是它
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

CREATE TABLE acl_entries (
    acl_id              text        PRIMARY KEY,
    tenant_id           text        NOT NULL REFERENCES tenants(tenant_id),
    object_type         text        NOT NULL,           -- source | folder | document | field
    object_id           text        NOT NULL,           -- C-7：租户内全局唯一，靠类型前缀保证
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
    group_id        text        NOT NULL,               -- 组或角色的 ID（共用一个命名空间，见 container_type）
    container_type  text        NOT NULL DEFAULT 'group', -- group | role；仅供溯源与审计，
                                                        -- 求值时 group 与 role 同等处理（TD-03 P-3）
    synced_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, member_id, group_id),
    CONSTRAINT ck_memb_ctype CHECK (container_type IN ('group','role'))
);
CREATE INDEX ix_memb_group ON principal_memberships (tenant_id, group_id);

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

CREATE TABLE audit_log (
    log_id              bigserial   PRIMARY KEY,        -- C-5 的唯一例外：只追加序列需单调游标做增量导出
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
 ('eval_case',    '评测集',       'hard_delete', true),
 ('document',     '文档（限制处理）','hard_delete', true);
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
