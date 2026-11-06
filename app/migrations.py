"""按版本顺序执行 PostgreSQL 迁移并检测已发布脚本漂移。"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Union

_MIGRATION_NAME = re.compile(r"^(?P<version>[0-9]{3})_[a-z0-9_]+\.sql$")
_ADVISORY_LOCK_KEY = 7_301_746_001

CREATE_SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(255) PRIMARY KEY,
    checksum CHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

COMMENT_SCHEMA_MIGRATIONS_SQL = """
COMMENT ON TABLE schema_migrations IS 'RAgent 元数据库已应用迁移版本和脚本校验和';
COMMENT ON COLUMN schema_migrations.version IS '迁移文件名，按三位递增版本号排序执行';
COMMENT ON COLUMN schema_migrations.checksum IS '迁移 SQL 文件的 SHA-256，用于阻止已发布脚本被静默修改';
COMMENT ON COLUMN schema_migrations.applied_at IS '迁移首次成功提交的 UTC 时间'
"""


class MigrationError(RuntimeError):
    """表示迁移文件缺失、重复、漂移或执行失败的生产阻断错误。"""


@dataclass(frozen=True)
class MigrationFile:
    """描述一个待执行 SQL 迁移及其完整性校验值。"""

    version: str
    path: Path
    checksum: str


def _checksum(content: bytes) -> str:
    """计算迁移文件原始字节的 SHA-256。

    参数:
        - `content`：迁移 SQL 文件的原始字节，避免换行转换导致校验漂移。
    """
    return hashlib.sha256(content).hexdigest()


def load_migrations(directory: Union[str, Path]) -> List[MigrationFile]:
    """读取并校验连续编号的 SQL 迁移文件。

    参数:
        - `directory`：包含 `NNN_name.sql` 迁移脚本的目录。

    返回:
        按版本号升序排列的迁移列表；目录为空、命名非法、版本重复或断号时抛出
        `MigrationError`，防止生产环境漏跑迁移。
    """
    migration_dir = Path(directory)
    if not migration_dir.is_dir():
        raise MigrationError(f"迁移目录不存在：{migration_dir}")
    migrations = []
    for path in sorted(migration_dir.glob("*.sql")):
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise MigrationError(f"迁移文件名不符合 NNN_name.sql：{path.name}")
        migrations.append(
            MigrationFile(
                version=path.name,
                path=path,
                checksum=_checksum(path.read_bytes()),
            )
        )
    if not migrations:
        raise MigrationError("迁移目录中没有 SQL 文件")
    versions = [int(item.version[:3]) for item in migrations]
    duplicates = sorted({value for value in versions if versions.count(value) > 1})
    if duplicates:
        raise MigrationError(f"迁移版本重复：{duplicates}")
    expected = list(range(1, len(versions) + 1))
    if versions != expected:
        raise MigrationError("迁移版本必须从 001 开始连续编号")
    return migrations


async def inspect_migrations(pool, directory: Union[str, Path]) -> Dict[str, Any]:
    """检查迁移目录相对数据库的待执行与校验漂移状态。

    参数:
        - `pool`：RAgent 元数据库连接池。
        - `directory`：包含正式迁移脚本的目录。

    返回:
        包含 `pending`、`applied`、`drifted` 三个版本的只读状态摘要；不执行 SQL 迁移。
    """
    migrations = load_migrations(directory)
    expected = {item.version: item.checksum for item in migrations}
    async with pool.acquire() as connection:
        table_exists = await connection.fetchval("SELECT to_regclass('public.schema_migrations')")
        if table_exists is None:
            return {
                "pending": list(expected),
                "applied": [],
                "drifted": [],
            }
        rows = await connection.fetch(
            "SELECT version, checksum FROM schema_migrations ORDER BY version"
        )
    recorded = {str(row["version"]): str(row["checksum"]) for row in rows}
    return {
        "pending": [version for version in expected if version not in recorded],
        "applied": [version for version in expected if version in recorded],
        "drifted": [
            version
            for version, checksum in expected.items()
            if version in recorded and recorded[version] != checksum
        ],
    }


async def apply_migrations(pool, directory: Union[str, Path]) -> List[str]:
    """在单个事务和咨询锁内应用全部待执行迁移。

    参数:
        - `pool`：RAgent 元数据库连接池。
        - `directory`：包含正式迁移脚本的目录。

    返回:
        本次新应用迁移的版本列表；已应用脚本校验和不一致、SQL 失败或并发迁移锁异常时
        抛出异常并回滚本事务内全部新迁移。
    """
    migrations = load_migrations(directory)
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock($1)", _ADVISORY_LOCK_KEY)
            await connection.execute(CREATE_SCHEMA_MIGRATIONS_SQL)
            await connection.execute(COMMENT_SCHEMA_MIGRATIONS_SQL)
            rows = await connection.fetch(
                "SELECT version, checksum FROM schema_migrations ORDER BY version"
            )
            recorded = {str(row["version"]): str(row["checksum"]) for row in rows}
            for migration in migrations:
                previous_checksum = recorded.get(migration.version)
                if previous_checksum is not None:
                    if previous_checksum != migration.checksum:
                        raise MigrationError(
                            f"已应用迁移校验和不一致：{migration.version}"
                        )
                    continue
                sql = migration.path.read_text(encoding="utf-8")
                await connection.execute(sql)
                await connection.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)",
                    migration.version,
                    migration.checksum,
                )
    return [item.version for item in migrations if item.version not in recorded]
