ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS embedding JSONB;

COMMENT ON COLUMN document_chunks.embedding IS '可替换 embedding 服务的固定维度文本向量，MVP 使用 JSONB 存储';
