<!-- ragent-document-test-corpus: generated -->
# 测试语料说明

> 检索关键词：测试语料 corpus 文档索引 document index

本目录由 `scripts/generate_document_test_corpus.py` 生成，只用于本地测试。
文档主题覆盖活动规则、VIP 等级、提现流程、设备注册、配置字段、故障排查、
部署回滚、数据字典和安全基线，便于验证 Markdown 分块、中文全文检索和向量召回。

重新生成：

```bash
.venv/bin/python scripts/generate_document_test_corpus.py
```

生成结果只包含合成数据，禁止把本目录内容当作真实业务配置或线上答案。
