# 交接检查点 / Checkpoint

| | |
| --- | --- |
| 保存时间 | 2026-10-28T02:38:16+00:00 |
| HEAD | *（尚未 `git init`，解压后第一次提交时填入）* |
| 工作区 | 全新解压，无未提交改动 |
| 单元测试 | `1 error`：`ModuleNotFoundError: No module named 'kb.identity.permissions'` —— **这是预期的「正确的红」**，见 `SETUP.md` 步骤 4 |

## 1. 手头这一步

- **目标**：尚未开始编码。第一个任务是创建 `kb/identity/permissions.py` 与 `kb/identity/registry.py`
- **验收**：`make test-unit` → `26 passed, 3 deselected`
- **进度**：文档、schema、测试向量、包骨架均已就位。实现代码一行未写

## 2. 试过并否决的

| 方案 | 为什么否决 |
| --- | --- |
| `effective_scope(ctx)` 用模块级全局 registry | 全局注册表让求值结果依赖进程历史，无法测试也无法审计。已改为显式传入 `registry` 与 `now`（TD-03 D-6） |
| 无条件启用 Cross-Encoder 重排 | PRD v1.7 改为证据驱动的条件启用：弱重排器会把已召回的正确证据往下压，造成净损失。须先由 FR-04-17 测出 recall@10 与 recall@50 的差值（PRD §14.3 E-3） |
| 融合权重沿用「向量为主」的行业默认 | 企业内部语料上 BM25 这条腿更强（PRD §14.3 E-1）。默认已改为偏向 BM25，且必须在客户评测集上标定 |
| 引用 ERAA-2026 的各项数字 | 无法验证来源，已定为 C 级**禁止引用**。见 PRD §14.3 E-6 |

## 3. 卡住的决策

| 编号 | 问题 | 在等谁 |
| --- | --- | --- |
| A-12 | 首个连接器选 SharePoint/Confluence 还是本地文件夹 | 用户拍板（建议 SharePoint，理由见 `SETUP.md` 第八节） |
| Q-16 | Cross-Encoder 重排模型与 Top-K 默认值 | 算法实测（现已改为先测 FR-04-17 双深度召回基线再定） |

## 4. 下一步

```bash
# 1. 初始化仓库（若尚未做）
git init && git add -A && git commit -m "初始骨架：文档、schema、测试"

# 2. 装环境并确认「正确的红」
python3 -m venv .venv && source .venv/bin/activate
make dev
make test-unit        # 应为 1 error: kb.identity.permissions 缺失

# 3. 进 opencode，把 SETUP.md 第四节那段提示原样交给它
opencode
```

实现前须读：`docs/td-03-permission-evaluation.md` 的 §2、§3、§4、§6。
**特别注意** P-1（不做层级比较）、P-2（break 位置在 append 之后）、D-5（有效期检查排在 actor 分支之前）。

## 5. 本次会话未解决的疑问

- PRD 已到 v1.7，但 TD-01/TD-02/TD-03 三份技术设计尚未同步 v1.7 的变更：
  **FR-04-17 双深度召回基线**与**融合权重默认取向**都还没有对应的技术设计条目。
  做到检索链路（开发顺序第 6 步）之前需要补。权限模块不受影响，可以先开工。
