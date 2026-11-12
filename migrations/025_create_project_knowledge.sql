-- 自动派生的项目知识库：术语表、配置结构摘要和历史结论候选。
-- 三张表都按项目隔离，并用 repository_fingerprint 绑定代码快照，代码变化后
-- 旧知识不再作为检索依据；所有条目均由索引或诊断证据自动生成，禁止人工维护。

-- 项目术语表：把中文业务词映射到仓库中真实存在的代码标识。
-- 术语来源只允许注释、符号、路径、配置键和运行时模型扩展五类；其中 LLM 扩展
-- 仅作为候选，置信度低于注释派生词。
CREATE TABLE IF NOT EXISTS project_glossary (
    id BIGSERIAL PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    term VARCHAR(200) NOT NULL,
    code_term VARCHAR(255) NOT NULL,
    term_kind VARCHAR(32) NOT NULL
        CHECK (term_kind IN ('symbol', 'file', 'directory', 'config_key', 'runtime')),
    source VARCHAR(32) NOT NULL
        CHECK (source IN ('comment', 'symbol', 'path', 'config_key', 'llm')),
    evidence_ref TEXT,
    confidence NUMERIC(4, 3) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    repository_fingerprint CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_project_glossary_term_code UNIQUE (project, term, code_term)
);

COMMENT ON TABLE project_glossary IS '项目中文业务词与真实代码标识的自动派生映射';
COMMENT ON COLUMN project_glossary.project IS '项目标识，与 code_symbols.project 和诊断任务一致';
COMMENT ON COLUMN project_glossary.term IS '中文业务词，来自注释或运行时问题，已去除空白和注释符号';
COMMENT ON COLUMN project_glossary.code_term IS '仓库中真实存在的类名、方法名、文件名干、目录名或配置键';
COMMENT ON COLUMN project_glossary.term_kind IS '代码词类型：符号、文件、目录、配置键或运行时扩展词';
COMMENT ON COLUMN project_glossary.source IS '术语来源：中文注释、符号声明、路径、配置键或模型扩展';
COMMENT ON COLUMN project_glossary.evidence_ref IS '可审计引用，通常为“文件:行号”或项目内相对路径';
COMMENT ON COLUMN project_glossary.confidence IS '术语可信度，注释和符号派生为高置信，模型沉淀为低置信';
COMMENT ON COLUMN project_glossary.repository_fingerprint IS '生成该术语时的 PHP 文件快照指纹，代码变化后需重建';
COMMENT ON COLUMN project_glossary.created_at IS '术语首次写入的 UTC 时间';
COMMENT ON COLUMN project_glossary.updated_at IS '术语最近一次重建或置信度更新的 UTC 时间';

CREATE INDEX IF NOT EXISTS idx_project_glossary_term
    ON project_glossary (project, term);

CREATE INDEX IF NOT EXISTS idx_project_glossary_fingerprint
    ON project_glossary (project, repository_fingerprint);

-- 配置结构摘要：只保存诊断证据窗口中已经解析过的配置路径和脱敏展示值，
-- 不保存源码正文、URL、密钥或令牌。配置结构用于把中文术语快速定位到配置键，
-- 不替代诊断时对真实源码的读取和校验。
CREATE TABLE IF NOT EXISTS project_config_structure (
    id BIGSERIAL PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    file TEXT NOT NULL,
    array_path TEXT NOT NULL,
    key_name VARCHAR(255) NOT NULL,
    value_kind VARCHAR(32) NOT NULL
        CHECK (value_kind IN ('numeric', 'bool', 'constant', 'label', 'env_test', 'env_production')),
    display_value TEXT,
    branch VARCHAR(16) NOT NULL DEFAULT '',
    line INT NOT NULL CHECK (line >= 1),
    repository_fingerprint CHAR(64) NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_project_config_structure_node
        UNIQUE (project, file, array_path, key_name, branch)
);

