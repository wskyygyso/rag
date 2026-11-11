-- 诊断阶段轨迹用于 SSE 恢复、耗时定位和降级审计。这里只保存阶段与脱敏统计，
-- 不保存完整问题、源码正文、模型请求体、Cookie、数据库连接串或 API Key。
CREATE TABLE IF NOT EXISTS diagnosis_trace_spans (
    id BIGSERIAL PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL REFERENCES diagnostic_tasks(id) ON DELETE CASCADE,
    event_type VARCHAR(64) NOT NULL,
    stage VARCHAR(64) NOT NULL,
    status VARCHAR(24) NOT NULL,
    elapsed_ms DOUBLE PRECISION,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- SSE 和 Trace API 都按任务和自增 ID 顺序增量读取；联合索引避免跨任务扫描。
CREATE INDEX IF NOT EXISTS idx_diagnosis_trace_task_created
    ON diagnosis_trace_spans (task_id, id);

COMMENT ON TABLE diagnosis_trace_spans IS '诊断任务的阶段时间线、耗时和脱敏降级统计';
COMMENT ON COLUMN diagnosis_trace_spans.id IS '单调递增轨迹 ID，SSE 用它做断线增量恢复';
COMMENT ON COLUMN diagnosis_trace_spans.task_id IS '所属诊断任务，任务删除时轨迹级联清理';
COMMENT ON COLUMN diagnosis_trace_spans.event_type IS '稳定事件类型，例如 stage.started 或 stage.completed';
COMMENT ON COLUMN diagnosis_trace_spans.stage IS '诊断阶段标识，例如 retrieval、validation、analysis 或 ai_narration';
COMMENT ON COLUMN diagnosis_trace_spans.status IS '阶段结果，例如 started、succeeded、failed 或 degraded';
COMMENT ON COLUMN diagnosis_trace_spans.elapsed_ms IS '本阶段耗时毫秒数，未知时为空';
COMMENT ON COLUMN diagnosis_trace_spans.payload IS '脱敏计数、模型名和错误类型；禁止保存密钥、源码和完整请求';
COMMENT ON COLUMN diagnosis_trace_spans.created_at IS '轨迹写入的 UTC 时间';
