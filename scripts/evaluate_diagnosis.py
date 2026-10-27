"""运行离线 PHP 诊断准确性评测基线。"""

import argparse
import json
import sys
from pathlib import Path

# 支持从项目根目录直接执行诊断评测。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.diagnosis_eval import evaluate_cases, load_cases


def parse_args() -> argparse.Namespace:
    """解析评测集、仓库根目录和最低通过率。"""
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="RAgent 离线诊断准确性评测")
    parser.add_argument("--root", default=str(project_root / "tests" / "fixtures" / "php_repo"))
    parser.add_argument("--cases", default=str(project_root / "tests" / "fixtures" / "diagnosis_cases.json"))
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    return parser.parse_args()


def main() -> None:
    """执行评测并以通过率阈值决定退出码。"""
    args = parse_args()
    if args.min_pass_rate < 0 or args.min_pass_rate > 1:
        raise SystemExit("--min-pass-rate 必须在 0 到 1 之间")
    result = evaluate_cases(args.root, load_cases(args.cases))
    result["min_pass_rate"] = args.min_pass_rate
    result["threshold_passed"] = result["pass_rate"] >= args.min_pass_rate
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["threshold_passed"] else 1)


if __name__ == "__main__":
    main()
