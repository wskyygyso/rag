"""建立项目文档索引的命令行入口。

运行时实现位于 app.document_index，生产镜像无需额外设置 scripts 包路径。
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 支持从项目根目录直接执行脚本。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.document_index import (
    INDEX_VERSION,
    DocumentIndexResult,
    _repository_revision,
    _run,
    _run_detailed,
    run_document_index,
    run_document_index_chunks,
)

def main() -> None:
    """解析命令行参数并执行项目文档索引。"""
    parser = argparse.ArgumentParser(description="建立项目统一文档分块索引")
    parser.add_argument("project", help="项目目录名称")
    parser.add_argument("--dry-run", action="store_true", help="只扫描并统计分块，不连接数据库")
    args = parser.parse_args()
    result = asyncio.run(run_document_index(args.project, dry_run=args.dry_run))
    action = "扫描到" if args.dry_run else "复用" if result.reused else "已写入"
    print(f"{action} {result.chunk_count} 个文档分块")


if __name__ == "__main__":
    main()
