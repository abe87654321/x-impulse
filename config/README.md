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
