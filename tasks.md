# 阶段 35 任务清单：自动派生的项目知识库

> 规格见 [spec.md](spec.md)。每个任务完成后立即更新状态；全部完成后统一执行回归并更新 `progress.md`。

## [x] T1. 数据模型迁移

**产出**
- 新建 `migrations/025_create_project_knowledge.sql`，包含 `project_glossary`、`project_config_structure`、`project_verified_conclusions` 三表、唯一键、查询索引和逐字段中文备注。

**验收**
- 文件编号连续，`load_migrations` 不报错；
- `pytest tests/test_migrations.py` 通过。

**依赖**：无。

## [x] T2. 术语抽取接入符号索引

**产出**
- 修改 `app/php_index.py`：`parse_php_source` 返回 `glossary`；`index_repository` 汇总 `glossary`。
- 修改 `app/project_index_cache.py`：`decode_index` 兼容可选 `glossary`。
- 中文注释识别规则与单文件 200 条上限按 spec 4.2 执行。

**验收**
- 造一个含中文注释 + 类声明 + 配置键的临时 PHP 文件，`index_repository` 能产出术语；
- 无中文注释的仓库返回空 `glossary`，不报错；
- `pytest tests/test_php_index.py tests/test_project_index_cache.py` 通过。

**依赖**：无。

## [x] T3. 知识库核心模块

**产出**
- 新建 `app/project_knowledge.py`，实现 spec 4.1 的函数清单与降级策略。

**验收**
- 指纹一致跳过、指纹变化替换、条目数变化替换；
- 敏感配置值不落库；
- 结论候选状态流转正确；
- 所有函数中文注释齐全。

**依赖**：T1、T2。

## [x] T4. 术语扩展优先查知识库

**产出**
- 修改 `app/term_expansion.py`：新增 `glossary_terms` 参数、`glossary_hit` 状态与 `glossary_term_count`。
- 修改 `app/diagnosis_workflow.py`：查询知识库并传入扩展；沉淀模型扩展词；落库术语表；写 `retrieval_plan` 知识库元数据。

**验收**
- 知识库命中时不调用模型；
- 未命中时行为与阶段 34 完全一致；
- 知识库异常只产生 warning；
- `pytest tests/test_generic_config_engine.py tests/test_diagnosis_pipeline.py` 通过。

**依赖**：T3。

## [x] T5. 配置结构与结论候选落库

**产出**
- `app/diagnosis_workflow.py`：在确定性结论生成后调用 `record_config_structure` 与 `save_candidate_conclusion`；候选结论只写入 `retrieval_plan.candidate_conclusions`。
- `app/feedback_api.py`：反馈写入后调用 `apply_feedback_to_conclusion`。

**验收**
- `conclusion` 不包含候选结论内容；
- 反馈 `correct` → `verified`，`inaccurate` → `rejected`；
- `pytest tests/test_feedback_api.py tests/test_diagnosis_pipeline.py` 通过。

**依赖**：T3、T4。

## [x] T6. 知识库查询与重建 API

**产出**
- 新建 `app/knowledge_api.py`，实现 5 个端点；
- `app/project_knowledge.py` 增加 `rebuild_glossary_from_code_symbols`；
- `app/main.py` 注册路由；
- `app/settings.py` 与 `.env.example` 增加 `project_knowledge_enabled`。

**验收**
- 数据库不可用时返回 503；
- `rebuild` 不扫描仓库、只读 `code_symbols`；
- 新增 API 测试通过。

**依赖**：T3。

## [x] T7. 前端知识库卡片

**产出**
- `static/index.html` 增加知识库卡片、摘要加载、术语查询、重建按钮。

**验收**
- `node scripts/check_frontend_syntax.js` 通过；
- 页面能显示三类计数与指纹状态，查询和重建有成功/失败提示。

**依赖**：T6。

## [x] T8. 测试与回归

**产出**
- 新建 `tests/test_project_knowledge.py`，覆盖 spec 第六节全部场景。
- 更新 `progress.md` 阶段 35 记录。

**验收**
- 执行并通过：

```bash
.venv/bin/python -m pytest -q
python3 scripts/check_comment_coverage.py
node scripts/check_frontend_syntax.js
git diff --check
```

**依赖**：T1–T7。

## [x] T9. 真实 E2E 回归（需重建镜像）

**步骤**
1. `docker compose build api worker && docker compose up -d api worker`
2. 以 `use_ai=false` 依次诊断：
   - 游戏榜前 10 名分别奖励什么？
   - 测试环境中每个 vip 等级附赠的奖品是哪些？将每个等级列出来
   - waky3 的 FIFA2026 活动奖池基础值是多少？送礼积分按多少比例进奖池？
   - fupay 的商户号在配置里怎么写的？
3. 对比阶段 34 结论，确认 `result.retrieval_plan` 出现 `glossary` 与 `candidate_conclusions` 元数据。

**验收**
- 四条结论与阶段 34 一致；
- 知识库条数随诊断增长；
- 无新增敏感值出现在结论或知识库接口。

**依赖**：T1–T8、Docker 环境。

## 最终交付检查

- [x] 迁移编号连续、中文备注齐全
- [x] 所有新增函数参数/返回值/异常中文注释齐全
- [x] 知识库失败可降级
- [x] 候选结论不进入 `conclusion`
- [x] 测试、注释覆盖、前端语法、diff 检查全部通过
- [x] `progress.md` 已更新
- [ ] 等待用户确认后再中文提交推送
