"""项目仓库的安全文件遍历基础设施。

大型业务项目常同时挂载源码、运行态目录和异常符号链接。诊断索引只应读取项目
白名单内的普通文件；单个目录不可访问或挂载对象损坏时，应跳过该目录并继续处理
其他源码，不能让一次 `scandir` 失败中断整个诊断任务。

本模块不解析业务内容，也不修改仓库。所有遍历均不跟随符号链接，避免异常链接
循环和越过项目根目录读取宿主机文件。
"""

import logging
import os
from pathlib import Path
from typing import Iterable, Iterator, Optional


LOGGER = logging.getLogger(__name__)

# 生成缓存、依赖和运行态目录不属于业务源码。仓库扫描和全文检索共用同一份
# 排除清单，避免不同入口对“生产源码”的范围判断不一致。
IGNORED_REPOSITORY_PARTS = frozenset(
    {
        ".git",
        ".gitnexus",
        ".idea",
        ".vscode",
        "__pycache__",
        "vendor",
        "node_modules",
        "runtime",
        "storage",
        "cache",
    }
)


def _report_walk_error(error: OSError) -> None:
    """记录目录遍历错误，并允许其余可读目录继续扫描。

    参数:
        - `error`：`os.walk` 在读取目录或目录项时捕获到的文件系统异常；异常中的
          `filename` 通常指向损坏挂载、权限不足或并发删除的路径。

    返回:
        无返回值。调用方是 `os.walk` 的错误回调，本函数只写入带路径的诊断日志，
        不向用户抛出底层操作系统错误。
    """
    target = getattr(error, "filename", "") or "<unknown>"
    LOGGER.warning(
        "跳过不可读取的仓库路径 path=%s errno=%s error=%s",
        target,
        getattr(error, "errno", None),
        error,
    )


def _safe_directory_names(directory: Path, names: Iterable[str]) -> Iterator[str]:
    """筛选可以继续递归的普通子目录名。

    参数:
        - `directory`：当前正在遍历的项目内目录。
        - `names`：`os.walk` 报告的候选子目录名集合。

    生成:
        名称不在生成缓存排除表中、且不是符号链接的子目录名。无法完成链接属性
        检查的目录项按不可安全读取处理并跳过。
    """
    for name in names:
        if name in IGNORED_REPOSITORY_PARTS:
            continue
        candidate = directory / name
        try:
            if candidate.is_symlink():
                continue
        except OSError:
            continue
        yield name


def iter_repository_files(
    root: Path,
    suffixes: Optional[Iterable[str]] = None,
) -> Iterator[Path]:
    """遍历项目中的普通文件，并隔离单个异常目录的读取失败。

    参数:
        - `root`：已经过 `CodeRepository` 白名单校验的项目根目录。
        - `suffixes`：允许返回的文件后缀集合，例如 `{".php", ".md"}`；传入
          `None` 表示不按扩展名过滤。后缀比较不区分大小写。

    生成:
        项目内的无符号链接普通文件，路径保持为 `Path`。遍历不跟随目录或文件
        符号链接；异常挂载、权限错误和损坏目录项会被记录后跳过。该函数不会因为
        单个路径不可读而终止整个项目扫描。
    """
    allowed_suffixes = (
        {suffix.casefold() for suffix in suffixes}
        if suffixes is not None
        else None
    )
    for current, directory_names, file_names in os.walk(
        root,
        topdown=True,
        onerror=_report_walk_error,
        followlinks=False,
    ):
        current_path = Path(current)
        directory_names[:] = sorted(
            _safe_directory_names(current_path, directory_names)
        )
        for file_name in sorted(file_names):
            candidate = current_path / file_name
            if (
                allowed_suffixes is not None
                and candidate.suffix.casefold() not in allowed_suffixes
            ):
                continue
            try:
                if candidate.is_symlink() or not candidate.is_file():
                    continue
            except OSError:
                continue
            yield candidate


def iter_git_markers(root: Path) -> Iterator[Path]:
    """发现项目内各独立仓库的 `.git` 标记，同时跳过异常目录。

    参数:
        - `root`：已经过项目白名单校验的仓库根目录。

    生成:
        `.git` 目录或工作树指针文件路径。发现标记后不会进入 `.git` 内部继续
        扫描；依赖、缓存和损坏链接目录同样被跳过。
    """
    for current, directory_names, file_names in os.walk(
        root,
        topdown=True,
        onerror=_report_walk_error,
        followlinks=False,
    ):
        current_path = Path(current)
        marker = current_path / ".git"
        if ".git" in directory_names or ".git" in file_names:
            try:
                if not marker.is_symlink() and (
                    marker.is_dir() or marker.is_file()
                ):
                    yield marker
            except OSError:
                pass
        directory_names[:] = sorted(
            _safe_directory_names(
                current_path,
                (name for name in directory_names if name != ".git"),
            )
        )
