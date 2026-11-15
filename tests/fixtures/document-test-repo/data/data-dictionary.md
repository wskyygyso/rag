<!-- ragent-document-test-corpus: generated -->
# 数据字典

> 检索关键词：数据字典 schema 表 字段 任务 证据 索引

## 核心表

- `diagnostic_tasks`：诊断任务、状态、问题摘要和结果引用；
- `diagnostic_evidence`：证据文件、行号和摘要；
- `document_chunks`：统一分块、哈希、向量和全文索引；
- `document_index_runs`：索引运行状态、版本和分块数量；
- `diagnosis_trace_spans`：诊断阶段耗时和状态；
- `project_activity_aliases`：人工反馈的活动叫法映射。

## 约束

- 任务和证据只保存脱敏摘要，不保存完整源码；
- 向量必须记录模型和维度，跨模型召回需标记不兼容；
- 人工别名只允许指向真实存在的活动目录。
