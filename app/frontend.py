"""诊断 RAgent 轻量前端页面入口。"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["frontend"])
_UI_PATH = Path(__file__).resolve().parent.parent / "static" / "index.html"


@router.get("/ui", response_class=FileResponse)
async def ui_page() -> FileResponse:
    """返回无需构建工具即可使用的诊断任务前端页面。"""
    return FileResponse(_UI_PATH, media_type="text/html")
