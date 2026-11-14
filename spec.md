# 阶段 42 规格：生产交付门禁复跑与核心表覆盖修复

## 一、背景与目标

阶段 0 至 41 已完成全部功能闭环，`PRODUCTION_CHECKLIST.md` 中最新的本机演练记录
仍停留在 2026-11-06：当时只有 21 个迁移、11 张核心表，尚未包含诊断追踪、项目
知识库和人工反馈别名表。随着阶段 22–41 新增多张运行时依赖表，预检的
`REQUIRED_TABLES` 没有同步，存在“预检通过但 Trace、知识库或别名接口运行时报错”
的生产风险。

本阶段只做生产交付前的前置检查与门禁修复，不新增业务能力：

1. 把诊断追踪、项目知识库和人工别名表纳入启动预检核心表集合；
2. 在全新空库重跑全部迁移，确认 26 个版本、17 张表一次成功；
3. 复跑生产等价严格预检、烟雾测试、性能基线、迁移漂移检查；
4. 完成一次备份、SHA-256 校验、隔离库恢复演练和保留期清理 dry-run；
5. 验证生产 Compose 在注入必需 Secret 变量后可解析；
6. 更新 `PRODUCTION_CHECKLIST.md` 的演练证据与剩余外部项。

## 二、修复范围（`app/preflight.py`）

`REQUIRED_TABLES` 补充：

| 表 | 依赖者 | 缺失后果 |
| --- | --- | --- |
| `diagnosis_trace_spans` | Trace 查询接口 | 报告页 Trace 500 |
| `project_glossary` | 知识库术语接口 | 知识库摘要/术语 503 |
| `project_config_structure` | 配置结构接口 | 配置查询 503 |
| `project_verified_conclusions` | 历史结论与反馈闭环 | 反馈写入降级 |
| `project_activity_aliases` | 诊断活动链接 | 人工别名不可用，链接回退启发式 |

按用途分组补充中文注释，说明缺少该表时的具体运行影响。该检查只比对
`information_schema.tables`，不读取业务数据。

## 三、验证流程

```bash
# 1. 迁移漂移与连续版本
docker compose exec -T api python scripts/migrate_database.py --check

# 2. 全新空库迁移
docker compose exec -T postgres psql -U ragent -d ragent \
  -c "CREATE DATABASE ragent_migration_drill;"
docker compose exec -T -e DATABASE_URL=postgresql://ragent:ragent@postgres:5432/ragent_migration_drill \
  api python scripts/migrate_database.py

# 3. 生产等价严格预检（production、认证、独立只读验证库）
docker compose exec -T -e APP_ENV=production -e AUTH_ENABLED=true \
  -e API_KEYS_JSON='{"<32字节Key>":{"subject":"ops","projects":["waky3"],"scopes":["read","write"]}}' \
  -e VALIDATION_DATABASE_URL=postgresql://ragent:ragent@postgres:5432/ragent_validation_drill \
  -e CODE_REPOSITORIES_ROOT=/repositories \
  api python scripts/preflight_check.py --strict

# 4. 烟雾测试与性能基线
.venv/bin/python scripts/smoke_test.py --require-api --project waky3
.venv/bin/python scripts/performance_baseline.py --requests 100 --concurrency 10

# 5. 备份、校验与恢复演练
docker compose --profile maintenance run --rm backup
docker compose --profile maintenance run --rm --entrypoint sh backup -c \
  'sha256sum -c /backups/<dump>.sha256 && pg_restore --dbname=postgresql://ragent:ragent@postgres:5432/ragent_restore_drill --no-owner --no-acl --exit-on-error /backups/<dump>'

# 6. 保留期清理 dry-run
docker compose exec -T api python scripts/cleanup_retention.py

# 7. 生产 Compose 解析
RAGENT_REPOSITORIES_ROOT=/Users/wskyygyso/work/www RAGENT_IMAGE=rag-api:<tag> \
API_KEYS_JSON='...' VALIDATION_DATABASE_URL='...' \
docker compose -f docker-compose.production.yml config -q
```

隔离演练库在验证后删除，不覆盖主库，不保留副本。

## 四、验收标准

1. 迁移 `--check`：`pending=0 drifted=0`，26 个连续版本；
2. 全新空库迁移：26 个版本一次成功，17 张表齐全；
3. 生产等价严格预检：16 项全部 PASS，且核心表检查包含新增 5 张表；
4. 烟雾测试 4 项 PASS；
5. 性能基线 100 请求 0 错误，P95 低于 500 ms；
6. 备份 SHA-256 校验通过，恢复后核心表行数与主库一致；
7. 保留期清理 dry-run 候选为 0；
8. 生产 Compose 在注入必需变量后解析通过；
9. 全量测试、注释覆盖、前端语法和 `git diff --check` 通过；
10. 更新 `PRODUCTION_CHECKLIST.md` 与 `progress.md`。

## 五、明确不做与外部依赖

- 不在本机创建真实生产 Secret、TLS 域名或 Alertmanager 路由；
- 不伪造生产 embedding 配额的全量重建结果；
- 不删除现有备份文件，不覆盖主库数据；
- 不主动引入新的业务映射或答案硬编码。

仍待目标环境完成：真实 Secret 注入、受控 TLS 域名、生产 embedding 配额全量重建、
Alertmanager 通知与值班配置、目标环境备份恢复演练、上线负责人签字。
