# RAgent 元数据库备份和恢复演练

## 备份

备份以 PostgreSQL custom format 保存，跳过 owner 和 ACL，并生成 SHA-256 校验文件。默认保存在
被 Git 忽略的 `backups/` 目录，保留最近 7 份：

```bash
mkdir -p backups
docker compose --profile maintenance run --rm backup
```

可临时调整保留数量：

```bash
BACKUP_KEEP=14 docker compose --profile maintenance run --rm backup
```

备份文件通过 `pg_restore --list` 自检后才移动到最终文件名，避免把未完成的 dump 当成有效备份。

本地 `backups/` 仅用于开发和演练。生产备份包含诊断任务、代码证据和审计摘要，必须加密后保存到
与运行环境隔离的对象存储，启用版本控制、访问审计和生命周期策略，禁止通过聊天或普通文件共享传输。

## 恢复演练

禁止直接覆盖生产数据库。恢复必须创建隔离数据库，验证通过后再由人工决策切换。

1. 创建隔离数据库和专用恢复账号。
2. 将备份文件和 `.sha256` 文件复制到恢复环境。
3. 使用 `sha256sum -c <备份文件>.sha256` 校验完整性。
4. 执行恢复：

```bash
pg_restore \
  --dbname=ragent_restore \
  --no-owner \
  --no-acl \
  --exit-on-error \
  ragent-YYYYMMDDTHHMMSSZ.dump
```

5. 检查核心表、索引和行数：

```sql
SELECT 'diagnostic_tasks' AS table_name, COUNT(*) FROM diagnostic_tasks
UNION ALL SELECT 'diagnostic_evidence', COUNT(*) FROM diagnostic_evidence
UNION ALL SELECT 'diagnostic_feedback', COUNT(*) FROM diagnostic_feedback
UNION ALL SELECT 'document_chunks', COUNT(*) FROM document_chunks
UNION ALL SELECT 'security_audit', COUNT(*) FROM security_audit;
```

6. 运行 API 测试、核心表查询和只读验证查询。
7. 记录备份时间、恢复耗时、校验结果、行数差异和负责人。
8. 未完成隔离验证前，不得切换生产数据库或清理现有备份。

## 回滚

应用回滚不得回滚数据库迁移，除非迁移说明明确支持。代码发布回滚和数据库恢复必须分别审批；
优先使用前向兼容迁移修复，避免覆盖上线后的新数据。
