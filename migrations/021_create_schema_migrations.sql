-- 记录生产迁移版本和脚本校验和；应用首次执行迁移器时也会幂等创建该表。
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(255) PRIMARY KEY,
    checksum CHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE schema_migrations IS 'RAgent 元数据库已应用迁移版本和脚本校验和';
COMMENT ON COLUMN schema_migrations.version IS '迁移文件名，按三位递增版本号排序执行';
COMMENT ON COLUMN schema_migrations.checksum IS '迁移 SQL 文件的 SHA-256，用于阻止已发布脚本被静默修改';
COMMENT ON COLUMN schema_migrations.applied_at IS '迁移首次成功提交的 UTC 时间';
