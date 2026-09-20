# PHP 代码诊断 RAgent

当前项目处于阶段 0：环境和项目初始化。

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

## 当前阶段说明

本阶段只提供 API、Worker、Redis、PostgreSQL 和健康检查骨架，尚未实现诊断任务、代码搜索和 Agent 工具调用。
