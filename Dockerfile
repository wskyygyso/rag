# 使用精简 Python 3.11 运行 API、Celery Worker 和维护脚本。
FROM python:3.11-slim

# 禁止写入 pyc、实时刷新日志，并减少镜像中的 pip 缓存。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 统一容器工作目录。
WORKDIR /app

# 先复制依赖描述，利用 Docker 层缓存安装项目依赖。
COPY pyproject.toml ./

# 复制应用、静态前端和维护脚本；生产镜像不包含 tests。
COPY app ./app
COPY static ./static
COPY scripts ./scripts
RUN pip install --upgrade pip && pip install .

# API 默认监听端口。
EXPOSE 8000

# 默认启动 API；Worker 由 Compose command 覆盖为 Celery。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
