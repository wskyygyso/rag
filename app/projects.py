"""项目发现和项目概览聚合。"""

import asyncio
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.auth import ApiIdentity

PROJECT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def read_repository_revision(root: str, project: str) -> Optional[str]:
    """读取项目 Git HEAD；非 Git 仓库或命令失败时返回 None。

    参数:
        - `root`：所有诊断项目的仓库根目录。
        - `project`：需要读取提交版本的目录名。
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(Path(root) / project), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    revision = completed.stdout.strip()
    return revision or None


def discover_projects(root: str, identity: Optional[ApiIdentity] = None) -> List[Dict[str, Any]]:
    """列出目录白名单中存在且调用方有权访问的项目。

    参数:
        - `root`：挂载的代码仓库根目录。
        - `identity`：已认证调用方；未启用认证时为 None。
    """
    root_path = Path(root)
    if not root_path.is_dir():
        return []
    projects = []
    for path in sorted(root_path.iterdir(), key=lambda item: item.name):
        if not path.is_dir() or not PROJECT_NAME_PATTERN.fullmatch(path.name):
            continue
        if identity is not None and "*" not in identity.projects and path.name not in identity.projects:
            continue
        projects.append(
            {
                "name": path.name,
                "repository_revision": read_repository_revision(root, path.name),
            }
        )
    return projects


async def get_project_overview(pool, project: str) -> Dict[str, Any]:
    """聚合项目任务状态、最近索引运行和反馈分布。

    参数:
        - `pool`：RAgent 元数据库连接池。
        - `project`：需要汇总的诊断项目标识。
    """
    task_rows, latest_index, feedback_rows = await asyncio.gather(
        pool.fetch(
            "SELECT status, COUNT(*) AS count FROM diagnostic_tasks WHERE project = $1 GROUP BY status",
            project,
        ),
        pool.fetchrow(
            """
            SELECT id, status, chunk_count, repository_revision, index_version,
                   error, started_at, finished_at
            FROM document_index_runs
            WHERE project = $1
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """,
            project,
        ),
        pool.fetch(
            """
            SELECT f.rating, COUNT(*) AS count
            FROM diagnostic_feedback AS f
            JOIN diagnostic_tasks AS t ON t.id = f.task_id
            WHERE t.project = $1
            GROUP BY f.rating
            """,
            project,
        ),
    )
    statuses = {str(row["status"]): int(row["count"]) for row in task_rows}
    feedback = {
        "correct": 0,
        "inaccurate": 0,
        "insufficient_evidence": 0,
        "escalate": 0,
    }
    for row in feedback_rows:
        if row["rating"] in feedback:
            feedback[row["rating"]] = int(row["count"])
    index_run = None
    if latest_index is not None:
        index_run = dict(latest_index)
        for key in ("started_at", "finished_at"):
            value = index_run.get(key)
            if hasattr(value, "isoformat"):
                index_run[key] = value.isoformat()
    return {
        "project": project,
        "tasks_total": sum(statuses.values()),
        "task_statuses": dict(sorted(statuses.items())),
        "feedback": feedback,
        "latest_index_run": index_run,
    }


async def get_project_task_board(pool, project: str, recent_limit: int = 5) -> Dict[str, Any]:
    """聚合项目任务状态、运行中任务和最近终态任务。

    参数:
        - `pool`：RAgent 元数据库连接池。
        - `project`：需要查询任务看板的诊断项目标识。
        - `recent_limit`：最近终态任务返回数量。
    """
    if recent_limit < 1 or recent_limit > 20:
        raise ValueError("最近任务数量必须在 1 到 20 之间")
    status_rows, active_rows, recent_rows = await asyncio.gather(
        pool.fetch(
            "SELECT status, COUNT(*) AS count FROM diagnostic_tasks WHERE project = $1 GROUP BY status",
            project,
        ),
        pool.fetch(
            """
            SELECT id, question, status, progress, current_step, created_at, updated_at
            FROM diagnostic_tasks
            WHERE project = $1
              AND status IN ('QUEUED', 'RUNNING', 'SEARCHING', 'VERIFYING', 'REPORTING')
            ORDER BY updated_at DESC, id DESC
            LIMIT 20
            """,
            project,
        ),
        pool.fetch(
            """
            SELECT id, question, status, progress, current_step, created_at, updated_at
            FROM diagnostic_tasks
            WHERE project = $1
              AND status IN ('COMPLETED', 'FAILED', 'CANCELLED')
            ORDER BY updated_at DESC, id DESC
            LIMIT $2
            """,
            project,
            recent_limit,
        ),
    )

    def serialize(row):
        """将任务行的日期字段转换为 ISO 字符串。

        参数:
            - `row`：数据库返回的单条任务记录。
        """
        item = dict(row)
        for key in ("created_at", "updated_at"):
            value = item.get(key)
            if hasattr(value, "isoformat"):
                item[key] = value.isoformat()
        return item

    return {
        "project": project,
        "status_counts": {str(row["status"]): int(row["count"]) for row in status_rows},
        "active_tasks": [serialize(row) for row in active_rows],
        "recent_tasks": [serialize(row) for row in recent_rows],
    }
