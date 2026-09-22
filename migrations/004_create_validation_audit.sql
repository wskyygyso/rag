CREATE TABLE IF NOT EXISTS validation_audit (
    id BIGSERIAL PRIMARY KEY,
    task_id VARCHAR(64),
    operation VARCHAR(50) NOT NULL,
    project VARCHAR(100),
    status VARCHAR(20) NOT NULL,
    request_summary JSONB,
    result_summary JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_validation_audit_task_created
    ON validation_audit (task_id, created_at DESC);

COMMENT ON TABLE validation_audit IS '只读验证工具审计表：记录查询操作、结果摘要和错误信息';
COMMENT ON COLUMN validation_audit.task_id IS '关联的诊断任务 ID，可为空';
COMMENT ON COLUMN validation_audit.operation IS '验证操作类型，例如 database_query、config_read 或 logs_search';
COMMENT ON COLUMN validation_audit.project IS '验证所属项目';
COMMENT ON COLUMN validation_audit.status IS '操作状态：SUCCESS 或 FAILED';
COMMENT ON COLUMN validation_audit.request_summary IS '脱敏后的请求摘要，不保存完整敏感参数';
COMMENT ON COLUMN validation_audit.result_summary IS '脱敏后的结果摘要，例如返回行数';
COMMENT ON COLUMN validation_audit.error IS '失败时的错误信息';
COMMENT ON COLUMN validation_audit.created_at IS '审计记录创建时间';
