# PHP 代码诊断 RAgent

当前项目已完成阶段 0 和阶段 1，正在推进阶段 2：代码检索 MVP。

## 本地启动

1. 复制 `.env.example` 为 `.env`。
2. 创建 Python 虚拟环境：`python3 -m venv .venv`。
3. 升级 pip：`.venv/bin/python -m pip install --upgrade pip`。
4. 安装开发依赖：`.venv/bin/pip install '.[dev]'`。
5. 启动 API：`.venv/bin/uvicorn app.main:app --reload`。

## Docker 启动

复制 `.env.example` 为 `.env`，然后执行 `docker compose up --build`。

API 默认运行在 http://127.0.0.1:8000，Worker 使用 Celery 连接 Redis。

## 测试

执行 `.venv/bin/pytest -q`。

## 代码注释约定

所有新增或修改的代码文件，都必须同步维护注释：

- 模块顶部说明模块职责；
- 类注释说明类的职责和边界；
- 方法或函数注释说明用途、关键参数、返回值和异常边界；
- 复杂流程保留必要的行内注释，并在行为变化时同步更新；
- 注释使用中文，技术名词和接口名称保留原文。

## 当前阶段说明

当前已支持：

- 创建、查询、取消诊断任务；
- Celery/Redis 异步执行；
- SSE 进度推送；
- 代码关键词检索；
- PHP/配置文件按行读取；
- 代码仓库和目录白名单校验。

当前的诊断报告仍是关键词检索报告，尚未接入 LLM、PHP AST 调用链分析、数据库验证和日志验证。

代码仓库以只读方式挂载到 `repositories/<project>`，例如：

```text
repositories/waky3/php/action/BDCenterControl.php
```
