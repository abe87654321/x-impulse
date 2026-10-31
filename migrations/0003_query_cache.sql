-- 跨副本共享的检索缓存 —— FR-04-14a / A-16。
-- TD-01 §10：tenant_id NOT NULL；无软删除列；无原生 ENUM。
-- query_cache_chunks 是 chunk_id → cache_key 的反向映射，供删除定点失效。
CREATE TABLE IF NOT EXISTS query_cache (
    tenant_id       text        NOT NULL REFERENCES tenants(tenant_id),
    cache_key       text        NOT NULL,
    chunk_ids       text[]      NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, cache_key)
);

CREATE TABLE IF NOT EXISTS query_cache_chunks (
    tenant_id       text        NOT NULL,
    chunk_id        text        NOT NULL,
    cache_key       text        NOT NULL,
    PRIMARY KEY (tenant_id, chunk_id, cache_key),
    FOREIGN KEY (tenant_id, cache_key)
        REFERENCES query_cache(tenant_id, cache_key) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_qcache_chunk ON query_cache_chunks (tenant_id, chunk_id);
