"""检查 Python 类、函数和参数注释覆盖率。"""

import argparse
import ast
from pathlib import Path
from typing import Iterable, List, Tuple

GENERIC_PHRASES = ("输入参数", "输出参数", "输入值", "待处理或返回的结构化结果")


def _argument_names(node: ast.AST) -> List[str]:
    """返回函数中需要说明的参数名称，排除 self 和 cls。

    参数:
        - `node`：待检查的 Python AST 节点。
    """
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    arguments = [
        item.arg
        for item in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if item.arg not in {"self", "cls"}
    ]
    if node.args.vararg:
        arguments.append(f"*{node.args.vararg.arg}")
    if node.args.kwarg:
        arguments.append(f"**{node.args.kwarg.arg}")
    return arguments


def check_file(path: Path) -> List[str]:
    """检查单个 Python 文件并返回注释问题列表。

    参数:
        - `path`：需要执行 AST 注释审计的 Python 文件。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        docstring = ast.get_docstring(node) or ""
        location = f"{path}:{node.lineno}:{node.name}"
        if not docstring.strip():
            issues.append(f"{location}: 缺少职责注释")
            continue
        if _argument_names(node) and "参数:" not in docstring:
            issues.append(f"{location}: 缺少参数说明")
        for phrase in GENERIC_PHRASES:
            if phrase in docstring:
                issues.append(f"{location}: 使用了泛化措辞 {phrase}")
    return issues


def check_paths(paths: Iterable[str]) -> List[str]:
    """检查多个目录中的全部 Python 文件。

    参数:
        - `paths`：需要递归检查的目录路径集合。
    """
    issues = []
    for root in paths:
        for path in sorted(Path(root).rglob("*.py")):
            issues.extend(check_file(path))
    return issues


def parse_args() -> argparse.Namespace:
    """解析待检查目录，默认覆盖应用、脚本和测试。"""
    parser = argparse.ArgumentParser(description="检查 RAgent Python 注释覆盖率")
    parser.add_argument("paths", nargs="*", default=["app", "scripts", "tests"])
    return parser.parse_args()


def main() -> None:
    """执行注释审计；发现问题时输出明细并返回非零状态。"""
    args = parse_args()
    issues = check_paths(args.paths)
    if issues:
        print("\n".join(issues))
        raise SystemExit(1)
    print("comment coverage: ok")


if __name__ == "__main__":
    main()
