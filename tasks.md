# 阶段 37 任务清单：配置问答长尾修复与题库第一轮全绿

> 规格见 [spec.md](spec.md)。每个任务完成后立即更新状态；全部完成后统一执行回归并更新 `progress.md`。

## [x] T1. 完整配置窗口与同目录配置证据

**产出**
- `app/diagnosis_workflow.py::_full_config_window_files` 返回路径统一
  `replace("\\", "/").casefold()`，最多 3 个，优先问题英文标识命中的活动。
- `_sibling_config_evidence` 占位项补 `line_start/line_end` 与类型标记。
- `_select_diagnostic_evidence` 为 `sibling_config` 预留最多 6 条独立预算。
- 新增 `_activity_service_evidence`，为只有配置证据的活动补 `service/controller/Boot`。

**验收**
- 单测/离线探针确认驼峰目录 `config.php` 能读到完整文件；
- A-12 证据包含 `gameRanking/config.php` 全文。

## [x] T2. 活动展示词与目录选择

**产出**
- `configuration.py` 新增 `_CHINESE_DIRECTORY_HINTS`、`_activity_display_terms`、
  `_activity_alias_score`。
- `scoped_config_evidence` 与 `_dominant_activity_lines` 接入展示词评分。
- `_scope_identifier` 支持“新版/旧版”通用词根。

**验收**
- 中文名只出现在 pushmsg/注释时，多活动证据能选中正确目录；
- D-12 在 `giftWeekStar` 与 `giftWeekStarNew` 并存时选 2026；
- H-2/H-3 选中 `guildTreasureHunt` 而非 `guildVsMonsters`。

## [x] T3. 枚举问法与候选排序

**产出**
- `question_type.py` 把“哪些/哪几/有哪几种/监听哪些/第 N 名”路由为枚举。
- `config_graph.py` 增补 `_KEY_INTENT_ALIASES`；`enumeration_facts` 门控与排序；
  `_count_facts` 支持列举问法输出数量与成员值。
- `_enumeration_candidates` 区分“第 N 名”和“第 N 档/级/个”，排除无关 `day_rank`。

**验收**
- A-12 输出 11 与 allow_types 成员；
- D-14 输出 `levels.limit=1500000`，不再返回 rank=3；
- D-7/D-8 命中 `rank_prizes`。

## [x] T4. 奖品引用与布尔过滤

**产出**
- `config_graph.py` 新增 `_resolve_prize_references`，把 `id` 解析到同文件奖品表。
- `_render_levels` 支持 `is_can_jump_x=1` 过滤并输出 `name.zh`。
- 问题含“奖励”时优先渲染奖品名称/金额，不只输出 id。

**验收**
- A-13 前 10 名呈现 100/60/40/20/10 美元与 80000/60000/50000/40000/30000 金币；
- H-3 呈现 Bubble/Room background/Entrance show；
- H-11 只返回佩特拉古城、长城、吴哥窟。

## [x] T5. 活动服务与数据库证据

**产出**
- `_activity_service_evidence` 补入 `Logic/<活动>/service/*.php` 等文件。
- 针对 A-18/A-19：从服务文件中提取数据库表读取与 `existsByVol` 等幂等判定方法，
  作为确定性事实；找不到时明确报告证据缺口。

**验收**
- A-18 结论包含“数据库”；
- A-19 结论包含 `existsByVol`。

## [x] T6. 单测与回归

**产出**
- 新增 6 个构造源码回归测试（见 spec 3.5）。

**验收**
- `.venv/bin/python -m pytest -q` 全绿；
- `python3 scripts/check_comment_coverage.py`、`node scripts/check_frontend_syntax.js`、
  `git diff --check` 通过。

## [x] T7. 真实 E2E 与进度更新

**产出**
- 重建 `api`/`worker` 镜像并重启；
- 全量重跑 `/tmp/run_diag_round1.py`，记录通过数与剩余失败题；
- H-7 单独重跑记录耗时；
- 更新 `progress.md` 阶段 36/37 的完成范围、验证结果、剩余项和下一步。

**验收**
- A/D/H 通过数 ≥ 42，指定必过题全部通过；
- 未通过题均有明确证据缺口说明，无错误数值。
