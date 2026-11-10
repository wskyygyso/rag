-- 项目符号索引版本状态：避免每个并发诊断任务重复清空并重写四万级符号。
-- 版本指纹来自 PHP 文件相对路径、大小和修改时间；任一代码文件变化都会触发重写。
CREATE TABLE IF NOT EXISTS project_symbol_indexes (
    project VARCHAR(100) PRIMARY KEY,
    repository_fingerprint CHAR(64) NOT NULL,
    symbol_count INT NOT NULL CHECK (symbol_count >= 0),
    reference_count INT NOT NULL CHECK (reference_count >= 0),
    config_count INT NOT NULL CHECK (config_count >= 0),
    sql_count INT NOT NULL CHECK (sql_count >= 0),
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE project_symbol_indexes IS '项目代码快照与已持久化符号索引版本的对应关系';
COMMENT ON COLUMN project_symbol_indexes.project IS '被诊断项目标识，与 code_symbols.project 对应';
COMMENT ON COLUMN project_symbol_indexes.repository_fingerprint IS 'PHP 文件路径、大小和纳秒修改时间形成的 SHA-256 快照指纹';
COMMENT ON COLUMN project_symbol_indexes.symbol_count IS '该快照写入 code_symbols 的符号数量';
COMMENT ON COLUMN project_symbol_indexes.reference_count IS '该快照的静态调用引用数量';
COMMENT ON COLUMN project_symbol_indexes.config_count IS '该快照识别的 Config 读取数量';
COMMENT ON COLUMN project_symbol_indexes.sql_count IS '该快照识别的 SQL 片段数量';
COMMENT ON COLUMN project_symbol_indexes.indexed_at IS '该代码快照完成符号持久化的 UTC 时间';
