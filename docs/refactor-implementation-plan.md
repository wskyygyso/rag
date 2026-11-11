# PHP 代码诊断 RAgent 重构实施方案

## 1. 实施原则

本计划依据 [`docs/refactor-architecture.md`](refactor-architecture.md) 执行，采用可独立验收、
可单独回滚的阶段式迁移。

- 每个阶段形成一个完整能力闭环，不同时拆成多个零散接口或辅助函数；
- 先建立行为基线和契约，再移动代码；
- 保持现有 API、Celery 任务名、任务状态和报告字段兼容；
- 任何阶段都不得为完整问题增加固定类、方法、表或答案映射；
- 每阶段同步更新测试、注释、架构文档、`progress.md` 和真实任务证据；
- 默认不主动 commit，用户审阅确认后使用中文提交说明提交。

## 2. 阶段总览

| 阶段 | 能力目标 | 主要产出 | 验收重点 |
|---|---|---|---|
| 21 | 行为基线与架构守卫 | 特征测试、契约文档、边界检查 | 当前 274 项测试和真实任务基线稳定 |
| 22 | 诊断编排拆分 | `DiagnosisPipeline`、阶段上下文、薄 Worker | Worker 只做队列适配，流程可独立执行 |
| 23 | 检索通道与后处理链 | 通道契约、结果模型、预算、后处理链 | 检索行为一致，可独立计时和降级 |
| 24 | 分析器插件化 | 共享证据模型、分析器注册表、拆分子系统 | 事实与证据无回归，新增分析器不改主流程 |
| 25 | 模型网关与降级 | 统一模型入口、候选路由、错误分类、熔断 | AI 失败不影响确定性报告，降级可观测 |
| 26 | Trace、SSE 与阶段评测 | Run/Span、阶段事件、检索与事实指标 | 从报告可回溯到通道、分析器和模型 |
| 27 | 工作台与生产验收 | 阶段时间线、质量看板、负载和故障演练 | 生产等价环境验收通过，可灰度发布 |

阶段 21 至 24 是结构性重构主路径。阶段 25 至 27 在结构稳定后增加参考项目中的模型治理、
全链路追踪和管理能力。

## 3. 阶段 21：行为基线与架构守卫

### 3.1 目标

冻结当前可接受行为，建立重构安全网，避免后续移动代码时改变证据排序、任务状态和业务结论。

### 3.2 实施内容

1. 固化 waky3、waky4、waky6 的代表性问题、期望证据文件、关键行、配置值和负向题；
2. 为 `run_diagnosis_task` 增加特征测试，记录各阶段顺序、取消检查、证据上限、警告和最终结果字段；
3. 为检索计划建立稳定 JSON 快照，允许耗时、Task ID、Trace ID 和动态时间字段变化；
4. 标记当前模块依赖矩阵，列出 `worker`、`business_analysis`、`retrieval_planner` 的入向和出向依赖；
5. 增加架构守卫测试，禁止领域分析器导入 FastAPI、Celery、Redis 或数据库连接池；
6. 更新 README 中的架构入口和当前开发规则。

### 3.3 变更范围

```text
tests/fixtures/refactor/
tests/test_refactor_contracts.py
tests/test_architecture_boundaries.py
docs/refactor-architecture.md
docs/refactor-implementation-plan.md
README.md
progress.md
```

### 3.4 验收标准

- 当前全量测试通过；
- 新增基线测试可在无外部 AI 网络时稳定运行；
- 负向问题保持零证据、零事实；
- 真实任务与基线在证据文件、业务事实和关键配置值上一致；
- 架构守卫能阻止领域层反向依赖基础设施；
- `git diff --check`、注释覆盖和 Python 编译通过。

## 4. 阶段 22：诊断编排拆分

### 4.1 目标

把 `app/worker.py` 从 1698 行级执行文件收敛为 Celery 适配器，将诊断流程改为可测试的阶段编排。

### 4.2 目标结构

```text
app/application/diagnosis/
├── context.py
├── events.py
├── pipeline.py
└── stages/
    ├── load_task.py
    ├── understand.py
    ├── retrieve.py
    ├── analyze.py
    ├── validate.py
    ├── narrate.py
    └── finalize.py
app/workers/
├── celery_app.py
└── diagnosis_tasks.py
app/worker.py                  # 兼容导出，后续按发布周期移除
```

### 4.3 实施内容

1. 定义 `DiagnosisContext`、`StageResult`、`StageStatus` 和 `DiagnosisEvent`；
2. 把任务加载、取消检查、软超时和终态写入封装成统一生命周期；
3. 先抽离编排主体，暂不改变检索函数和证据选择逻辑；
4. 每个阶段支持 mock 输入输出测试，不依赖 Celery 和真实模型；
5. `worker.py` 保留 `celery_app`、任务名、路由和同步入口，内部委托 `DiagnosisPipeline`；
6. 保留私有辅助函数导出或兼容包装，直到测试与调用方迁移完成；
7. 阶段失败统一映射为 `FAILED`，软超时、取消、业务错误和内部错误分别记录；
8. 在任务结果追加 `pipeline_metadata`，不删除旧字段。

