import asyncio
from typing import Any, Dict

import asyncpg
from redis import asyncio as redis_asyncio

from app.settings import Settings


async def check_redis(settings: Settings) -> Dict[str, Any]:
    client = redis_asyncio.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.ping()
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    finally:
        await client.aclose()


async def check_postgres(settings: Settings) -> Dict[str, Any]:
    connection = None
    try:
        connection = await asyncpg.connect(settings.database_url, timeout=3)
        await connection.fetchval("SELECT 1")
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    finally:
        if connection is not None:
            await connection.close()


async def get_health(settings: Settings) -> Dict[str, Any]:
    redis_result, postgres_result = await asyncio.gather(
        check_redis(settings), check_postgres(settings)
    )
    dependencies = {"redis": redis_result, "postgres": postgres_result}
    overall = "ok" if all(item["status"] == "ok" for item in dependencies.values()) else "degraded"
    return {"status": overall, "environment": settings.app_env, "dependencies": dependencies}

