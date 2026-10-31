-- 管理面设置项 —— 只存管理员显式覆盖的值；推荐默认值由应用求值，不落库。
-- TD-01 §10：tenant_id NOT NULL；无 PII；无字符锚点；无 ACL 否定表达；
-- 无 audience（非知识内容）；变更历史由 audit_log 记录，本表保留 version 与 updated_by。
CREATE TABLE IF NOT EXISTS service_settings (
    tenant_id       text        NOT NULL REFERENCES tenants(tenant_id),
    setting_key     text        NOT NULL,
    value           jsonb       NOT NULL,
    version         integer     NOT NULL DEFAULT 1,
    updated_by      text        NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, setting_key),
    CONSTRAINT ck_service_settings_version CHECK (version > 0)
);
