# 备份 manifest / Backup Manifest

`backup_manifest.jsonl` 是备份系统与轮转/恢复演练之间的契约文件。
**一行一个 JSON 对象**，每行描述一个备份代次。

## 字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `backup_id` | 是 | 备份代次唯一标识 |
| `tenant_id` | 视 scope | `scope=tenant` 时必填；`shared` / `global` 可为 null |
| `created_at` | 是 | ISO 时间，建议带时区 |
| `expires_at` | 是 | ISO 时间；到期后由轮转任务处理 |
| `scope` | 是 | `tenant` / `shared` / `global` |
| `encryption_key_ref` | 否 | 备份加密密钥引用（只存引用，不存密钥） |
| `status` | 否 | 默认 `available` |

## 生成 / 追加 / 校验

```bash
# 为你的租户初始化 3 条示例代次
.venv/bin/python -m kb.backup_manifest init \
  --path config/backup_manifest.jsonl \
  --tenant default --count 3 --rotation-days 90

# 每次备份完成后追加一条（backup_id 不传则按时间生成）
.venv/bin/python -m kb.backup_manifest append \
  --path config/backup_manifest.jsonl \
  --tenant default --rotation-days 90 \
  --encryption-key-ref kms://backup-key-1

# 校验格式
.venv/bin/python -m kb.backup_manifest validate \
  --path config/backup_manifest.jsonl
```

## 在演练中使用

```bash
.venv/bin/python -m kb.backup_drill \
  --manifest config/backup_manifest.jsonl \
  --manifest-tenant default \
  --tenant drill_restore
```

- `--manifest-tenant` 是 manifest 里的真实租户，用于轮转视图
- `--tenant` 是恢复演练租户，必须以 `drill_` 开头，只清理该租户数据
- 不加 `--regenerate` 时**不会覆盖已有 manifest**；文件不存在才自动生成演示数据

## 注意

- 本文件里的是**示例数据**，不是真实备份。真实备份清单应由备份任务生成。
- manifest 模式的 `rotate` 只报告到期，不删除真实介质；删除由对象存储生命周期/备份系统执行。
- 不要把真实备份密钥写进 manifest，只写密钥引用。

## S3 兼容对象存储目录（`catalog_mode=s3_api`）

每个租户使用独立前缀，备份任务必须按下面的约定写入：

```
s3://<bucket>/<prefix>/<tenant_id>/<backup_id>/kb-manifest.json
s3://<bucket>/<prefix>/<tenant_id>/<backup_id>/<其它数据对象...>
```

`kb-manifest.json` 是目录索引，内容与 manifest 行一致：

```json
{
  "backup_id": "b-20261031",
  "tenant_id": "default",
  "created_at": "2026-10-31T00:00:00+00:00",
  "expires_at": "2027-01-29T00:00:00+00:00",
  "scope": "tenant",
  "encryption_key_ref": "kms://backup-key-1",
  "status": "available"
}
```

- `list_generations` 只列当前租户前缀，并且要求 manifest 的 `tenant_id` 与
  `backup_id`（目录名）一致，否则拒绝。
- `expire` 只删除**指定租户 + 指定 backup_id** 前缀下的对象；不会跨租户。
- 凭据只从环境读取：`KB_S3_ENDPOINT`、`KB_S3_REGION`、
  `KB_S3_ACCESS_KEY_ID`、`KB_S3_SECRET_ACCESS_KEY`（可选
  `KB_S3_SESSION_TOKEN`），设置表只存 `catalog_credential_ref` 引用名。
- `expire` 是真实的 `DeleteObjects`；若希望由桶生命周期而非应用删除，
  不要把 `s3_api` 配上自动轮转。删除后仍须保留 A-19 的诚实表述。

## 原文对象存储（可选）

设置 `KB_RAW_OBJECT_STORE=s3` 后，索引流水线会把原文写到
`<prefix>/<tenant_id>/<doc_id>/<content_hash>`，文档级删除会按文档前缀
删除对象及其版本历史。需要：

```
KB_RAW_OBJECT_STORE=s3
KB_RAW_OBJECT_BUCKET=<bucket>
KB_RAW_OBJECT_PREFIX=raw           # 可选
KB_S3_ENDPOINT=https://...
KB_S3_ACCESS_KEY_ID=...
KB_S3_SECRET_ACCESS_KEY=...
```

不配置 `KB_RAW_OBJECT_STORE`（或设为 `none`）时删除证明的 `raw_object`
保持 SKIPPED，不会假装已清理。

## 保留期到期清除（T-3）

由外部调度器调用，不要在应用进程内 sleep 轮询：

```bash
# 先看会删什么
.venv/bin/python -m kb.retention --dry-run

# 执行
.venv/bin/python -m kb.retention
```

需要 `KB_SUPPRESSION_PEPPER`、数据库 DSN，以及 `KB_VECTOR_STORE`
（`memory` 仅开发；生产用 `qdrant` + `KB_QDRANT_URL`）。
删除失败时退出码为 1，供调度器告警。

## 共享检索缓存（多副本）

默认 `KB_QUERY_CACHE=memory` 只适合单进程开发。多副本部署必须：

```bash
KB_QUERY_CACHE=postgres
# 并确保已执行 migrations/0003_query_cache.sql（make migrate）
```

删除链路会通过 `query_cache_chunks` 反向映射按 chunk 定点失效；不配置共享
缓存时，其它副本仍可能命中旧数据。
