-- 创建安全拒绝审计表，记录认证失败和项目越权摘要。
CREATE TABLE IF NOT EXISTS security_audit (
    id BIGSERIAL PRIMARY KEY,
    event_type VARCHAR(32) NOT NULL,
    status_code SMALLINT NOT NULL,
    reason VARCHAR(500) NOT NULL,
    method VARCHAR(16) NOT NULL,
    path VARCHAR(500) NOT NULL,
    client_address VARCHAR(100),
    actor VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 支持按时间和拒绝类型排查异常访问。
CREATE INDEX IF NOT EXISTS idx_security_audit_created
    ON security_audit (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_audit_type_created
    ON security_audit (event_type, created_at DESC);

COMMENT ON TABLE security_audit IS 'API 认证失败和项目授权拒绝的安全审计摘要';
COMMENT ON COLUMN security_audit.reason IS '受控拒绝原因，不保存请求头或原始 API Key';
COMMENT ON COLUMN security_audit.actor IS '认证成功但授权失败时的可信调用方主体';
