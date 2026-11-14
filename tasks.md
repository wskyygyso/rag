# 阶段 40 任务清单：扩展活动知识库结构化事实

- [x] **T1 知识库结构化解析**
  - [x] 新增 `ActivityLevel` / `ActivityPrize` dataclass 与 `ActivityRecord` 字段
  - [x] 解析 `vol`/`year` 及行号
  - [x] 解析 `levels` / `level` 等级表（序号、门槛、名称、奖励引用）
  - [x] 解析顶层 `prizes` 奖励定义（id、name、type、num、days、行号）
  - [x] 注释与安全边界说明同步更新

- [x] **T2 后台活动登记与覆盖**
  - [x] 枚举 `Logic/<name>/boot/config.php` 活动配置
  - [x] 按 `crontab > Logic > action_activity > config > conf` 同名去重
  - [x] 计算 `backend_registered` / `backend_config_file`

- [x] **T3 工作流交接**
  - [x] `activity_link_payload` 增加期次、等级表、奖励表、后台登记字段
  - [x] 保持歧义链接不产生可用事实

- [x] **T4 确定性事实模块**
  - [x] 新增 `app/domain/diagnosis/analyzers/activity_kb_facts.py`
  - [x] `activity_structured_facts`：期次、后台登记
  - [x] `activity_level_table_facts`：等级名称/门槛/奖励名逐级事实
  - [x] 奖励引用按 `prizes` 快照解析，跳过无名奖励

- [x] **T5 问答接入**
  - [x] `business_analysis.py` 期次/后台事实分支
  - [x] 等级枚举为空时用知识库等级表兜底
  - [x] 门槛缺口门控改为知识库事实存在时不缺口
  - [x] 结论拼接 `活动结构化事实`

- [x] **T6 单测与真实抽样**
  - [x] `tests/test_activity_kb.py` 新增期次、等级表、奖励引用、后台登记、去重、降级测试
  - [x] 真实 waky3 抽样：`loveBall` / `gemMaster` / `noble` 字段符合预期

- [x] **T7 回归与进度记录**
  - [x] `.venv/bin/python -m pytest -q`（401 passed）
  - [x] `python3 scripts/check_comment_coverage.py`
  - [x] `node scripts/check_frontend_syntax.js`
  - [x] `git diff --check`
  - [x] 重建 api/worker 后重跑 24 题验收：`23/24`，唯一失败 B3 为数据缺口
  - [x] 更新 `progress.md`，等待用户审阅，不主动 commit
