# PHP 代码诊断 RAgent 生产上线检查清单

## 配置和安全

- [ ] `APP_ENV=production`。
- [ ] `AUTH_ENABLED=true`，API Key 来自 Secret Manager 且满足长度要求。
- [ ] API Key 的项目白名单和 `read`/`write` scope 最小化。
- [ ] 配置 TLS、反向代理、CORS 和请求体大小限制。
- [ ] `VALIDATION_DATABASE_URL` 使用独立目标业务库只读账号。
- [ ] 目标数据库账号仅授予允许 schema 的 `SELECT` 权限。
- [ ] 代码、配置和日志目录以只读方式挂载。

## 数据库和后端依赖

- [ ] 已在目标环境执行全部 `migrations/*.sql`。
- [ ] RAgent 元数据库连接、核心表和索引检查通过。
- [ ] Redis 连接和共享限流检查通过。
- [ ] PostgreSQL、Redis 和索引数据已有备份策略。
- [ ] 已完成至少一次恢复演练并记录恢复时间。
- [ ] 已配置任务、证据、反馈、验证审计、安全审计和索引记录保留期限。

## 任务和 Worker

- [ ] `worker` 仅消费 `diagnosis` 队列。
- [ ] `index-worker` 仅消费 `index` 队列。
- [ ] 软超时小于硬超时，超时值符合真实任务耗时。
- [ ] Worker 中断后，超时悬挂任务可以恢复为失败。
- [ ] 诊断任务可以在失败后人工重试，且重试上限有效。
- [ ] 索引任务失败可以异步重试。

## 监控和告警

- [ ] Prometheus 已抓取 `/api/metrics` 并携带只读 API Key。
- [ ] 已加载 `monitoring/prometheus-rules.yml`。
- [ ] Alertmanager 通知路由、值班人和升级策略已配置。
- [ ] 已建立 HTTP P95、错误率、任务失败率和队列积压性能基线。
- [ ] 安全拒绝、限流和认证失败趋势可查询。

## 验证和故障恢复

- [ ] 代码检索、符号索引、混合检索和历史任务接口通过冒烟测试。
- [ ] SSE 进度、取消、失败重试和报告导出通过端到端测试。
- [ ] 数据库写 SQL、系统表访问、路径穿越和跨项目访问均被拒绝。
- [ ] Redis 不可用时验证接口安全拒绝。
- [ ] PostgreSQL 不可用时健康检查和错误响应符合预期。
- [ ] Worker 崩溃和硬超时后任务能够恢复。
- [ ] 数据清理脚本已 dry-run，候选数量经过确认。

## 发布和回滚

- [ ] 镜像和依赖已完成漏洞扫描并生成 SBOM。
- [ ] 发布使用不可变镜像标签，不使用仅 `latest` 标签。
- [ ] 数据库迁移有前向兼容和回滚说明。
- [ ] 回滚版本、配置和密钥变更流程已验证。
- [ ] 上线负责人、验证负责人和回滚决策人已确认。

## 上线门禁

- [ ] `docker compose config` 通过。
- [ ] 完整测试通过。
- [ ] `python scripts/preflight_check.py --strict` 通过。
- [ ] 性能基线达到约定阈值。
- [ ] 监控、告警和备份验证完成。
