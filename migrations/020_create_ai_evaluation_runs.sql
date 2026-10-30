-- 保存固定 AI 评测集的真实运行结果，支持质量趋势追踪。
CREATE TABLE IF NOT EXISTS ai_evaluation_runs (
    id BIGSERIAL PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL,
    min_pass_rate DOUBLE PRECISION NOT NULL,
    total INT NOT NULL,
    executed INT NOT NULL,
    skipped INT NOT NULL,
    passed INT NOT NULL,
    pass_rate DOUBLE PRECISION NOT NULL,
    average_evidence_ref_recall DOUBLE PRECISION NOT NULL,
    result JSONB NOT NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_evaluation_runs_project_created
    ON ai_evaluation_runs (project, created_at DESC);

COMMENT ON TABLE ai_evaluation_runs IS 'AI 固定评测集的运行、通过率、证据召回和用例明细';
COMMENT ON COLUMN ai_evaluation_runs.result IS '完整评测汇总 JSON，包含每个用例的质量检查';
