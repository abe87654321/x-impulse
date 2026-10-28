# 项目启动指南 / Setup

**这份是唯一的入口文档。** 其余文档按需读，路由表在 `AGENTS.md`。

环境：Ubuntu · Python 3.12+（开发机 3.14 亦可，见四·五）· Docker · PostgreSQL 15

---

## 一、文件清单

### 根目录

| 文件 | 是什么 | 你要改吗 |
| --- | --- | --- |
| `AGENTS.md` | **编码 agent 的常驻指令** —— 10 条硬不变量 + 文档路由表 + 本期不做清单 | 加约定时改 |
| `opencode.json` | opencode 项目配置。只有权限规则与格式化器，**不含 model/provider** —— 那些用你的全局配置 | 一般不改 |
| `Makefile` | 常用命令。`make` 不带参数列出全部 | 加命令时改 |
| `schema.sql` | **完整 DDL**，28 张表 27 个索引。容器首次启动自动执行 | 见"改 schema"一节 |
| `pyproject.toml` | Python 项目定义、依赖分组、pytest/ruff/mypy 配置 | 加依赖时改 |
| `docker-compose.yml` | 开发用 PostgreSQL + Qdrant | 一般不改 |
| `.gitattributes` | 强制 LF 换行 | 不改 |
| `.gitignore` | 标准忽略 | 按需 |
| `SETUP.md` | 本文件 | — |

### `docs/` —— 按需读，不要一次全读

| 文件 | 什么时候读 | 行数 |
| --- | --- | --- |
| `td-01-database-schema.md` | 建表 / 改表结构 / 写迁移 | ~620 |
| `td-02-connector-contract.md` | 写连接器 | ~400 |
| `td-03-permission-evaluation.md` | 碰权限、ACL、委派 | ~430 |
| `fr-12-04-cascading-erasure-design.md` | 做删除链路 | ~650 |
| `prd.md` | **只查单条 FR 的确切措辞**，不要通读 | ~1200 |
| `architecture-positioning.md` | 需要背景时；第 4~6 章是 Phase 3 内容，本期不看 | ~900 |

### `tests/` —— 已写好，先跑它，别改它

```
tests/permission/
  builders.py       构造测试数据的纯函数    → 测试里显式 import
  conftest.py       pytest 夹具            → 自动加载，不 import
  test_vectors.py   23 条用例 + 3 个属性测试（非 integration 共 26 条）

tests/contract/
  helpers.py        skip_unless 辅助函数
  conftest.py       夹具 + 参数化钩子
  registry.py       连接器登记表（新增连接器时加一行）
  test_connector.py 11 条契约测试
```

> **测试是规格，不是待改的代码。** 如果 agent 为了让测试通过去改测试文件，那 23 条向量就白写了。

### `kb/` —— 空包骨架，等你和 agent 填

```
知识管道上下文（共享 Document/Chunk/Citation，可互相依赖）
  kb/ingestion/    连接器与同步        ← TD-02
  kb/parsing/      解析与切分
  kb/indexing/     向量化与索引
  kb/retrieval/    检索与重排
  kb/generation/   生成与引用绑定

独立上下文（各有独立模型，不得反向依赖管道）
  kb/identity/     身份与授权          ← TD-03
  kb/compliance/   合规与数据主体权利   ← FR-12-04
  kb/governance/   治理与运营
  kb/audit/        审计（只追加）
```

每个目录里只有一个空的 `__init__.py`。**这些目录必须存在** —— 否则 `pip install -e .` 会失败。

---

## 二、一次性准备

```bash
# 1. 系统依赖
sudo apt update
sudo apt install -y python3-venv postgresql-client

#    Python 用系统自带的就行（24.04 是 3.12，26.04 是 3.13），
#    装了 3.14 也可以 —— 见「四·五、关于 Python 版本」。

#    注意：apt 会建议 postgresql-client-common，那个包【不含 psql】。
#    要装的是 postgresql-client。

# 2. docker 免 sudo（不做的话 opencode 调 docker 会卡在授权上）
sudo usermod -aG docker $USER
newgrp docker
docker ps                    # 不带 sudo 能跑就对了

# 3. 确认有 Compose V2
docker compose version
#    报错说没这命令的话：sudo apt install -y docker-compose-v2
```

---

## 三、启动项目（五步，每步都有验证）

