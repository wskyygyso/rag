CREATE TABLE IF NOT EXISTS code_symbols (
    id BIGSERIAL PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    symbol_type VARCHAR(50) NOT NULL,
    symbol_name VARCHAR(255) NOT NULL,
    file_path TEXT NOT NULL,
    line INT NOT NULL,
    signature TEXT,
    "references" JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_code_symbols_project_name
    ON code_symbols (project, symbol_name);

COMMENT ON TABLE code_symbols IS 'PHP 代码符号索引：保存类、方法、函数及其引用关系';
COMMENT ON COLUMN code_symbols.project IS '符号所属项目';
COMMENT ON COLUMN code_symbols.symbol_type IS '符号类型，例如 class、method 或 function';
COMMENT ON COLUMN code_symbols.symbol_name IS '符号名称或 Class::method 全限定名';
COMMENT ON COLUMN code_symbols.file_path IS '符号定义文件相对路径';
COMMENT ON COLUMN code_symbols.line IS '符号定义所在行号';
COMMENT ON COLUMN code_symbols.signature IS '符号定义的原始声明片段';
COMMENT ON COLUMN code_symbols."references" IS '该符号关联的引用和调用元数据';
COMMENT ON COLUMN code_symbols.updated_at IS '索引最后更新时间';
