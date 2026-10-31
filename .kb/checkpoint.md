# 交接检查点 / Checkpoint

| | |
| --- | --- |
| 保存时间 | 2026-10-31T13:17:38+00:00 |
| HEAD | `960852453c68` |
| 工作区 | 干净；`master` 领先 `origin/master` 2 个提交（push 因网络/TLS 失败） |
| 单元测试 | 实跑：`325 passed, 2 skipped, 24 deselected`（integration 之外） |
| 集成测试 | 本次会话实跑：`24 passed`（需先应用 `migrations/0003_query_cache.sql`） |

## 1. 手头这一步

- **目标**：本轮三项（T-2 源端删除、租户备份轮转、共享检索缓存）已全部提交，无未完成代码。
- **验收**：`git status --short` 为空；`make schema-check` 输出建表数 31。
- **进度**：代码与测试完成；push 未完成。

## 2. 试过并否决的

| 方案 | 为什么否决 |
| --- | --- |
| T-1 主体删除整篇删除 `raw_object` | 会连带删除同一原文中其他主体的合法内容；按主体重写 PDF/Office 未实现，故主体级 raw 继续 SKIPPED（A-29） |
| 在 `raw_store.py` 顶层 import `kb.compliance.backup_s3` | 触发 `indexing.deletion → raw_store → compliance.__init__ → erasure` 循环导入；改为 TYPE_CHECKING + 函数内延迟导入 |
| 无设置时用 `BackupPolicy` 推荐默认值构造轮转 | 会把“推荐默认”当成“已接入”，删除证明误报 LOGICAL_DONE；改为必须显式配置 `backup_system`（A-35） |
| S3 `delete_prefix` 只做普通 DeleteObjects | 版本控制与 Object Lock 会让删除假成功；改为版本删除 + Object Lock 拒绝 + 中止 multipart（A-33） |
| 保留期清除放在进程内 sleep 轮询 | 违反连接器/调度器职责划分；改为外部调度器调用的 `kb.retention` CLI（A-32） |

## 3. 卡住的决策

| 编号 | 问题 | 在等谁 |
| --- | --- | --- |
| A-19 | 真实备份系统/桶策略/凭据与非生产验收环境 | 用户/客户基础设施 |
| E-3 | 备份轮转周期具体取值 | 客户基础设施 + 合规 |
| A-15 | 主体级 `raw_object` 按主体重写原文能力 | 产品/合规 |
| Q-16 | 重排模型与 Top-K 默认值，需真实评测基线 | 算法 |

## 4. 下一步

```bash
# 网络恢复后补推（当前领先 origin/master 2 个提交）
git push origin master

# 新环境先应用迁移，再跑集成测试
make migrate
make schema-check          # 期望建表数 31
DATABASE_URL=postgresql://kb:dev@localhost/kb .venv/bin/pytest -m integration -q
```

下一项实现建议：在 `kb/assembly.py` 补检索服务装配，让 `KB_QUERY_CACHE=postgres`
真正进入检索路径（目前 `HybridRetrievalService` 未显式注入时仍默认
`InMemoryQueryCache`）。需要先读：`kb/retrieval/pg_adapters.py`、`kb/assembly.py`、
`kb/retrieval/cache.py`。

## 5. 本次会话未解决的疑问

- 推送失败：`Failed to connect to github.com port 443` / `GnuTLS recv error (-110)`；本地领先 2 个提交。
- `mypy kb/retrieval/retriever.py` 有 2 条既有 rerank 类型错误（非本次引入），改动该文件前需决定是否一并修。
- 新环境若未执行 `migrations/0003_query_cache.sql`，`PostgresQueryCache` 集成测试会失败；`make up` 会自动 `make migrate`。
