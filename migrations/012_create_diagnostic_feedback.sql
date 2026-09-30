-- 创建诊断报告人工反馈表，支持质量评估和转人工协作。
CREATE TABLE IF NOT EXISTS diagnostic_feedback (
    id BIGSERIAL PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL REFERENCES diagnostic_tasks(id) ON DELETE CASCADE,
    rating VARCHAR(32) NOT NULL CHECK (rating IN ('correct', 'inaccurate', 'insufficient_evidence', 'escalate')),
    comment TEXT,
    submitted_by VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 按任务和时间倒序查询反馈，避免报告页面读取全表。
CREATE INDEX IF NOT EXISTS idx_diagnostic_feedback_task_created
    ON diagnostic_feedback (task_id, created_at DESC);

COMMENT ON TABLE diagnostic_feedback IS '诊断报告人工反馈、质量评价和转人工记录';
COMMENT ON COLUMN diagnostic_feedback.rating IS '反馈类型：正确、不准确、证据不足或转人工';
COMMENT ON COLUMN diagnostic_feedback.comment IS '用户补充说明，不应填写密码、Token 等敏感信息';
