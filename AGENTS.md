# AGENTS.md — 企业知识库 RAG 平台

单租户交付的企业内部知识助手（PF-1 档）。员工提问、员工看，**不对客**。
栈：Python 3.12+ / PostgreSQL 15+ / 外部向量库（Milvus 或 Qdrant）。
语法下限是 3.12，即使开发机装的是更高版本 —— 见 pyproject.toml 里 target-version 的注释。

---

## 一、硬不变量 —— 违反即回退，不接受"先跑起来再说"

这 10 条要么是安全边界，要么是改起来极贵的结构决策。**写代码前先确认没有违反。**

```
I-1  权限：任一适用的 deny 命中即拒绝，不比较层级；无 allow 则默认拒绝。
     目录级 deny 必须压过文档级 allow。禁止实现成"更具体者优先"。

I-2  权限：断开继承（inheritance_broken）时，该节点【自身】的条目仍适用，
     只有它的【祖先】不适用。写错这个 break 位置是越权 bug。
     **且断开继承只在 ObjectNode 上表达**：acl_entries 里那一列只被
     td-03 §3.5 的 build_chain 读取一次并翻译成节点属性，此后不再读。
     漏了这次翻译 = 断开继承被静默忽略 = 越权。build_chain 必须查
     链上【全部】对象，只查文档自身是典型错误（断开通常设在目录上）。

I-3  权限：检索必须在【召回阶段】过滤，禁止先召回后遮蔽。
     且缓存命中后仍须做实时二次校验。

I-4  数据：所有业务表必须有 tenant_id NOT NULL。
     仅 deployment_profiles / erasure_location_types 两张全局字典表例外。

I-5  数据：禁止软删除列（is_deleted / deleted_at 作可见性开关）。
     删除必须落到存储层。日志与备份用加密擦除，不是硬删。

I-6  数据：禁止 PostgreSQL 原生 ENUM 类型，一律 text + CHECK。
     原生 ENUM 无法删值，会废掉可扩展预留。

I-7  数据：chunk 必须记 char_start / char_end（相对规范化正文）。
     citation 必须记 chunk_content_hash + chunk_version。
     这两项事后无法补——历史数据不会因为将来加字段而变得可追溯。
     **「规范化」的规则集见 td-01 §2·5，必须严格照做**：NFC，禁用 NFKC
     （NFKC 把全角折成半角，中文文档的全部偏移量会变），不折叠空白。
     偏移量只在 (parser_version, normalization_version, content_hash)
     三元组下可验证；主信任锚是 chunk_content_hash，不是偏移量。

I-8  数据：audience 默认 internal_only。新增知识源、新增文档一律如此。
     放宽必须是显式操作。

I-9  连接器：不得自行 sleep 重试。重试是调度器职责。
     连接器内部重试会让限速、超时预算与断点续传全部失效。

I-10 身份：禁止运行时隐式降级保证等级。校验失败即拒绝，
     不得回退到更弱的等级或服务账号——那是一次静默的权限提升。
```

---

## 一·五、会话开头与结尾

```
会话开始 → 先调 ck-resume 技能（读 .kb/checkpoint.md，并强制校验它是否仍成立）
会话结束 → 调 ck-save 技能（覆盖写入 .kb/checkpoint.md 并提交）
```

`.kb/checkpoint.md` 只记 **git 记不下的过程信息**：手头那一步、试过并否决的方案、
卡住的决策、下一步的具体命令。**不要在其中复述本文件或 PRD 的内容** —— 那会产生
第二份必然漂移的真相。改了哪些文件由 `git log` 负责，不进检查点。

---

## 二、按任务加载文档 —— 不要一次全读

| 你要做的事 | 读这份 | 读哪几节 |
| --- | --- | --- |
| 建表 / 改表结构 / 写迁移 | `docs/td-01-database-schema.md` | 全文；新表必过 §10 六问清单 |
| 写连接器 | `docs/td-02-connector-contract.md` | §1 接口、§2 能力声明、§4 ACL 映射、§7 契约测试 |
| 碰权限 / ACL / 委派 | `docs/td-03-permission-evaluation.md` | §3 算法、§7 测试向量（必读） |
| 做删除链路 | `docs/fr-12-04-cascading-erasure-design.md` | §5 十处、§6 加密擦除、§7 抑制清单 |
| 查某条需求的确切措辞 | `docs/prd.md` | **只读该 FR 所在的那一行**，不要通读 |

