# 交接检查点 / Checkpoint

| | |
| --- | --- |
| 保存时间 | 2026-10-30T16:43:02+00:00 |
| HEAD | `195e19bc06ce` 实现限制处理与保留期到期清除 |
| 工作区 | 仅未跟踪 `.claude/`；其余已提交 |
| 单元测试 | 实跑：`132 passed, 2 skipped, 15 deselected`（integration 之外） |
| 全量测试 | 实跑：`147 passed, 2 skipped`（含集成，需 `make up`） |
| git | 未 push。`origin` 已配，VPN 阻断 |

## 1. 手头这一步

- **目标**：核心链路与合规链路已闭环（同步 → 索引 → 混合检索 → 生成/引用 → API/审计 → 文档级/T-1 删除 → 加密擦除 → 墓碑 → 限制处理）
- **验收**：`.venv/bin/pytest -q` → `147 passed, 2 skipped`
- **进度**：本期纯逻辑、持久层、删除链路均完成；尚未做流式回答、限流、真实嵌入/模型、审计加密写入

## 2. 试过并否决的

| 方案 | 为什么否决 |
| --- | --- |
| 词法腿用 `ILIKE` 子串计数 | 不是 BM25，排序没有长度/IDF 概念；已替换为应用层 BM25 |
| 文档级删除把 audit/backup 标成 `PHYSICAL_DONE` | 没有真实密文与备份接入时是虚假覆盖；改为 `SKIPPED`/`LOGICAL_DONE` 并写入未覆盖声明 |
| T-1 删除整条会话/答案 | 会话是提问者的数据；改为原位替换为 `[已依法删除]` |
| 抑制键直接用 `external_id` 或未哈希的 NUL 字符串 | 前者与墓碑对不上，后者被 Postgres text 拒绝（0x00）；统一 `document_scope_key` + 哈希器 |
| 审计 payload 写问题原文 | 违反 FR-09-01 / FR-12-16；只写 `query_id` 与 `text_hash` |
| 复用旧 dev 数据库卷 | schema 已漂移（缺 `parser_version` 等），直接跑集成测试报列不存在；用 `make reset` 重建 |

## 3. 卡住的决策

| 编号 | 问题 | 在等谁 |
| --- | --- | --- |
| Q-16 | Cross-Encoder 重排模型与 Top-K 默认值 | 算法实测（需 FR-04-17 基线） |
| E-1 | 加密擦除是否被客户合规官接受为等效删除 | 客户合规 + 法务 |
| E-3 | 备份轮转周期 | 客户基础设施 + 合规 |
| —（未编号） | 真实嵌入模型与生成模型选型 | 算法 + 硬件/部署方 |

## 4. 下一步

```bash
# 推送（VPN 恢复后）
git push -u origin master

# 建议下一项：流式回答 + 限流/配额
grep -n "FR-05-12\|### FR-16\|FR-16-" docs/prd.md
```

实现顺序建议：
1. `POST /v1/ask/stream`（SSE），复用 `CitationBinder.bind_stream` 的逐句校验
2. 限流/配额（FR-16）：按租户/主体，读部署档案配置
3. 真实嵌入/生成适配器（现有 `Embedder` / `Generator` 协议可直接替换）
4. FR-04-17 评测基线与重排开关（阻塞 Q-16）

## 5. 本次会话未解决的疑问

- `.claude/` 下是本地技能副本，是否纳入版本库仍需用户确认
- 首次推送仍未完成：需确认远端仓库与默认分支
- `schema.sql` 新增了 `erasure_location_types('document')` 字典行；新库自动带，旧库由 `RestrictionService` 运行时补齐
