# 交接检查点 / Checkpoint

| | |
| --- | --- |
| 保存时间 | 2026-10-31T08:16:37+00:00 |
| HEAD | `8bbd8fc9848f` |
| 工作区 | 有大量未提交改动（本轮新增/修改，未 commit；另含未跟踪 `.claude/`） |
| 单元测试 | 实跑：`269 passed, 2 skipped, 22 deselected`（integration 之外） |
| 集成测试 | 实跑：`22 passed`（需 `make up` / PostgreSQL） |
| git | 未 push。`origin` 已配，VPN 阻断 |

## 1. 手头这一步

- **目标**：备份合规链路的可扩展接入 —— KMS 接缝、恢复演练闸门、备份目录领域层、设置表与管理 API、manifest 工具与本机演练。
- **验收**：`ruff check .` / `ruff format --check .` 通过；`pytest -m "not integration"` → 269 passed；`pytest -m integration` → 22 passed；`make schema-check` → 29 张表。
- **进度**：本轮全部完成。新增迁移 `0002_service_settings.sql` 已应用到本地库；`make up` 现在会自动跑 `make migrate`。
- **未提交**：按项目规则，用户未明确要求 commit，故未提交。

## 2. 试过并否决的

| 方案 | 为什么否决 |
| --- | --- |
| 直接修改 `schema.sql` 加设置表 | 违反 TD-01 §12 M-2；改用增量迁移 `migrations/0002_service_settings.sql` |
| 让 Agent 通过管理 API 直接执行设置变更 | 违反 A-13 / NG-4 / NG-7；只保留 `proposals` 提案端点，PUT 对 `x-actor-type: agent` 返回 403 |
| 备份轮转用单一长期密钥 | 无法做主体级加密擦除；策略层直接拒绝 `single_long_lived` |
| 备份权威状态只放业务库 | 恢复旧备份会回滚删除状态；策略层拒绝 `authority_store=db_only` |
| 恢复演练自动删除数据 | 风险过高；`RestoreGuard` 只扫描并阻断，清理由隔离环境的人/编排执行 |
| manifest 模式声称已物理删除介质 | manifest 只读/生命周期外部执行；文案明确只报告到期，不宣称物理清除 |

## 3. 卡住的决策

| 编号 | 问题 | 在等谁 |
| --- | --- | --- |
| Q-16 | 重排模型与 Top-K 默认值；需 FR-04-17 真实评测基线 | 算法 |
| E-1 | 加密擦除是否被客户合规官接受 | 客户合规 + 法务 |
| E-3 | 备份轮转周期具体取值 | 客户基础设施 + 合规 |
| A-19 | 真实备份系统 API/凭据/租户隔离方式 | 用户/客户基础设施 |
| A-20 | 真实 KMS 厂商适配器（Vault Transit / 云 KMS / HSM） | 部署环境 |

## 4. 下一步

```bash
# 先确认工作区并跑完整自检
make up
make schema-check
.venv/bin/pytest -m "not integration" -q
DATABASE_URL=postgresql://kb:dev@localhost/kb .venv/bin/pytest -m integration -q
```

建议顺序：
1. 接 `HttpBackupCatalog` 的真实厂商适配器（`catalog_mode=http_api`），用用户提供的目录 API 与凭据引用。
2. 用 `config/backup_manifest.jsonl` 或真实 manifest 跑：
   `.venv/bin/python -m kb.backup_drill --manifest <path> --manifest-tenant <tenant>`
3. 实现真实 KMS 适配器（先 Vault Transit，再部署环境对应云 KMS）。
4. 处理 A-13 的实施顾问上线降级逻辑（当前允许 `implementation_consultant` 写设置）。

## 5. 本次会话未解决的疑问

- 用户尚未提供真实备份系统类型、目录 API、租户隔离方式；`external_catalog` 目前是 manifest 参考实现。
- 另一个 agent 曾在 04:09 创建并删除 `kb/compliance/manifest_drill.py` 与对应测试，只留下旧 `.pyc`；已删除残留 pyc，确认无源码引用。`.pytest_cache` 中仍有旧测试名，属缓存。
- `.claude/` 下是本地技能副本，是否纳入版本库仍需用户确认。