COMMENT ON TABLE project_config_structure IS '诊断过程中自动落库的 PHP 配置结构摘要，仅含脱敏安全值';
COMMENT ON COLUMN project_config_structure.project IS '配置所属项目标识';
COMMENT ON COLUMN project_config_structure.file IS '配置文件在项目内的相对路径';
COMMENT ON COLUMN project_config_structure.array_path IS '从顶层数组到当前键的路径，例如 prize.1 或 rank_prizes.1.id';
COMMENT ON COLUMN project_config_structure.key_name IS '当前配置节点末级键名';
COMMENT ON COLUMN project_config_structure.value_kind IS '值类型：数字、布尔、代码常量、奖励标签或测试/正式环境值';
COMMENT ON COLUMN project_config_structure.display_value IS '已脱敏的安全展示值；敏感字段和地址类配置为空';
COMMENT ON COLUMN project_config_structure.branch IS '环境分支：test、production 或空字符串';
COMMENT ON COLUMN project_config_structure.line IS '配置节点在源码中的 1 起始行号';
COMMENT ON COLUMN project_config_structure.repository_fingerprint IS '观测该配置时的代码快照指纹';
COMMENT ON COLUMN project_config_structure.observed_at IS '最近一次观测到该配置的 UTC 时间';

CREATE INDEX IF NOT EXISTS idx_project_config_structure_key
    ON project_config_structure (project, key_name);

CREATE INDEX IF NOT EXISTS idx_project_config_structure_file
    ON project_config_structure (project, file);

-- 历史结论库：保存已完成诊断的确定性结论及证据引用。
-- 该表只作为检索候选提示，不参与新结论拼接；只有用户反馈“正确”后才升级为
-- verified，反馈“不准确”则标记 rejected。结论绑定代码指纹，代码变化后过期，
-- 避免旧活动配置继续影响新问题。
CREATE TABLE IF NOT EXISTS project_verified_conclusions (
    id BIGSERIAL PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    question_hash CHAR(64) NOT NULL,
    question TEXT NOT NULL,
    conclusion TEXT NOT NULL,
    confirmed_facts JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    status VARCHAR(16) NOT NULL
        CHECK (status IN ('candidate', 'verified', 'expired', 'rejected')),
    source_task_id VARCHAR(64),
    repository_fingerprint CHAR(64) NOT NULL,
    verified_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_project_verified_conclusion_question UNIQUE (project, question_hash)
);

COMMENT ON TABLE project_verified_conclusions IS '历史诊断确定性结论候选库，仅供检索提示，不直接作为新结论';
COMMENT ON COLUMN project_verified_conclusions.project IS '结论所属项目标识';
COMMENT ON COLUMN project_verified_conclusions.question_hash IS '规范化问题的 SHA-256，用于同问题匹配和去重';
COMMENT ON COLUMN project_verified_conclusions.question IS '用户原始问题文本，保留用于审计和展示';
COMMENT ON COLUMN project_verified_conclusions.conclusion IS '历史诊断生成的确定性结论原文';
COMMENT ON COLUMN project_verified_conclusions.confirmed_facts IS '事实摘要 JSON，只保存类别与陈述，不保存源码全文';
COMMENT ON COLUMN project_verified_conclusions.evidence_refs IS '证据引用 JSON 数组，指向具体文件和行号';
COMMENT ON COLUMN project_verified_conclusions.status IS '结论状态：待确认、已验证、已过期或已拒绝';
COMMENT ON COLUMN project_verified_conclusions.source_task_id IS '生成该结论的诊断任务 ID';
COMMENT ON COLUMN project_verified_conclusions.repository_fingerprint IS '生成结论时的代码快照指纹，不一致时不得复用';
COMMENT ON COLUMN project_verified_conclusions.verified_at IS '用户反馈确认正确的时间';
COMMENT ON COLUMN project_verified_conclusions.expires_at IS '结论有效期截止时间，过期后不再作为检索候选';
COMMENT ON COLUMN project_verified_conclusions.created_at IS '结论首次写入的 UTC 时间';
COMMENT ON COLUMN project_verified_conclusions.updated_at IS '结论最近一次状态变化的 UTC 时间';

CREATE INDEX IF NOT EXISTS idx_project_verified_conclusions_status
    ON project_verified_conclusions (project, status, updated_at DESC);
