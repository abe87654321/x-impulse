---
name: skill-library
description: Router for the ECC library skills that are not auto-loaded in this project. Use when a task needs a skill outside the DAILY set (database/Postgres migration work, deployment, security audits, research, planning, ECC tooling). Triggers include "ECC skill", "library skill", "off-stack skill", "有没有 XX 技能", "查文档", "调研", "部署方案", "审计".
metadata:
  origin: ECC
  bucket: LIBRARY
---

# ECC Library Router (x-impulse)

This project is a **Python 3.12 backend** (pytest / ruff / mypy, PostgreSQL 15, Qdrant).
The default ECC install ships 200+ skills, most of them for other stacks. Those are
**denied** from the skill tool in this project so the session prompt stays small.

The files are still on disk. To use one, read its `SKILL.md` directly:

```
~/.config/opencode/skills/<name>/SKKILL.md
```

Read it with the Read tool, then follow it. Do not try to load it via the skill tool —
it is denied by design.

## Groups

### 数据库 / Schema / 迁移
| Skill | Path under `~/.config/opencode/skills/` | When |
| --- | --- | --- |
| database-migrations | `database-migrations/SKILL.md` | 迁移文件设计（TD-01 §12 冻结后） |
| postgres-patterns | `postgres-patterns/SKILL.md` | 索引、查询、RLS 评估（注意 I-6：禁原生 ENUM） |
| mysql-patterns | `mysql-patterns/SKILL.md` | 本项目不用，仅对照 |

### 安全 / 审计
| Skill | Path | When |
| --- | --- | --- |
| security-scan | `security-scan/SKILL.md` | 扫 opencode/claude 配置里的密钥 |
| security-bounty-hunter | `security-bounty-hunter/SKILL.md` | 对外暴露面审计 |
| production-audit | `production-audit/SKILL.md` | Phase 2 上线前就绪核查 |
| llm-trading-agent-security | `llm-trading-agent-security/SKILL.md` | 含「禁止运行时降级」类模式，可对照 I-10 |

### 规划 / 决策
| Skill | Path | When |
| --- | --- | --- |
| blueprint | `blueprint/SKILL.md` | 多会话任务蓝图 |
| architecture-decision-records | `architecture-decision-records/SKILL.md` | 决策留痕（项目另有 `decision-log.xlsx`，人工维护） |
| intent-driven-development | `intent-driven-development/SKILL.md` | 需求模糊时先定验收口径 |
| council | `council/SKILL.md` | 争议决策（A-12 / Q-16） |
| dev-team | `dev-team/SKILL.md` | 多角色评审方案 |

### 上下文 / 成本
| Skill | Path | When |
| --- | --- | --- |
| context-budget | `context-budget/SKILL.md` | 上下文占用体检 |
| strategic-compact | `strategic-compact/SKILL.md` | 阶段边界手动压缩 |
| token-budget-advisor | `token-budget-advisor/SKILL.md` | 用户要求控制回答长度 |
| cost-tracking | `cost-tracking/SKILL.md` | 模型用量/花费 |
| cost-aware-llm-pipeline | `cost-aware-llm-pipeline/SKILL.md` | 检索/生成的模型分层 |

### 调研 / 文档
| Skill | Path | When |
| --- | --- | --- |
| research-ops | `research-ops/SKILL.md` | 外部事实调研 |
| deep-research | `deep-research/SKILL.md` | 多源深度调研 |
| exa-search | `exa-search/SKILL.md` | 代码/资料检索 |
| documentation-lookup | `documentation-lookup/SKILL.md` | 库 API 现行文档（DAILY，亦可直读） |

### 部署 / 运维
| Skill | Path | When |
| --- | --- | --- |
| deployment-patterns | `deployment-patterns/SKILL.md` | CI/CD 与回滚 |
| docker-patterns | `docker-patterns/SKILL.md` | 镜像与 Compose 加固 |
| kubernetes-patterns | `kubernetes-patterns/SKILL.md` | K8s 部署 |
| git-workflow | `git-workflow/SKILL.md` | 分支与提交规范 |
| safety-guard | `safety-guard/SKILL.md` | 破坏性操作防护 |

### ECC 自身管理
| Skill | Path | When |
| --- | --- | --- |
| agent-sort | `agent-sort/SKILL.md` | 再次精简 ECC 安装 |
| configure-ecc | `configure-ecc/SKILL.md` | 安装/更新 ECC |
| ecc-guide | `ecc-guide/SKILL.md` | 查 ECC 当前能力面 |
| ecc-recipes | `ecc-recipes/SKILL.md` | 查 ECC 命令编排 |
| workspace-surface-audit | `workspace-surface-audit/SKILL.md` | 审计仓库与 harness 面 |
| config-gc | `config-gc/SKILL.md` | 清理 Claude/opencode 配置 |
| skill-stocktake | `skill-stocktake/SKILL.md` | 技能质量审计 |
| skill-scout | `skill-scout/SKILL.md` | 找新技能 |
| skill-comply | `skill-comply/SKILL.md` | 校验技能是否被遵守 |
| prompt-optimizer | `prompt-optimizer/SKILL.md` | 优化提示词 |
| continuous-learning-v2 | `continuous-learning-v2/SKILL.md` | 经验沉淀（项目另有 checkpoint.md） |
| rules-distill | `rules-distill/SKILL.md` | 从技能提炼规则 |

### 明确不适用于本项目
以下技能与本仓库栈无关，已在 `opencode.json` 中 deny（文件仍在磁盘，需要时直读）：
C++ / Go / Java / Kotlin / PHP / Rust / Swift / Flutter / Laravel / Spring / Django /
Quarkus / React / Next.js / Three.js（Blender）、视频（Remotion / Manim / video-editing）、
营销（content-engine / social-publisher / marketing-campaign / SEO / brand-*）、
IoT 计算（ito-*）、加密交易与预测市场（prediction-market-* / defi-* / llm-trading-*）、
医疗合规（healthcare-* / hipaa-*）、网络运维（cisco-* / homelab-* / netmiko-*）、
物流与行业运营（carrier-* / logistics-* / healthcare-cdss-* 等）。

> 例外：若将来本项目真要接入某类源（如 Kafka、Elasticsearch），先在
> `opencode.json` 的 `permission.skill` 里放行对应技能，再直接使用。
