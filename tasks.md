# 阶段 39 任务清单：项目活动知识库与实体链接

- [x] **T1 活动知识库模块**
  - [x] 新增 `app/domain/diagnosis/activity_kb.py`：`ActivityRecord`、`ActivityLink`
  - [x] `build_activity_knowledge` 支持注入 `read_text`，扫描活动目录与全局配置
  - [x] 别名词、时间窗、等级结构、结构标记提取
  - [x] `load_activity_knowledge` 按代码指纹缓存

- [x] **T2 实体链接器**
  - [x] `link_activity` 目录/别名/等级名/结构意图打分
  - [x] 最低分与分差双门控，分差不足返回 `None` 或歧义
  - [x] `select_related_files` 按活动目录/全局配置选服务与控制器

- [x] **T3 知识库单测**
  - [x] 新增 `tests/test_activity_kb.py` 7 项构造源码测试
  - [x] 既有测试保持通过（`396 passed`）

- [x] **T4 工作流接入**
  - [x] `_select_diagnostic_evidence` 增加 `activity_kb_config` / `activity_kb_entry` 预算
  - [x] `execute_diagnosis` 构建知识库、链接活动、追加证据
  - [x] 链接配置强制进入完整窗口并整文件读取
  - [x] `retrieval_plan.activity_link` 审计字段

- [x] **T5 判定逻辑事实**
  - [x] `configuration.py` 新增 `activity_decision_source_facts`
  - [x] `business_analysis.py` 接入判定事实与“未链接活动”缺口门控

- [x] **T6 验收与回归**
  - [x] `pytest -q` 全绿 `396 passed`
  - [x] `check_comment_coverage.py` 通过
  - [x] `check_frontend_syntax.js` 通过
  - [x] `git diff --check` 通过
  - [x] 重建 api/worker，重跑 `/tmp/run_acceptance.py`：`23/24`，唯一失败 B3 为数据缺口

- [x] **T7 进度记录**
  - [x] 更新 `progress.md`：完成范围、验证结果、B3 数据缺口与下一步
  - [x] 等待用户审阅，不主动 commit
