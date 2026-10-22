"""FastAPI 应用入口和生命周期管理。"""

from contextlib import asynccontextmanager
from typing import Dict

from fastapi import FastAPI, HTTPException

from app.auth import auth_guard, security_http_exception_handler, validate_auth_settings
from app.health import get_health
from app.rate_limit import RedisRateLimiter
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


@asynccontextmanager
async def lifespan(application: FastAPI):
    """启动时创建数据库连接池，关闭时释放连接资源。"""
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


@app.middleware("http")
async def access_control(request, call_next):
    """校验 API Key，并把调用方身份传递给项目级权限检查。"""
    return await auth_guard(request, call_next, settings)


app.include_router(diagnosis_router)
app.include_router(feedback_router)
app.include_router(project_feedback_router)
app.include_router(frontend_router)
app.include_router(code_router)
app.include_router(validation_router)
app.include_router(hybrid_router)
app.include_router(index_router)


@app.get("/health")
async def health() -> Dict:
    """返回 API、Redis 和 PostgreSQL 的健康状态。"""
    return await get_health(settings)


@app.get("/")
async def root() -> Dict[str, str]:
    """返回服务名称和运行状态，用于快速确认 API 已启动。"""
    return {"name": settings.app_name, "status": "running"}
