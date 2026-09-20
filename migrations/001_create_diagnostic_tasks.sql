CREATE TABLE IF NOT EXISTS diagnostic_tasks (
    id VARCHAR(64) PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    question TEXT NOT NULL,
    environment VARCHAR(100) NOT NULL,
    status VARCHAR(32) NOT NULL,
    progress INT NOT NULL DEFAULT 0,
    current_step VARCHAR(255),
    result JSONB,
    error TEXT,
    retry_count INT NOT NULL DEFAULT 0,
    created_by VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE diagnostic_tasks IS '诊断任务主表：记录用户问题、异步执行状态、进度和最终结果';
COMMENT ON COLUMN diagnostic_tasks.id IS '诊断任务唯一 ID，建议使用 diag_ 前缀';
COMMENT ON COLUMN diagnostic_tasks.project IS '待诊断的代码项目标识';
COMMENT ON COLUMN diagnostic_tasks.question IS '用户提交的原始问题描述';
COMMENT ON COLUMN diagnostic_tasks.environment IS '诊断目标环境，例如 development、test 或 production-readonly';
COMMENT ON COLUMN diagnostic_tasks.status IS '任务状态：CREATED、QUEUED、RUNNING、SEARCHING、VERIFYING、REPORTING、COMPLETED、FAILED、CANCELLED';
COMMENT ON COLUMN diagnostic_tasks.progress IS '任务进度百分比，范围为 0 到 100';
COMMENT ON COLUMN diagnostic_tasks.current_step IS '当前执行步骤，供前端展示任务进度';
COMMENT ON COLUMN diagnostic_tasks.result IS '诊断完成后的结构化报告 JSON';
COMMENT ON COLUMN diagnostic_tasks.error IS '任务失败时的错误信息';
COMMENT ON COLUMN diagnostic_tasks.retry_count IS '任务重试次数';
COMMENT ON COLUMN diagnostic_tasks.created_by IS '创建任务的用户或系统标识';
COMMENT ON COLUMN diagnostic_tasks.created_at IS '任务创建时间';
COMMENT ON COLUMN diagnostic_tasks.updated_at IS '任务最后更新时间';
