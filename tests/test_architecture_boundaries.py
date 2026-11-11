"""重构分层依赖边界测试。"""

import ast
from pathlib import Path


_APP_ROOT = Path(__file__).parents[1] / "app"
_DOMAIN_ROOT = _APP_ROOT / "domain"
_FORBIDDEN_DOMAIN_PREFIXES = (
    "fastapi",
    "celery",
    "asyncpg",
    "redis",
)


def _imported_modules(path: Path) -> set[str]:
    """解析 Python 文件中声明的绝对导入模块名。

    参数:
        - `path`：待扫描的 Python 文件路径，类型为 `Path`。

    Returns:
        文件内 `import x` 和 `from x import y` 声明的顶层模块集合。语法错误会
        直接抛给测试，避免架构守卫在不可解析代码上静默通过。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_domain_modules_do_not_depend_on_api_queue_or_storage_drivers() -> None:
    """验证领域分析层不直接依赖 HTTP、Celery、Redis 或数据库驱动。"""
    violations = []
    if _DOMAIN_ROOT.exists():
        for path in _DOMAIN_ROOT.rglob("*.py"):
            for module in _imported_modules(path):
                if module.startswith(_FORBIDDEN_DOMAIN_PREFIXES):
                    violations.append(f"{path.relative_to(_APP_ROOT)} -> {module}")

    assert violations == []
