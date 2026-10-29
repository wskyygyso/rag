-- 将 HTTP 请求 ID 持久化到任务操作审计。
ALTER TABLE diagnostic_task_operations ADD COLUMN IF NOT EXISTS request_id VARCHAR(128);

CREATE INDEX IF NOT EXISTS idx_task_operations_request_id
    ON diagnostic_task_operations (request_id);

COMMENT ON COLUMN diagnostic_task_operations.request_id IS '触发创建、取消或重试操作的 HTTP 请求 ID';
