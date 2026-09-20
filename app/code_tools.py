"""受控代码检索工具。

本模块只允许访问配置的项目仓库，并对项目标识、文件路径、文件大小、
搜索结果数量和执行时间做限制，供 Agent 获取可审计的代码证据。
"""

import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional


class CodeToolError(ValueError):
    """代码工具请求非法或超出仓库白名单时抛出的业务异常。"""


class CodeRepository:
    """表示一个经过路径白名单校验的项目代码仓库。"""

    def __init__(self, root: str, project: str) -> None:
        """校验项目标识并解析项目根目录。

        Args:
            root: 所有项目仓库的根目录。
            project: 项目标识，对应根目录下的一级子目录。
        """
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", project):
            raise CodeToolError("项目标识包含非法字符")
        base = Path(root).expanduser().resolve()
        repository = (base / project).resolve()
        if base not in repository.parents:
            raise CodeToolError("项目路径超出代码仓库白名单")
        if not repository.is_dir():
            raise CodeToolError(f"代码仓库不存在: {project}")
        self.base = base
        self.path = repository

    def safe_file(self, relative_path: str) -> Path:
        """将项目内相对路径解析为安全文件路径并阻止路径穿越。"""
        candidate = (self.path / relative_path).resolve()
        if self.path not in candidate.parents and candidate != self.path:
            raise CodeToolError("文件路径超出项目目录")
        if not candidate.is_file():
            raise CodeToolError(f"文件不存在: {relative_path}")
        return candidate


def read_file(
    root: str,
    project: str,
    relative_path: str,
    start_line: int = 1,
    end_line: int = 200,
    max_bytes: int = 512_000,
) -> Dict:
    """读取项目文件的指定行区间，并返回带行号的代码内容。"""
    if start_line < 1 or end_line < start_line or end_line - start_line > 1000:
        raise CodeToolError("行号范围无效，最多读取 1000 行")
    file_path = CodeRepository(root, project).safe_file(relative_path)
    if file_path.stat().st_size > max_bytes:
        raise CodeToolError("文件超过单次读取大小限制")
    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    selected = lines[start_line - 1 : end_line]
    return {
        "project": project,
        "file": relative_path,
        "start_line": start_line,
        "end_line": min(end_line, len(lines)),
        "content": "\n".join(f"{start_line + index}: {line}" for index, line in enumerate(selected)),
    }


def _iter_text_files(repository: Path, extensions: Iterable[str]) -> Iterable[Path]:
    """遍历可检索的文本文件，跳过依赖、缓存和版本控制目录。"""
    allowed = set(extensions)
    ignored = {".git", "vendor", "node_modules", "runtime", "storage", "cache"}
    for path in repository.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in allowed:
            continue
        if any(part in ignored for part in path.parts):
            continue
        yield path


def _python_search(repository: Path, query: str, max_results: int, extensions: List[str]) -> List[Dict]:
    """在没有 ripgrep 时使用 Python 执行大小写不敏感的文本搜索。"""
    matches: List[Dict] = []
    for path in _iter_text_files(repository, extensions):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            if query.lower() in line.lower():
                matches.append({
                    "file": str(path.relative_to(repository)),
                    "line": line_number,
                    "content": line.strip(),
                })
                if len(matches) >= max_results:
                    return matches
    return matches


def search_code(
    root: str,
    project: str,
    query: str,
    max_results: int = 20,
    extensions: Optional[List[str]] = None,
) -> Dict:
    """在项目仓库中搜索固定字符串并返回文件、行号和匹配内容。"""
    query = query.strip()
    if not query or len(query) > 200:
        raise CodeToolError("搜索关键词不能为空且不能超过 200 个字符")
    if max_results < 1 or max_results > 100:
        raise CodeToolError("搜索结果数量必须在 1 到 100 之间")
    repository = CodeRepository(root, project)
    extensions = extensions or [".php", ".inc", ".phtml", ".conf", ".ini", ".yaml", ".yml", ".json"]
    matches: List[Dict] = []
    rg = shutil.which("rg")
    if rg:
        globs = [item if item.startswith("*") else f"*{item}" for item in extensions]
        command = [
            rg,
            "--line-number",
            "--no-heading",
            "--color=never",
            "--max-count",
            str(max_results),
            "--glob",
            "!.git/**",
            "--glob",
            "!vendor/**",
            "--glob",
            "!node_modules/**",
        ]
        for pattern in globs:
            command.extend(["--glob", pattern])
        command.extend(["-F", query, str(repository.path)])
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodeToolError("代码搜索超时") from exc
        if completed.returncode not in (0, 1):
            raise CodeToolError(f"代码搜索失败: {completed.stderr.strip()}")
        for line in completed.stdout.splitlines()[:max_results]:
            file_name, line_number, content = line.split(":", 2)
            matches.append({
                "file": str(Path(file_name).relative_to(repository.path)),
                "line": int(line_number),
                "content": content.strip(),
            })
    else:
        matches = _python_search(repository.path, query, max_results, extensions)
    return {"project": project, "query": query, "matches": matches, "total": len(matches)}


def extract_search_queries(question: str, max_queries: int = 8) -> List[str]:
    """从用户问题提取中英文关键词，并按首次出现顺序去重。"""
    candidates = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", question)
    result: List[str] = []
    for candidate in candidates:
        if candidate not in result:
            result.append(candidate)
        if len(result) >= max_queries:
            break
    return result or [question[:200]]
