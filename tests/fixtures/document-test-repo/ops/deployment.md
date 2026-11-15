<!-- ragent-document-test-corpus: generated -->
# 部署与回滚

> 检索关键词：部署 deploy 回滚 rollback 迁移 migration 健康检查

## 部署顺序

1. 完成备份并校验；
2. 执行迁移检查，确认无 pending 和 drifted；
3. 应用迁移；
4. 启动 API 与 Worker；
5. 执行 readiness、烟雾测试和性能基线；
6. 观察告警和队列指标。

## 回滚原则

应用回滚使用上一个已验证镜像 digest。数据库回滚不等于应用回滚，优先以前向兼容
修复处理；确需恢复数据时，只在隔离库恢复并验证后再切换流量。

## 禁止事项

- 禁止使用 `latest` 镜像；
- 禁止修改已发布迁移文件；
- 禁止把生产 Secret 写入仓库或日志。