**不要读**：`docs/architecture-positioning.md` 的第 4~6 章（MCP / Agent 接入，Phase 3 内容）。

---

## 三、本期不做 —— 看到相关需求也不要实现

```
N-1  MCP Server 与 Agent 接入（FR-07-11~13、FR-06-18~27）—— Phase 3
N-2  实体层 / 知识图谱 / GraphRAG —— 明确不建，只保留表结构预留
N-3  对客产品档 PF-2 —— 独立产品，后续分支
N-4  多活容灾、亚秒延迟优化、计费系统 —— 无法预留，将来真做
N-5  Self-RAG 语义蕴含校验（FR-05-05b）—— 降为 P1，与流式输出冲突
     本期只做 FR-05-05a 引用绑定校验（逐句、可与流式并行）
N-6  按权限分区的结果缓存（FR-04-14b）—— 需容量评估
     本期只做 FR-04-14a 基础缓存（问题哈希 → chunk_id 列表）
```

PRD 里编号有空缺（FR-01-17、FR-04-15、FR-05-18/19 等）是**故意保留给 Phase 3 的**，不要填补。

---

## 四、开发顺序

```
1. schema.sql 建库
2. 权限求值：测试向量已经写好在 tests/permission/（28 条 + 3 个属性测试，
   非 integration 共 31 条），不要重写。直接按 td-03 §2/§3/§3.5/§4/§6 实现
   kb/identity/permissions.py 与 kb/identity/registry.py 让它变绿。
   §3.5 的 build_chain 也是本步交付物，不要留到做检索时再写。
   evaluate 必须是纯函数（无 IO、无全局状态）；effective_scope 的
   registry 与 now 必须显式传入（D-6）
3. 连接器接口定义 + td-02 §7 的 11 条契约测试
4. 第一个连接器实现
5. 解析切分（注意 I-7 字符锚点）
6. 检索链路
7. 生成与引用绑定
```

---

## 五、提交前自检

```
□ 新增的表过了六问清单（td-01 §10）
□ 碰了权限逻辑 → pytest -m "not integration" 全绿（31 条），其中 10 条 ★ 必过
□ 碰了 ACL 加载/同步 → tests/permission 的 B·5 节（5 条）必过，它守 I-2 的加载接缝
□ 写了连接器 → td-02 §7 契约测试通过（不适用的可 skip，但要在能力声明里体现）
□ 新增枚举值 → 用了 text + CHECK 或登记表，没用原生 ENUM
□ 没有引入 is_deleted 之类的软删除列
□ 检索路径的改动 → 确认仍是召回阶段过滤 + 二次校验
```

---

## 六、约定

- 模块按**限界上下文**切，不按技术层切。`kb/` 下的包已建好：

  ```
  知识管道上下文（共享 Document/Chunk/Citation 模型，可互相依赖）
    kb/ingestion/    连接器与同步        ← TD-02
    kb/parsing/      解析与切分
    kb/indexing/     向量化与索引
    kb/retrieval/    检索与重排
    kb/generation/   生成与引用绑定

  独立上下文（各有独立模型，不得反向依赖管道）
    kb/identity/     身份与授权          ← TD-03
    kb/compliance/   合规与数据主体权利   ← FR-12-04 设计
    kb/governance/   治理与运营
    kb/audit/        审计（只追加）
  ```

  ❌ 不要建 `parsers/` `retrievers/` `generators/` 这种按技术层切的目录
- 档次相关的行为（受众过滤、trust_floor 默认值、留存期、限流阈值、
  内容安全阈值）一律**读配置**，不得硬编码 —— 将来靠部署档案区分产品档次
- 服务保持无状态：会话与任务状态存共享存储，不放进程内，不写本地文件
- 时间戳一律 `timestamptz`；主键一律可读 text ID（`chk_...` / `doc_...`）
