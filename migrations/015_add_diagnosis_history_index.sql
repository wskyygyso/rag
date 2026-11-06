-- 加速项目历史任务按创建时间倒序分页和保留期清理。
CREATE INDEX IF NOT EXISTS idx_diagnostic_tasks_project_created
    ON diagnostic_tasks (project, created_at DESC);

COMMENT ON INDEX idx_diagnostic_tasks_project_created IS '项目历史任务按创建时间倒序查询索引';
