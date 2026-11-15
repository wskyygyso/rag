<!-- ragent-document-test-corpus: generated -->
# 常见故障排查

> 检索关键词：故障 排查 error timeout 队列 worker 重试

## 诊断任务长时间未完成

1. 检查 `diagnosis` 队列是否有积压；
2. 检查 Worker 健康检查和日志；
3. 检查模型或 embedding 超时配置；
4. 必要时取消任务后重试，不要重复创建大量任务。

## 索引任务失败

1. 检查仓库挂载是否只读且可访问；
2. 检查 `document_index_runs` 的失败原因；
3. 确认索引版本和 embedding 模型一致；
4. 修复后重新执行索引，旧运行记录会保留审计。

## 接口 5xx

先查看请求 ID 和结构化日志，再核对数据库连接、迁移漂移和只读验证库状态。
