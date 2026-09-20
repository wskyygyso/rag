CREATE TABLE IF NOT EXISTS diagnostic_evidence (
    id BIGSERIAL PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL REFERENCES diagnostic_tasks(id) ON DELETE CASCADE,
    evidence_type VARCHAR(30) NOT NULL,
    project VARCHAR(100),
    file_path TEXT,
    line_start INT,
    line_end INT,
    query TEXT,
    content TEXT,
    source JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE diagnostic_evidence IS '诊断任务证据表：保存代码、配置、数据库和日志等可追溯证据';
COMMENT ON COLUMN diagnostic_evidence.task_id IS '所属诊断任务 ID';
COMMENT ON COLUMN diagnostic_evidence.evidence_type IS '证据类型，例如 code、config、database 或 log';
COMMENT ON COLUMN diagnostic_evidence.project IS '证据所属项目';
COMMENT ON COLUMN diagnostic_evidence.file_path IS '代码或配置文件相对路径';
COMMENT ON COLUMN diagnostic_evidence.line_start IS '证据起始行号';
COMMENT ON COLUMN diagnostic_evidence.line_end IS '证据结束行号';
COMMENT ON COLUMN diagnostic_evidence.query IS '产生该证据的检索关键词或查询条件';
COMMENT ON COLUMN diagnostic_evidence.content IS '证据正文或脱敏后的结果摘要';
COMMENT ON COLUMN diagnostic_evidence.source IS '来源元数据，例如工具名称和仓库版本';
COMMENT ON COLUMN diagnostic_evidence.created_at IS '证据写入时间';
