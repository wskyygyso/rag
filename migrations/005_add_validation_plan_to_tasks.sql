ALTER TABLE diagnostic_tasks
    ADD COLUMN IF NOT EXISTS validation_plan JSONB;

COMMENT ON COLUMN diagnostic_tasks.validation_plan IS '任务创建时提交的只读数据库、配置和日志验证计划';
