# 交接检查点 / Checkpoint

| | |
| --- | --- |
| 保存时间 | 2026-10-29T14:03:08+00:00 |
| HEAD | `6b73f1f412d2` 实现权限求值模块与 Agent 登记表 |
| 工作区 | 有未提交改动：`M AGENTS.md`（N-7 助手否决项）；未跟踪 `.claude/`、`.kb/decisions.md` |
| 单元测试 | 实跑：`31 passed, 3 deselected` |
| 环境 | `.venv` 为 Python 3.13.15；`ruff` / `mypy kb/identity` 均通过 |
| git | 无 push。`origin` = `https://github.com/abe87654321/x-impulse.git`。`gh` 已装并登录，但 VPN 问题导致推送未成功 |

## 1. 手头这一步

- **目标**：权限求值模块已完成并通过验收；下一步进入开发顺序第 3 步（连接器接口 + 契约测试）
- **验收**：`make test-unit` → `31 passed, 3 deselected`（已达成）
- **进度**：`kb/identity/permissions.py`（evaluate / build_chain / expand_memberships / effective_scope）与 `kb/identity/registry.py`（AgentRegistry）已实现并提交；3 条 integration 测试仍 deselect（依赖仓储层 / 检索层未实现）

## 2. 试过并否决的

| 方案 | 为什么否决 |
| --- | --- |
| `effective_scope` 用模块级全局 registry | 求值结果依赖进程历史，无法测试与审计；改为显式注入 `registry` 与 `now` |
| 把 `inheritance_broken` 读在 AclRow 上 | 断开继承是对象属性，只在 ObjectNode 表达；条目侧再读会造成二义与越权 |
| `Actor is None` 先于有效期检查返回 FULL_SCOPE | 过期令牌会被放行（TD-03 D-5）；已把有效期检查放在最前 |

## 3. 卡住的决策

| 编号 | 问题 | 在等谁 |
| --- | --- | --- |
| A-12 | 首个连接器选 SharePoint/Confluence 还是本地文件夹 | 用户拍板（建议 SharePoint） |
| Q-16 | Cross-Encoder 重排模型与 Top-K 默认值 | 算法实测（先做 FR-04-17 双深度召回基线） |

## 4. 下一步

```bash
# VPN 恢复后推送已提交内容
git push -u origin master

# 进入第 3 步之前先读
#   docs/td-02-connector-contract.md §1 接口、§2 能力声明、§4 ACL 映射、§7 契约测试
# 然后实现连接器接口并跑契约测试（当前 11 条会 skip 到实现为止）
pytest -m "not integration" -q
```

## 5. 本次会话未解决的疑问

- 工作区 `AGENTS.md` 的 N-7 修改与 `.kb/decisions.md` 尚未提交，是否随下次提交一并纳入？
- `origin` 已配置但首次推送未完成；需确认远端仓库 `abe87654321/x-impulse` 是否已创建、默认分支是否接受 `master`。
