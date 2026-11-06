# 使用固定多架构摘要的精简 Python 3.11 运行 API、Celery Worker 和维护脚本。
# 升级基础镜像时必须同步验证依赖锁、漏洞扫描和全部回归测试。
FROM python:3.11-slim@sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60

# 镜像元数据由 CI 构建参数注入，便于追踪源码版本和发布来源。
ARG RAGENT_SOURCE=https://github.com/wskyygyso/rag
ARG RAGENT_REVISION=unknown
LABEL org.opencontainers.image.title="PHP Diagnostic RAgent" \
      org.opencontainers.image.source="${RAGENT_SOURCE}" \
      org.opencontainers.image.revision="${RAGENT_REVISION}" \
      org.opencontainers.image.licenses="Proprietary"

# 禁止写入 pyc、实时刷新日志，并减少镜像中的 pip 缓存。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache

# 统一容器工作目录。
WORKDIR /app

# 先复制锁定依赖，确保生产镜像不随时间解析出不同版本。
COPY requirements.lock ./
RUN pip install --no-cache-dir --upgrade pip==26.3 \
    && pip install --no-cache-dir -r requirements.lock

# 在生产镜像构建阶段拒绝依赖元数据冲突，避免发布后 Worker 启动才暴露问题。
RUN python -m pip check

# 复制应用、迁移、静态前端和维护脚本；生产镜像不包含 tests。
COPY app ./app
COPY migrations ./migrations
COPY static ./static
COPY scripts ./scripts
COPY pyproject.toml ./
RUN pip install --no-cache-dir --no-deps .

# 使用固定非 root UID/GID 运行 API 和 Worker，降低容器逃逸后的权限范围。
RUN groupadd --system --gid 10001 ragent \
    && useradd --system --uid 10001 --gid ragent --home-dir /tmp/ragent --shell /usr/sbin/nologin ragent \
    && mkdir -p /tmp/ragent /tmp/.cache \
    && chown -R ragent:ragent /tmp/ragent /tmp/.cache

USER 10001:10001

# API 默认监听端口。
EXPOSE 8000

# 默认启动 API；Worker 由 Compose command 覆盖为 Celery。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
