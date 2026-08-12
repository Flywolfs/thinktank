# P0.3 双路线融合方案 — Plan 决策层设计

> 日期: 2026-08-06
> 状态: 设计稿（待实现）
> 背景: P0.3 对比完成——路线B（Docker Hermes）plan 质量更高（queries 可执行性/维度完整度/方法论引用），
>       路线A（自研 plan_node）优势在速度/确定性/离线可跑。融合不是二选一，而是**取长补短**。

---

## 一、对比结论回顾（P0.3 实测数据）

| 维度 | 路线A (plan_node v2) | 路线B (Docker Hermes) |
|------|---------------------|----------------------|
| plan 质量 | 维度合理但 queries 泛 | **queries 带具体人名/金额/动作，可直接执行** |
| 维度完整度 | 偶缺关键维度（蒋方舟缺资金链条） | **更完整**（利益链条/同类对比/大环境关联） |
| 方法论引用 | 有时只有章节号 | **多章节组合，引用完整** |
| 速度 | **快**（23-27s） | 慢（36-57s，约 1.5-2x） |
| 确定性 | **强**（JSON schema 约束） | 弱（需二次解析/校验） |
| 离线可用 | **可**（DeepSeek API 即可） | 否（依赖 Docker 容器） |
| 成本 | 5 次 LLM 调用 | 1 次 agent 调用（内部多轮） |

**结论**：路线B 的 queries 生成能力是核心增量——它把"搜索什么"变成了"深扒级线索"（例：`罗永浩 6亿 债务 偿还 真还传 进度` vs 路线A 的 `罗永浩 债务 金额 债权人 明细`）。这正是决定后续 search 阶段命中率的关键。

## 二、融合架构：PlanProvider 抽象层

```
┌─────────────────────────────────────────────────────────┐
│                     plan_node (图节点)                    │
│  不变：接收 state → 输出统一维度模板 JSON → 进入执行循环    │
└────────────────────────┬────────────────────────────────┘
                         │ 委托
                         ▼
┌─────────────────────────────────────────────────────────┐
│              PlanProvider (策略选择器)                    │
│  模式: auto | hermes | local（配置可切换）                │
│  auto: 优先 Hermes → 失败/超时/校验不过 → fallback local  │
└───────────────┬─────────────────────┬───────────────────┘
                │                     │
                ▼                     ▼
┌──────────────────────────┐  ┌──────────────────────────┐
│  HermesPlanProvider       │  │  LocalPlanProvider        │
│  (路线B: Docker API 8643) │  │  (路线A: 现有5步推理)      │
│  - 调 /v1/chat/completions │  │  - _plan_analyze_type     │
│  - 已加载方法论 skill      │  │  - _plan_generate_...     │
│  - 输出解析+降级兜底       │  │  - _plan_self_check ...   │
└───────────────┬──────────┘  └──────────────┬───────────┘
                │                             │
                └─────────────┬───────────────┘
                              ▼
┌─────────────────────────────────────────────────────────┐
│               PlanValidator (统一校验器)                  │
│  两条路线的输出都必须通过:                                │
│  1. 结构校验: dimensions 数组、每项含 name/methodology/  │
│     rationale/queries/priority                          │
│  2. 方法论合法性: methodology_source 必须在映射表 18 维度 │
│  3. 基线档案第一: dimensions[0] 必须是基线档案           │
│  4. query 具体性: 每维 ≥2 个 query，含实体名或具体线索   │
│  5. 数量: 3-7 个维度；priority ∈ {high,medium,low}       │
│  校验失败 → 标记来源 + 尝试修复或降级                     │
└─────────────────────────────────────────────────────────┘
```

## 三、关键设计决策

### 3.1 模式切换（配置驱动）

```yaml
# config.yaml 或 .env
PLAN_PROVIDER: auto        # auto | hermes | local
HERMES_API_URL: http://localhost:8643
HERMES_API_KEY: <deploy/intel-planner/.env 的 HERMES_INTEL_API_KEY>
HERMES_PLAN_TIMEOUT: 180   # 秒，Hermes agent 生成慢，超时后降级
```

- `auto`（默认）：Hermes 优先，健康检查失败/超时/输出无法解析 → 自动降级 local
- `hermes`：强制 Hermes，失败则报错（不静默降级）
- `local`：只用自研（离线环境/成本敏感）

### 3.2 校验器是融合的核心

不管哪条路线，输出必须过同一套 PlanValidator。校验失败时的处理：
1. **轻微问题**（如 query 只有 1 个）：自动补全（用实体名拼接）
2. **中等问题**（如缺 priority）：默认 medium
3. **严重问题**（如方法论来源非法/基线不在第一）：尝试修复；修复失败 → 降级另一条路线

这保证：**Hermes 的聪明 + 系统的确定性约束**。Hermes 输出自由文本，系统负责把它变成可执行的蓝图。

### 3.3 审计与日志

- 每条 plan 记录 `provider` 字段（hermes/local）+ 耗时 + 校验结果
- 日志保留两条路线的完整中间状态（决策可追溯）
- data/plans_compare/ 作为对比基准持续积累

## 四、实现步骤（P0.3 实施顺序）

1. **新建 `shared/plan/provider.py`** — PlanProvider 抽象 + 策略选择器（auto/hermes/local）
2. **新建 `shared/plan/hermes_provider.py`** — Hermes API 调用封装（httpx，读 .env 的 key，超时控制，JSON 解析）
3. **新建 `shared/plan/validator.py`** — PlanValidator（结构/方法论合法性/基线第一/query 具体性）
4. **重构 `entity_intel/graph.py` 的 plan_node** — 改为调用 PlanProvider（保留现有 5 步推理为 LocalPlanProvider）
5. **验证**：三案例（蒋方舟/罗永浩/韩红）× auto 模式，确认：Hermes 正常时走 Hermes；容器停掉时自动降级 local
6. **前端/API**：可选——暴露 plan_provider 参数让用户选择模式

## 五、后续扩展（不在本次范围）

- **P1.1 Tool Registry**：plan 执行时 LLM 动态选工具（与本次正交）
- **P2.1 动态工具创建**：Hermes 写爬虫注册进系统（复用 HermesPlanProvider 的连接）
- **queries 增强回灌**：把 Hermes 生成的优质 queries 作为 few-shot 示例注入 local 路线，逐步拉平差距

## 六、风险与对策

| 风险 | 对策 |
|------|------|
| Hermes 容器挂了 → plan 失败 | auto 模式自动降级 local；health check 先行 |
| Hermes 输出非 JSON | 解析失败 → 降级 local（不阻塞调查） |
| 成本上升（Hermes 1 次 > local 5 次） | 默认 auto；成本敏感环境可配置 local |
| 校验器过严/过松 | 校验规则集中在 validator.py，可调参数化 |

---

## 附：P0.3 实测数据存档

`data/plans_compare/` 已存 6 份 JSON（3 案例 × 2 路线），作为融合效果回归基准。