### 步骤 1 · 解压 + 初始化 git

```bash
tar -xzf kb-rag.tar.gz
cd kb-rag
git init && git add -A && git commit -m "初始骨架：文档、schema、测试"
```

### 步骤 2 · Python 环境

```bash
python3 -m venv .venv        # 或 python3.14 -m venv .venv
source .venv/bin/activate
make dev                     # = pip install -e ".[dev]"
```

**应该看到**：安装成功，末尾 `Successfully installed ... kb-rag-0.1.0`。

> ML 依赖（sentence-transformers 等，几个 G）在 `[ml]` 组里，**现在不用装**。要跑 embedding 时才 `pip install -e ".[ml]"`。

### 步骤 3 · 起数据库

```bash
# 如果你之前手工起过 kb-pg 容器，先删掉——它占着 5432 且账号不一致
docker rm -f kb-pg 2>/dev/null || true

make up
```

**验证**：

```bash
make schema-check
```

**应该看到**：`建表数：28`。

不是 28 就说明 `schema.sql` 没执行或执行失败：

```bash
docker compose logs postgres | grep -i error
```

### 步骤 4 · 跑测试，确认是「正确的红」

```bash
make test-unit
```

**应该看到**（已在本包上实测过，逐字如此）：

```
collected 0 items / 1 error

ERROR tests/permission - ModuleNotFoundError: No module named 'kb.identity.permissions'
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```

**这是正确的红。** `kb/identity/permissions.py` 还没写，测试当然跑不起来。

三个判断点：

- 错误**只有 1 个**，不是 2 个。`tests/contract/` 在 `kb.ingestion.contract` 实现前会整体跳过收集，不该报错。
- 错的是 `kb.identity.permissions`，不是 `builders`。报 `No module named 'builders'` 说明 `pythonpath = ["."]` 丢了。
- 不该有语法错误或 fixture 缺失。有的话是文件放错了位置，先解决再往下。

### 步骤 5 · 确认 opencode 读到了 AGENTS.md

```bash
opencode
```

问它一句：

> 这个项目的硬不变量有几条？第 I-2 条是什么？

**答得出**（10 条；I-2 是断开继承时该节点自身的条目仍适用）就说明加载成功。

答不出就检查 `AGENTS.md` 是不是在项目根目录、文件名大小写对不对。

---

## 四、第一个开发任务

把下面这段原样交给 opencode：

```
读 docs/td-03-permission-evaluation.md 的 §2、§3、§4、§6。

创建两个文件：
- kb/identity/permissions.py —— §2 的数据结构 + evaluate / expand_memberships
  / effective_scope 三个函数
- kb/identity/registry.py —— AgentRegistry，需要 register(agent_id,
  granted_scope=...) 与 get(agent_id)（返回带 .granted_scope 的对象，
  未登记返回 None）

目标：pytest -m "not integration" -v 全绿，26 passed。

约束：
- evaluate 必须是纯函数：无 IO、无全局状态
- effective_scope 的 registry 与 now 都是显式参数，不得用模块级全局，
  不得调 datetime.now()（见 §6 D-6）
- 严格按 §3 的算法实现，特别注意 P-1、P-2 两条性质与 §6 的 D-5
- 不要修改 tests/ 下的任何文件
```

**验收**：`make test-unit` → `26 passed, 3 deselected`。

> 我已用一份参考实现在本包上跑通过这 26 条，所以「跑不绿」一定是实现问题，不是测试问题。
>
> - `2b` 或 `3d` 不过 → 踩了 P-1（做了层级比较）或 P-2（break 位置写错）
> - `6c` 不过 → 踩了 D-5（把 `actor is None` 的分支写在了有效期检查之前）
>
> 把 TD-03 对应段落再发一次给它通常就能改对。

跑通之后进 TD-02 的连接器接口。

---

## 四·五、关于 Python 版本

**声明的下限是 3.12，但你在哪个版本上开发都行。** 两者是不同的东西，别混。

| | 值 | 为什么 |
| --- | --- | --- |
| `requires-python` | `>=3.12` | 客户机器上跑的那个。Ubuntu 24.04 自带 3.12、26.04 自带 3.13，都不用装 PPA —— 私有化部署时这一条很值钱 |
| ruff `target-version` / mypy `python_version` | `py312` | **锁下限，不是锁开发机。** 本机若是 3.14、这里也写 3.14，agent 会写出 3.12 跑不了的语法，而你本机永远测不出来，等客户机器上才炸 |
| CI matrix | `3.12` + `3.14` | 两头都跑。只跑一个，另一头会悄悄坏掉 |

