-- 创建诊断任务操作审计表，记录创建、取消和重试等协作操作。
CREATE TABLE IF NOT EXISTS diagnostic_task_operations (
    id BIGSERIAL PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL REFERENCES diagnostic_tasks(id) ON DELETE CASCADE,
    operation VARCHAR(32) NOT NULL,
    from_status VARCHAR(32),
    to_status VARCHAR(32),
    actor VARCHAR(100),
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 按任务和时间倒序查询操作轨迹。
CREATE INDEX IF NOT EXISTS idx_task_operations_task_created
    ON diagnostic_task_operations (task_id, created_at DESC);

COMMENT ON TABLE diagnostic_task_operations IS '诊断任务创建、取消、重试等操作审计记录';
COMMENT ON COLUMN diagnostic_task_operations.actor IS '操作方标识，接入认证后应替换为可信身份';
