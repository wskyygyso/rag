ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(64);

ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS embedding_dimension SMALLINT;

COMMENT ON COLUMN document_chunks.embedding_model IS '生成当前向量的模型或特征版本标识';
COMMENT ON COLUMN document_chunks.embedding_dimension IS '当前向量维度，用于检索时校验兼容性';