### 4.4 验收标准

- `worker.py` 行数显著下降，只保留队列配置和任务适配；
- 不启动 Celery也能执行完整诊断管线；
- 创建、取消、重试、复制重跑和 SSE 行为不变；
- 阶段顺序、进度和现有前端状态兼容；
- 任一阶段抛错时任务仍进入明确终态且连接池释放；
- 关键真实任务证据数和确定性结论不下降。

## 5. 阶段 23：检索通道与后处理链

### 5.1 目标

将首轮检索、概念扩展、符号补全、配置定义、调用链和文档检索统一为可组合通道，将去重、
排序、门禁和证据预算统一为后处理链。

### 5.2 目标结构

```text
app/retrieval/
├── contracts.py
├── budget.py
├── engine.py
├── channels/
│   ├── code_text.py
│   ├── symbol.py
│   ├── config.py
│   ├── call_chain.py
│   ├── document.py
│   ├── history.py
│   └── validation.py
└── postprocessors/
    ├── deduplicate.py
    ├── attribution.py
    ├── relevance_rank.py
    ├── coverage.py
    ├── support_gate.py
    └── evidence_budget.py
```

### 5.3 实施内容

1. 建立统一 `RetrievalQuery`、`RetrievalHit`、`ChannelResult` 和 `RetrievalSummary`；
2. 把现有 `search_code_terms`、符号索引、配置定义和调用链包装为通道；
3. 把 `retrieval_planner.py` 的候选排序、概念覆盖、支撑门禁和配置定义词迁入明确策略；
4. 把 `worker.py` 的证据去重、预算和最终选择迁入后处理链；
5. 首批代码通道保持串行，避免并发全仓扫描争抢磁盘；文档、历史和只读验证可按预算并行；
6. 每通道记录 `elapsed_ms`、命中数、降级原因和返回候选数；
7. 最终证据预算仍为 64 条，召回上限不随时间无限扩大；
8. 输出兼容现有 `retrieval_plan` 字段，并追加 `channels` 和 `postprocessors` 摘要。

### 5.4 验收标准

- 通道可单独测试，不依赖完整 Worker；
- 任一可选通道失败时主流程可降级并保留警告；
- 同一输入下的证据集合与关键排序不劣于基线；
- 配置值仍通过定义行真实反查，不能由通道直接猜测；
- 支撑不足问题仍返回零扩展证据；
- `retrieval_plan` 可解释每个检索词和证据的来源；
- 大型项目检索耗时不超过阶段 21 基线的既定阈值。

## 6. 阶段 24：分析器插件化

### 6.1 目标

拆分 `business_analysis.py`，建立按可组合业务特征选择的分析器，使新增业务能力不再修改单一巨型注册链。

### 6.2 目标结构

```text
app/domain/diagnosis/
├── evidence.py
├── profile.py
├── finding.py
├── registry.py
└── analyzers/
    ├── withdrawal.py
    ├── exchange.py
    ├── device_registration.py
    ├── activity_reward.py
    ├── frontend_display.py
    ├── risk_control.py
    └── explicit_symbol.py
```

### 6.3 实施内容

1. 抽取 `EvidenceLine`、行查找、配置读取和小范围源码窗口工具；
2. 定义 `DiagnosticAnalyzer` 契约和注册表，以概念、动作、证据类别声明适用条件；
3. 逐组迁移提现、兑换、设备注册、活动奖励、前端展示、风控和显式符号分析器；
4. 每个分析器返回结构化 `Finding`，不直接生成 Markdown；
5. 保留现有事实结构、类别、证据引用和结论措辞兼容层；
6. 注册表先执行全部匹配分析器，再统一去重和置信度聚合；
7. 增加冲突策略：同一事实不同值时降低置信度并要求运行态验证，不能静默选择；
8. 用固定正负向用例验证无问题专属硬编码。

### 6.4 验收标准

- `business_analysis.py` 只保留兼容入口，不再包含全部业务实现；
- 新增分析器只需新建模块并注册，不修改 Pipeline 主流程；
- 设备注册、提现、兑换、活动奖励和前端展示事实与基线一致；
- 负向问题仍不生成事实；
- 每个事实可识别分析器 ID、证据文件、行号和证据边界；
- 分析器单测不依赖数据库、Redis、Celery 或真实 LLM。

## 7. 阶段 25：模型网关与稳定性

### 7.1 目标

在现有证据约束 AI 叙写之上增加统一模型网关，支持候选模型、超时、降级和熔断，同时保持确定性分析独立。

