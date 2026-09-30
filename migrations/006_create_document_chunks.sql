CREATE TABLE IF NOT EXISTS document_chunks (
    id BIGSERIAL PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    source_type VARCHAR(30) NOT NULL,
    source_id VARCHAR(500) NOT NULL,
    file_path TEXT,
    line_start INT,
    line_end INT,
    content TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project, source_type, source_id)
);

CREATE INDEX IF NOT EXISTS idx_document_chunks_project_type
    ON document_chunks (project, source_type);
CREATE INDEX IF NOT EXISTS idx_document_chunks_search_vector
    ON document_chunks USING GIN (search_vector);

COMMENT ON TABLE document_chunks IS '代码、配置和历史诊断文档的统一分块索引表';
COMMENT ON COLUMN document_chunks.source_type IS '来源类型，例如 code、config、history';
COMMENT ON COLUMN document_chunks.source_id IS '来源稳定标识，用于去重和回溯';
COMMENT ON COLUMN document_chunks.search_vector IS 'PostgreSQL simple 配置生成的全文检索向量';
