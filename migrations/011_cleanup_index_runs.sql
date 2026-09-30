-- 索引运行清理通过应用层按项目保留数量执行，本迁移用于版本占位。
COMMENT ON TABLE document_index_runs IS '统一文档分块索引运行记录、版本和失败恢复依据';
