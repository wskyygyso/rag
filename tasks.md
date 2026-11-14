# 阶段 41 任务清单：可审计的反馈活动别名表

- [x] **T1 数据模型与迁移**
  - [x] 新增 `migrations/026_create_project_activity_aliases.sql`，含表、唯一约束、索引与中文备注
  - [x] 确认迁移版本连续且能通过 `tests/test_migrations.py`

- [x] **T2 存储模块**
  - [x] 新增 `app/activity_aliases.py`：别名/活动目录规范化与校验
  - [x] `upsert_activity_alias`、`list_activity_aliases`、`set_activity_alias_status`
  - [x] `load_approved_activity_aliases` 数据库异常降级为空映射
  - [x] 中文注释覆盖职责、参数业务含义、返回值与失败条件

- [x] **T3 活动链接接入**
  - [x] `ActivityLink` 新增 `feedback_alias` 字段
  - [x] `link_activity` 新增 `approved_aliases` 参数与最长别名优先匹配
  - [x] 目标活动缺失时忽略别名；同长度冲突返回歧义

- [x] **T4 工作流接入**
  - [x] 诊断执行前读取已启用别名并传入 `link_activity`
  - [x] `retrieval_plan.activity_link` 增加 `feedback_alias` 审计字段
  - [x] 别名读取失败只记录告警，不中断诊断

- [x] **T5 API**
  - [x] `POST /api/knowledge/{project}/activity-aliases` 登记
  - [x] `GET /api/knowledge/{project}/activity-aliases` 列表
  - [x] `POST /api/knowledge/{project}/activity-aliases/{id}/status` 启用/停用
  - [x] `GET /api/knowledge/{project}/activity-candidates` 候选活动查询
  - [x] 登记前校验项目权限与目标活动存在，知识库不可用返回 503

- [x] **T6 前端**
  - [x] 项目知识库面板新增“人工反馈别名”区块
  - [x] 候选活动查询与一键填入目标活动
  - [x] 别名列表、启用/停用、摘要与错误提示
  - [x] 项目切换与刷新时同步更新，JSDoc 注释完整

- [x] **T7 测试与回归**
  - [x] 新增 `tests/test_activity_aliases.py`（规范化、存储、API）
  - [x] `tests/test_activity_kb.py` 新增别名链接、缺失目标、歧义、未传参兼容
  - [x] `.venv/bin/python -m pytest -q` 全绿
  - [x] `python3 scripts/check_comment_coverage.py`
  - [x] `node scripts/check_frontend_syntax.js`
  - [x] `git diff --check`

- [x] **T8 真实验收与文档**
  - [x] 登记 `鉴定大师 → gemMaster` 后重跑 24 题验收（目标 `24/24`）
  - [x] 抽查 `activity_link.feedback_alias` 审计字段
  - [x] 更新 `progress.md`，等待用户审阅，不主动 commit
