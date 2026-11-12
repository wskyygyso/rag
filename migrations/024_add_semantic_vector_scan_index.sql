-- 真实语义检索每次只扫描当前 embedding 模型和维度兼容的分块。该联合索引把
-- 项目隔离、模型版本过滤和最近更新排序合并到一条访问路径中，避免旧模型分块
-- 或文档分块占满 Python 余弦计算的扫描窗口。
CREATE INDEX IF NOT EXISTS idx_document_chunks_semantic_scan
    ON document_chunks (
        project,
        embedding_model,
        embedding_dimension,
        updated_at DESC,
        source_id
    )
    WHERE embedding IS NOT NULL;

COMMENT ON INDEX idx_document_chunks_semantic_scan IS
    '真实语义向量候选扫描：按项目、embedding 模型和维度筛选兼容分块';