### 7.2 实施内容

1. 抽象 `ModelGateway`，封装 Responses API 与 Chat Completions；
2. 配置模型候选顺序、能力、首包/总超时、最大尝试次数和熔断阈值；
3. 对 429、5xx、连接超时和空响应分类；
4. 只重试幂等请求；若流式场景后续接入，必须避免重复拼接；
5. 使用 Redis 记录模型健康状态和短期熔断，键按模型与 Endpoint 隔离；
6. 结构化输出继续执行证据引用校验，非法引用使该次 AI 结果失败并降级；
7. `ai_metadata` 记录候选尝试、最终模型、错误类型、耗时和是否降级；
8. 增加模型故障注入测试，验证确定性报告仍可完成。

### 7.3 验收标准

- 任一外部模型不可用时任务仍可完成；
- 不使用无界重试，最坏耗时低于任务软超时；
- 熔断只影响模型叙写，不影响代码检索与确定性事实；
- 结构化输出和证据引用校验不弱化；
- 日志和任务结果不含密钥或完整敏感请求体。

## 8. 阶段 26：Trace、SSE 与阶段评测

### 8.1 目标

以统一阶段事件驱动 SSE、Trace 和评测，能够定位一次诊断的每个耗时和降级点。

### 8.2 实施内容

1. 新增诊断 Trace Run/Span 数据表和迁移；
2. 记录阶段、检索通道、后处理器、分析器、验证工具和模型调用；
3. Trace 输入输出只保存脱敏摘要与统计，不保存密钥和完整业务数据；
4. SSE 增加阶段事件和事件 ID，保留任务查询恢复；
5. 新增任务 Trace 查询 API，受项目权限控制；
6. 增加检索召回、配置值召回、证据引用完整率、分析器冲突和通道降级指标；
7. 前端展示阶段时间线、通道命中、分析器事实和 AI 降级原因；
8. 将离线评测扩展到阶段级，而不只评估最终回答。

### 8.3 验收标准

- 任一事实可从报告反查到证据、后处理器、通道和分析器；
- SSE 断线后可通过任务接口恢复终态；
- Trace 写入失败不阻断诊断主流程；
- 敏感字段脱敏测试通过；
- 阶段指标可接入现有 Prometheus 与质量看板；
- 真实任务可识别主要耗时和降级通道。

## 9. 阶段 27：工作台与生产验收

### 9.1 目标

完成重构后的操作界面和生产等价验收，支持灰度上线、回滚和持续质量观察。

### 9.2 实施内容

1. 工作台增加诊断阶段时间线、通道状态、分析器事实和证据过滤；
2. 增加重构前后并发、延迟、失败率和质量对比；
3. 执行大仓库性能、Redis/PostgreSQL 故障、模型超时和 Worker 中断演练；
4. 更新预检、监控规则、发布说明和回滚手册；
5. 按项目灰度启用新 Pipeline，可配置回退旧执行入口；
6. 灰度期间每日核对确定性与 AI 结论一致性；
7. 验收完成后移除兼容层和旧大文件中的空壳。

### 9.3 验收标准

- 全量测试、注释覆盖、编译、前端语法、Compose、迁移和发布门禁通过；
- waky3、waky4、waky6 代表性任务事实和证据不低于重构前；
- 负向问题零证据、零事实；
- 外部依赖故障不会生成错误业务结论；
- 新 Pipeline 可通过配置回退；
- 生产责任人完成监控、恢复、灰度和回滚签字。

## 10. 每阶段统一验证清单

```bash
.venv/bin/pytest -q
python3 scripts/check_comment_coverage.py
.venv/bin/python -m compileall -q app tests scripts
node scripts/check_frontend_syntax.js
git diff --check
docker compose config --quiet
```

还必须执行与当前阶段有关的额外验证：

- 数据库迁移：`docker compose run --rm --no-deps api python scripts/migrate_database.py --check`；
- 前端改动：浏览器验证加载、交互、SSE 终态和错误恢复；
- 检索改动：固定基准集和真实项目回归；
- 模型改动：无网络、超时、429、5xx 和非法引用故障注入；
- 生产编排改动：预检、烟雾测试、备份恢复和告警演练。

## 11. 提交与审阅规则

- 每个阶段结束先更新 `progress.md`；
- 默认不 commit，等待用户明确审阅确认；
- 用户确认后使用中文 commit 说明当前阶段成果；
- 一个 commit 只对应一个可验收阶段，不混入下一阶段重构；
- 发现行为回归时优先修复基线，不在错误基线上继续迁移。

## 12. 首轮执行建议

下一步从阶段 21 开始，只建立基线、契约和架构守卫，不移动生产代码。阶段 21 通过审阅后，
再进入阶段 22 拆分 `worker.py`。这样可以先证明重构没有改变现有诊断结果，再开始结构调整。
