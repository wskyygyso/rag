"""按保留期限统计和分批清理历史数据。"""

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class RetentionTarget:
    """描述一个可按时间分批清理的数据目标。"""

    name: str
    count_sql: str
    delete_sql: str


RETENTION_TARGETS = (
    RetentionTarget(
        name="tasks",
        count_sql="""
            SELECT COUNT(*) FROM diagnostic_tasks
            WHERE status IN ('COMPLETED', 'FAILED', 'CANCELLED')
              AND updated_at < NOW() - ($1 * INTERVAL '1 day')
        """,
        delete_sql="""
            DELETE FROM diagnostic_tasks
            WHERE id IN (
                SELECT id FROM diagnostic_tasks
                WHERE status IN ('COMPLETED', 'FAILED', 'CANCELLED')
                  AND updated_at < NOW() - ($1 * INTERVAL '1 day')
                ORDER BY updated_at ASC
                LIMIT $2
            )
        """,
    ),
    RetentionTarget(
        name="validation_audit",
        count_sql="""
            SELECT COUNT(*) FROM validation_audit
            WHERE created_at < NOW() - ($1 * INTERVAL '1 day')
        """,
        delete_sql="""
            DELETE FROM validation_audit
            WHERE id IN (
                SELECT id FROM validation_audit
                WHERE created_at < NOW() - ($1 * INTERVAL '1 day')
                ORDER BY created_at ASC
                LIMIT $2
            )
        """,
    ),
    RetentionTarget(
        name="security_audit",
        count_sql="""
            SELECT COUNT(*) FROM security_audit
            WHERE created_at < NOW() - ($1 * INTERVAL '1 day')
        """,
        delete_sql="""
            DELETE FROM security_audit
            WHERE id IN (
                SELECT id FROM security_audit
                WHERE created_at < NOW() - ($1 * INTERVAL '1 day')
                ORDER BY created_at ASC
                LIMIT $2
            )
        """,
    ),
    RetentionTarget(
        name="document_index_runs",
        count_sql="""
            SELECT COUNT(*) FROM document_index_runs
            WHERE status IN ('COMPLETED', 'FAILED')
              AND COALESCE(finished_at, started_at) < NOW() - ($1 * INTERVAL '1 day')
        """,
        delete_sql="""
            DELETE FROM document_index_runs
            WHERE id IN (
                SELECT id FROM document_index_runs
                WHERE status IN ('COMPLETED', 'FAILED')
                  AND COALESCE(finished_at, started_at) < NOW() - ($1 * INTERVAL '1 day')
                ORDER BY COALESCE(finished_at, started_at) ASC
                LIMIT $2
            )
        """,
    ),
)


def _affected_rows(result: str) -> int:
    """从 PostgreSQL DELETE 命令结果中提取受影响行数。"""
    suffix = result.rsplit(" ", 1)[-1]
    return int(suffix) if suffix.isdigit() else 0


def _validate_retention_args(
    retention_days: Dict[str, int], batch_size: int, max_batches: int
) -> None:
    """校验期限、批大小和最大批次数，避免误删和非终止循环。"""
    for name, days in retention_days.items():
        if days < 1 or days > 3650:
            raise ValueError(f"{name} 保留天数必须在 1 到 3650 天之间")
    if batch_size < 1 or batch_size > 10_000:
        raise ValueError("批大小必须在 1 到 10000 之间")
    if max_batches < 1 or max_batches > 10_000:
        raise ValueError("最大批次数必须在 1 到 10000 之间")


async def cleanup_retention(
    pool,
    retention_days: Dict[str, int],
    *,
    batch_size: int = 500,
    max_batches: int = 100,
    apply: bool = False,
) -> Dict[str, Any]:
    """统计过期数据；应用模式下按小批次删除并返回完整执行状态。"""
    expected_names = {target.name for target in RETENTION_TARGETS}
    if set(retention_days) != expected_names:
        raise ValueError("保留策略必须包含全部数据目标")
    _validate_retention_args(retention_days, batch_size, max_batches)

    counts = {}
    deleted = {}
    complete = True
    for target in RETENTION_TARGETS:
        days = retention_days[target.name]
        counts[target.name] = int(await pool.fetchval(target.count_sql, days))
        deleted[target.name] = 0
        if not apply or counts[target.name] == 0:
            continue
        for _ in range(max_batches):
            affected = _affected_rows(
                await pool.execute(target.delete_sql, days, batch_size)
            )
            deleted[target.name] += affected
            if affected < batch_size:
                break
        else:
            complete = False

    return {
        "mode": "apply" if apply else "dry-run",
        "retention_days": retention_days,
        "candidate_counts": counts,
        "deleted_counts": deleted,
        "batch_size": batch_size,
        "max_batches": max_batches,
        "complete": complete,
    }
