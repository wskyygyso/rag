"""生产上线前预检命令行入口。"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 支持从项目根目录直接执行 `python scripts/preflight_check.py`。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.preflight import run_preflight
from app.settings import get_settings


def parse_args() -> argparse.Namespace:
    """解析严格模式和 JSON 输出参数。"""
    parser = argparse.ArgumentParser(description="RAgent 生产上线预检")
    parser.add_argument("--strict", action="store_true", help="生产门禁模式，警告也视为未通过")
    parser.add_argument("--json", action="store_true", help="输出 JSON 结果")
    return parser.parse_args()


async def main_async() -> int:
    """执行预检，存在 FAIL 或严格模式警告时返回非零状态。"""
    args = parse_args()
    results = await run_preflight(get_settings(), strict=args.strict)
    if args.json:
        print(
            json.dumps(
                [result.__dict__ for result in results],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for result in results:
            print(f"{result.status:<4} {result.name:<28} {result.message}")
    failed_status = {"FAIL", "WARN"} if args.strict else {"FAIL"}
    return 0 if all(result.status not in failed_status for result in results) else 1


def main() -> None:
    """脚本入口。"""
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
