# 阶段 42 任务清单：生产交付门禁复跑与核心表覆盖修复

- [x] **T1 预检核心表修复**
  - [x] `diagnosis_trace_spans`、项目知识库三表、人工别名表纳入 `REQUIRED_TABLES`
  - [x] 按用途分组补充中文注释和缺失影响说明
  - [x] `tests/test_preflight.py` 通过

- [x] **T2 迁移门禁**
  - [x] `migrate_database.py --check`：pending=0 drifted=0
  - [x] 全新空库执行 26 个迁移，17 张表齐全，含 `project_activity_aliases`

- [x] **T3 生产等价预检**
  - [x] production、认证、独立只读验证库、仓库根目录全部注入
  - [x] 16 项检查全部 PASS，核心表检查覆盖新增表

- [x] **T4 运行时门禁**
  - [x] 烟雾测试 readiness、认证项目、数据库指标、项目看板 4 项 PASS
  - [x] 性能基线 100 请求 0 错误，P95 47.544 ms
  - [x] 生产 Compose 注入必需变量后 `config` 通过

- [x] **T5 备份与恢复演练**
  - [x] 生成备份并完成 SHA-256 校验
  - [x] 隔离库 `pg_restore` 约 40s，核心表行数与主库一致
  - [x] 恢复演练后删除隔离库
  - [x] 保留期清理 dry-run 候选为 0

- [x] **T6 文档与回归**
  - [x] 更新 `PRODUCTION_CHECKLIST.md` 演练证据和勾选状态
  - [x] 更新 `progress.md` 阶段 42 记录
  - [x] 全量 pytest、注释覆盖、前端语法、`git diff --check` 通过
  - [x] 提交代码（推送受网络阻塞，待恢复后补推）