**为什么不是 3.11**：2027-10 就 EOL。这是个背等保三级义务的私有化产品，运行时在第一个合同周期内到期，客户的漏扫会直接报出来。

**为什么不是「3.14 一把梭」**：Phase 1 没问题（asyncpg 0.31 已支持到 3.14，权限/连接器/schema 都是纯 Python）。风险在 Phase 2 的 `[ml]` 组 —— PyTorch 的 cp314 **CUDA** 轮子历来滞后于 CPU 轮子。到时先跑：

```bash
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

打出 `+cpu` 或 `False` 就说明这个版本没 GPU 轮子。两条路：① 数据面单独用一个 Python 版本跑（`[ml]` 本就独立，embedding 服务本来也该在 GPU 机器上）；② 开发机降到 3.13。**不要为了迁就 torch 去改 `requires-python`** —— 服务面和数据面的运行时约束不是一回事。

---

## 五、日常命令

```bash
make              # 列出所有命令
make test-unit    # 单元测试，不需要数据库，快
make test         # 全部测试（含 integration）
make lint         # ruff check + format --check
make typecheck    # mypy（权限模块是安全关键）
make up / down    # 起停数据库
make reset        # ★ 改完 schema.sql 后必跑
```

> `make lint` 在**刚解压的空包上就是绿的**（已实测）。第一次跑就红说明有人动过
> `pyproject.toml` 里的 ruff 配置 —— 尤其是 `extend-exclude = ["docs"]`：
> 去掉它，`ruff format` 会去重排 TD-02/TD-03 文档里手工对齐的规格代码块。

---

## 六、改 schema 的正确姿势

`schema.sql` 是通过 `/docker-entrypoint-initdb.d/` 挂载的，**只在数据目录为空时执行一次**。

所以改完 DDL 之后：

```bash
make reset        # = docker compose down -v && docker compose up -d
make schema-check # 确认表数变化符合预期
```

**`down -v` 不能省。** 不删数据卷的话 schema.sql 不会重跑，你会一直在旧 schema 上测试 —— 而测试还是绿的，因为库根本没更新。这个坑很隐蔽。

进入 Phase 1 之后表结构就冻结了，之后的改动要走增量迁移文件（见 TD-01 §12），不再改 `schema.sql`。

---

## 七、常见问题

| 现象 | 原因与处理 |
| --- | --- |
| `psql: command not found` | 装 `postgresql-client`，**不是** apt 建议的 `postgresql-client-common` |
| `permission denied ... docker.sock` | 用户不在 docker 组，见二·2 |
| `make schema-check` 表数不是 28 | schema.sql 没执行。`make reset` 重来，看 `docker compose logs postgres` |
| 端口 5432 被占用 | 有旧容器：`docker rm -f kb-pg` |
| `pip install -e .` 报找不到 kb | `kb/` 目录被误删了，至少要有 `kb/__init__.py` |
| 测试报 `No module named 'builders'` | `pyproject.toml` 里的 `pythonpath = ["."]` 被改掉了 |
| 改了 DDL 但测试行为没变 | 忘了 `make reset` |
| `pip install -e .` 报 requires-python 不满足 | 用的 Python < 3.12。`python3 --version` 确认，或显式用 `python3.14 -m venv` |
| opencode 每条 docker 命令都要授权 | `sudo docker ...` 不匹配 `opencode.json` 的 allow 规则；把用户加进 docker 组 |

---

## 八、还没定的事

一件需要你拍板，不影响现在开工：

**A-12 首个连接器选哪个？**

| | SharePoint / Confluence | 本地文件夹 |
| --- | --- | --- |
| 契约测试 | 11 条全跑通 | 5 条会 skip |
| 接口定型质量 | 高 | 低，会被最简单的源带偏 |
| 第一个 demo | 慢 1–2 周 | 快 |

**建议 SharePoint**（同时有断开继承和显式 Deny，是契约的最强约束）。若急着做 demo 先做本地文件夹，**必须在第二个连接器时重构接口，不得拖到第三、四个**。

其余待决事项在决策台账（`decision-log.xlsx`，不在本包内，人用不给 agent）。
