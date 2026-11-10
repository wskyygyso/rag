"""诊断 RAgent 轻量前端页面入口。"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["frontend"])
_UI_PATH = Path(__file__).resolve().parent.parent / "static" / "index.html"


def _ui_response() -> FileResponse:
    """构造诊断工作台首页响应，供根路径和兼容路径共同复用。

    Returns:
        指向 `static/index.html` 的 HTML 文件响应。页面由原生 HTML、CSS 和
        JavaScript 提供，不依赖额外前端构建产物；文件缺失时由 FastAPI 返回
        标准文件读取错误。
    """
    return FileResponse(_UI_PATH, media_type="text/html")


@router.get("/", response_class=FileResponse)
async def root_page() -> FileResponse:
    """把服务根路径返回为可直接使用的诊断工作台页面。

    Returns:
        诊断页面。服务存活和依赖状态继续由 `/health` 系列接口负责，根路径不再
        返回 JSON，避免浏览器测试时只看到运行状态对象。
    """
    return _ui_response()


@router.get("/ui", response_class=FileResponse)
async def ui_page() -> FileResponse:
    """保留 `/ui` 作为诊断工作台页面兼容入口。

    Returns:
        与根路径相同的诊断页面，供旧链接、书签和部署探针继续使用。
    """
    return _ui_response()
