"""FastAPI 应用入口和生命周期管理。"""

from contextlib import asynccontextmanager
import json
import logging
import time
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from app.auth import auth_guard, security_http_exception_handler, validate_auth_settings
from app.health import get_health
from app.metrics import begin_http_request, finish_http_request, router as metrics_router
from app.preflight import enforce_production_settings
from app.rate_limit import RedisRateLimiter
from app.request_limits import RequestBodyTooLarge, read_limited_body
from app.request_context import resolve_request_id
from app.security_headers import add_cors_middleware, apply_security_headers
from app.settings import get_settings
from app.code_api import router as code_router
from app.diagnosis import router as diagnosis_router
from app.feedback_api import project_feedback_router, router as feedback_router
from app.frontend import router as frontend_router
from app.validation_api import router as validation_router
from app.hybrid_api import router as hybrid_router
from app.index_api import router as index_router
from app.task_store import create_pool

settings = get_settings()
logger = logging.getLogger("ragent.access")


@asynccontextmanager
async def lifespan(application: FastAPI):
    """启动时创建数据库连接池，关闭时释放连接资源。"""
    enforce_production_settings(settings)
    validate_auth_settings(settings)
    application.state.validation_rate_limiter = RedisRateLimiter.from_url(
        settings.redis_url,
        settings.validation_rate_limit_per_minute,
        settings.validation_rate_limit_window_seconds,
    )
    try:
        application.state.db_pool = await create_pool(settings)
    except Exception:
        # 允许不带依赖服务运行 API 单元测试；实际任务接口会返回 503。
        application.state.db_pool = None
    try:
        yield
    finally:
        await application.state.validation_rate_limiter.close()
        if application.state.db_pool is not None:
            await application.state.db_pool.close()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_exception_handler(HTTPException, security_http_exception_handler)
add_cors_middleware(app, settings.cors_allowed_origins)


@app.middleware("http")
async def access_control(request, call_next):
    """校验 API Key，并把调用方身份传递给项目级权限检查。"""
    started_at = begin_http_request()
    request_id = resolve_request_id(request.headers.get("X-Request-ID"))
    request.state.request_id = request_id
    status_code = 500
    response = None
    try:
        if request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH"}:
            try:
                await read_limited_body(request, settings.max_request_body_bytes)
            except RequestBodyTooLarge as exc:
                response = JSONResponse(status_code=413, content={"detail": str(exc)})
                status_code = response.status_code
                return response
        response = await auth_guard(request, call_next, settings)
        status_code = response.status_code
        return response
    finally:
        finish_http_request(request, started_at, status_code)
        if response is not None:
            apply_security_headers(response, request)
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
                },
                ensure_ascii=False,
            )
        )


app.include_router(diagnosis_router)
app.include_router(feedback_router)
app.include_router(project_feedback_router)
app.include_router(frontend_router)
app.include_router(code_router)
app.include_router(validation_router)
app.include_router(hybrid_router)
app.include_router(index_router)
app.include_router(metrics_router)


@app.get("/health")
async def health() -> Dict:
    """返回 API、Redis 和 PostgreSQL 的健康状态。"""
    return await get_health(settings)


@app.get("/health/live")
async def health_live() -> Dict[str, str]:
    """返回进程存活状态，不访问外部依赖。"""
    return {"status": "ok", "check": "liveness"}


@app.get("/health/ready")
async def health_ready() -> Any:
    """返回流量就绪状态，核心依赖降级时使用 HTTP 503。"""
    result = await get_health(settings)
    if result["status"] != "ok":
        return JSONResponse(status_code=503, content=result)
    return result


@app.get("/")
async def root() -> Dict[str, str]:
    """返回服务名称和运行状态，用于快速确认 API 已启动。"""
    return {"name": settings.app_name, "status": "running"}
