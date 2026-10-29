-- 将 HTTP 请求 ID 持久化到诊断任务和安全审计，用于跨日志追踪。
ALTER TABLE diagnostic_tasks ADD COLUMN IF NOT EXISTS request_id VARCHAR(128);
ALTER TABLE security_audit ADD COLUMN IF NOT EXISTS request_id VARCHAR(128);

CREATE INDEX IF NOT EXISTS idx_tasks_request_id
    ON diagnostic_tasks (request_id);
CREATE INDEX IF NOT EXISTS idx_security_audit_request_id
    ON security_audit (request_id);

COMMENT ON COLUMN diagnostic_tasks.request_id IS '创建诊断任务的 HTTP 请求 ID';
COMMENT ON COLUMN security_audit.request_id IS '触发安全拒绝的 HTTP 请求 ID';
