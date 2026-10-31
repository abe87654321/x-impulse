# 交接检查点 / Checkpoint

| | |
| --- | --- |
| 保存时间 | 2026-10-31T08:25:00+00:00 |
| HEAD | `45f910332a4b` 实现流式回答、限流与备份/KMS 合规链路 |
| 工作区 | 干净；仅未跟踪 `.claude/`（本地技能副本，未纳入版本库） |
| 单元测试 | 实跑：`269 passed, 2 skipped, 22 deselected`（integration 之外） |
| 集成测试 | 实跑：`22 passed`（需 `make up` / PostgreSQL） |
| git | 已 push；`origin/master` 跟踪正常 |

## 1. 手头这一步

- **目标**：下个会话从“真实 KMS 适配器（Vault Transit）”开始。
- **验收**：新增适配器与假传输测试通过；密钥销毁后 `unwrap` 失败；KMS 不可用时 fail-closed；不把凭据写进设置表或代码。
- **进度**：本轮全部已提交并推送。当前无未完成代码。

## 2. 试过并否决的

| 方案 | 为什么否决 |
| --- | --- |
| 直接修改 `schema.sql` 加设置表 | 违反 TD-01 §12 M-2；已改用增量迁移 `migrations/0002_service_settings.sql` |
| Agent 通过管理 API 直接执行设置变更 | 违反 A-13 / NG-4 / NG-7；只保留 proposals，PUT 对 agent 返回 403 |
| 备份轮转用单一长期密钥 | 无法做主体级加密擦除；策略层拒绝 `single_long_lived` |
| 备份权威状态只放业务库 | 恢复旧备份会回滚删除状态；策略层拒绝 `authority_store=db_only` |
| 恢复演练自动删除数据 | 风险过高；`RestoreGuard` 只扫描并阻断，清理在隔离环境执行 |
| manifest 模式声称已物理删除介质 | manifest 只读/生命周期外部执行；文案明确只报告到期 |
| 先接真实备份目录 API | 需要用户先提供 API/凭据/租户隔离方式，当前阻塞；故先做 KMS |

## 3. 卡住的决策

| 编号 | 问题 | 在等谁 |
| --- | --- | --- |
| Q-16 | 重排模型与 Top-K 默认值；需 FR-04-17 真实评测基线 | 算法 |
| E-1 | 加密擦除是否被客户合规官接受 | 客户合规 + 法务 |
| E-3 | 备份轮转周期具体取值 | 客户基础设施 + 合规 |
| A-19 | 真实备份系统 API/凭据/租户隔离方式 | 用户/客户基础设施 |

## 4. 下一步

**第一优先：Vault Transit KMS 适配器**

```bash
# 构建带 KMS 的镜像/环境不在本期；先做适配器与测试
.venv/bin/pytest tests/compliance/test_kms.py -q

# 实现后跑完整自检
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest -m "not integration" -q
DATABASE_URL=postgresql://kb:dev@localhost/kb .venv/bin/pytest -m integration -q
```

实现顺序：
1. `kb/compliance/kms_vault.py`：`VaultTransitKmsClient`（create/wrap/unwrap/destroy/key_state）
2. 凭据只从环境/Vault 注入；不进设置表、不进代码
3. 假 `HttpTransport` 测试，覆盖销毁后 `unwrap` 失败、KMS 不可用 fail-closed
4. `build_kms_from_env` 工厂；KMS 不可用时拒绝启动加密路径
5. 可选：把 `kb/backup_drill.py` 切到真实 KMS 适配器（用假 Vault 先跑）

**第二优先**：接真实 `HttpBackupCatalog` 厂商适配器（需 A-19 信息）。

**第三优先**：实施顾问上线自动降级（FR-06-05 / A-13），当前 `implementation_consultant` 仍可长期写设置。

需要先读：`kb/compliance/kms.py`、`kb/compliance/crypto.py`、`.kb/decisions.md` A-18/A-20。

## 5. 本次会话未解决的疑问

- 用户尚未提供真实备份系统类型、目录 API、租户隔离方式；`external_catalog` 目前是 manifest 参考实现。
- 另一个 agent 曾在 04:09 创建并删除 `kb/compliance/manifest_drill.py` 与对应测试，只留下旧 `.pyc`；已删除残留 pyc，确认无源码引用。`.pytest_cache` 中仍有旧测试名，属缓存。
- `.claude/` 下是本地技能副本，是否纳入版本库仍需用户确认。
