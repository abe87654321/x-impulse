# 交接检查点 / Checkpoint

| | |
| --- | --- |
| 保存时间 | 2026-10-30T05:08:00+00:00 |
| HEAD | `88cb46db954b` 实现生成与引用绑定：逐句校验、拒答与证据快照 |
| 工作区 | 仅未跟踪 `.claude/`（本地技能副本）；其余已提交 |
| 单元测试 | 实跑：`92 passed, 2 skipped, 3 deselected` |
| 环境 | `.venv` Python 3.13.15；`ruff check/format` 全过；`mypy kb` 无错误 |
| git | 未 push。`origin` = `https://github.com/abe87654321/x-impulse.git`，VPN 阻断 |

## 1. 手头这一步

- **目标**：开发顺序 1–7 的纯逻辑部分已全部完成（权限 → 连接器 → 解析切分 → 检索 → 生成与引用绑定）
- **验收**：`.venv/bin/pytest -m "not integration" -q` → `92 passed, 2 skipped, 3 deselected`
- **进度**：所有模块均为内存/注入式实现，接口已定型；尚未接 PostgreSQL、真实向量库、真实模型

## 2. 试过并否决的

| 方案 | 为什么否决 |
| --- | --- |
| 缓存存未过滤的原始召回、命中后再遮蔽 | 违反 I-3（召回阶段过滤）；改为缓存已按当时用户过滤的候选，命中后仍二次校验 |
| 切分把 `\n\n` 的每个 `\n` 都当切点 | 会切在段落分隔中间；改为连续换行只取末尾位置 |
| 只在文档自身查 `inheritance_broken` | 目录上的断开继承会失效（越权）；已有回归测试守住"查链上全部对象" |
| 用 `datetime.now()` 或模块级 registry 做求值 | 已有前次会话否决记录；求值保持纯函数、依赖显式注入 |

## 3. 卡住的决策

| 编号 | 问题 | 在等谁 |
| --- | --- | --- |
| Q-16 | Cross-Encoder 重排模型与 Top-K 默认值 | 算法实测（需先有 FR-04-17 双深度基线） |

## 4. 下一步

```bash
# VPN 恢复后推送
git push -u origin master

# 接入持久层前的准备
make up                                  # 起 postgres + 向量库
.venv/bin/pytest -m integration -q       # 现有 3 条集成测试从 deselect 转绿
```

接入顺序建议：先写 PostgreSQL 仓储（`AclRepository` / `ChunkRepository` 的真实实现），
再接 BM25 + 向量后端，最后接模型适配器。读 `docs/td-03-permission-evaluation.md` §5
（检索路径权限）与 `docs/td-01-database-schema.md` §3、§5（chunks / citations）。

## 5. 本次会话未解决的疑问

- `.claude/` 下是本项目技能副本，是否纳入版本库由下次会话与用户确认。
- 首次推送仍未完成：需确认远端仓库与默认分支。
