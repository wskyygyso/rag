"""FastAPI 应用入口和生命周期管理。"""

from contextlib import asynccontextmanager
from typing import Dict

from fastapi import FastAPI

from app.health import get_health
from app.settings import get_settings
from app.diagnosis import router as diagnosis_router
from app.task_store import create_pool

settings = get_settings()


@asynccontextmanager
async def lifespan(application: FastAPI):
    """启动时创建数据库连接池，关闭时释放连接资源。"""
    try:
        application.state.db_pool = await create_pool(settings)
    except Exception:
        # 允许不带依赖服务运行 API 单元测试；实际任务接口会返回 503。
        application.state.db_pool = None
    try:
        yield
    finally:
        if application.state.db_pool is not None:
            await application.state.db_pool.close()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(diagnosis_router)


@app.get("/health")
async def health() -> Dict:
    """返回 API、Redis 和 PostgreSQL 的健康状态。"""
    return await get_health(settings)


@app.get("/")
async def root() -> Dict[str, str]:
    """返回服务名称和运行状态，用于快速确认 API 已启动。"""
    return {"name": settings.app_name, "status": "running"}
