-- 加速项目 AI 质量看板按最近更新时间扫描已完成诊断任务。
CREATE INDEX IF NOT EXISTS idx_diagnostic_tasks_project_updated
    ON diagnostic_tasks (project, updated_at DESC);

COMMENT ON INDEX idx_diagnostic_tasks_project_updated IS 'AI 质量和任务历史按项目及更新时间查询索引';
