ALTER TABLE document_index_runs
    ADD COLUMN IF NOT EXISTS repository_revision VARCHAR(128);

ALTER TABLE document_index_runs
    ADD COLUMN IF NOT EXISTS index_version VARCHAR(32) NOT NULL DEFAULT 'v1';

COMMENT ON COLUMN document_index_runs.repository_revision IS '索引对应的 Git 提交 SHA 或其他仓库版本标识';
COMMENT ON COLUMN document_index_runs.index_version IS '分块和排序规则版本';
