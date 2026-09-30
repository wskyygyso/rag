CREATE TABLE IF NOT EXISTS document_index_runs (
    id BIGSERIAL PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL,
    chunk_count INT NOT NULL DEFAULT 0,
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_document_index_runs_project_started
    ON document_index_runs (project, started_at DESC);

COMMENT ON TABLE document_index_runs IS '统一文档分块索引运行记录和失败恢复依据';
COMMENT ON COLUMN document_index_runs.status IS '索引状态：RUNNING、COMPLETED 或 FAILED';
COMMENT ON COLUMN document_index_runs.chunk_count IS '本次成功写入的分块数量';
