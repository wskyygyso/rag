-- 人工反馈活动别名表：把业务人员确认的“问题中文叫法 → 真实活动目录”映射落库。
-- 该表是活动实体链接的确定性输入，只允许授权用户登记，禁止由代码常量或自动
-- 推断写入；停用即回滚，记录保留用于审计。

CREATE TABLE IF NOT EXISTS project_activity_aliases (
    id BIGSERIAL PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    alias VARCHAR(40) NOT NULL,
    activity_id VARCHAR(100) NOT NULL,
    status VARCHAR(16) NOT NULL
        CHECK (status IN ('approved', 'rejected')),
    source VARCHAR(16) NOT NULL DEFAULT 'feedback'
        CHECK (source IN ('feedback')),
    evidence TEXT,
    created_by VARCHAR(100),
    updated_by VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_project_activity_alias UNIQUE (project, alias)
);

COMMENT ON TABLE project_activity_aliases IS '人工登记的问题中文叫法到活动目录映射，参与活动实体链接';
COMMENT ON COLUMN project_activity_aliases.project IS '别名所属项目标识，与诊断项目和活动知识库一致';
COMMENT ON COLUMN project_activity_aliases.alias IS '问题中出现的中文业务叫法，已规范化并禁止通用问句词';
COMMENT ON COLUMN project_activity_aliases.activity_id IS '目标活动目录名，登记前必须存在于当前活动知识库';
COMMENT ON COLUMN project_activity_aliases.status IS '启用状态：approved 参与链接，rejected 保留记录但不参与';
COMMENT ON COLUMN project_activity_aliases.source IS '别名来源，当前仅支持人工反馈';
COMMENT ON COLUMN project_activity_aliases.evidence IS '登记说明或反馈依据，供审计追溯，不保存密钥和敏感信息';
COMMENT ON COLUMN project_activity_aliases.created_by IS '首次登记该别名的操作主体';
COMMENT ON COLUMN project_activity_aliases.updated_by IS '最近一次登记或状态变更的操作主体';
COMMENT ON COLUMN project_activity_aliases.created_at IS '别名首次写入的 UTC 时间';
COMMENT ON COLUMN project_activity_aliases.updated_at IS '别名最近一次变更的 UTC 时间';

CREATE INDEX IF NOT EXISTS idx_project_activity_aliases_status
    ON project_activity_aliases (project, status, updated_at DESC);
