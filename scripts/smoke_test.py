"""部署后 RAgent 只读烟雾测试，不创建诊断任务或修改生产数据。"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Tuple


def parse_args() -> argparse.Namespace:
    """解析发布后烟雾测试地址、项目与认证要求。"""
    parser = argparse.ArgumentParser(description="RAgent 部署后只读烟雾测试")
    parser.add_argument("--base-url", default=os.getenv("RAGENT_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--project", default=os.getenv("RAGENT_SMOKE_PROJECT", ""))
    parser.add_argument("--require-api", action="store_true", help="要求受保护 API 和数据库指标可用")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def request_json(base_url: str, path: str, api_key: str, timeout: float) -> Tuple[int, Dict[str, Any]]:
    """发送只读 HTTP 请求并解析有限大小的 JSON 响应。

    参数:
        - `base_url`：部署后 API 根地址，不接受包含用户信息的 URL。
        - `path`：以 `/` 开头的只读接口路径。
        - `api_key`：仅在内存中附加的 RAgent API Key；空字符串表示匿名请求。
        - `timeout`：单次请求超时秒数，避免烟雾测试无限阻塞。

    返回:
        HTTP 状态码和 JSON 对象；非 JSON 响应会被包装为稳定错误，不输出 API Key。
    """
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        headers=headers,
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            payload = response.read(1_000_001)
    except urllib.error.HTTPError as exc:
        status = exc.code
        payload = exc.read(1_000_001)
    if len(payload) > 1_000_000:
        raise RuntimeError(f"{path} 响应超过 1MB")
    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        # Prometheus 指标值可能出现在响应较后的位置，保留完整文本供门禁判断；
        # 响应读取已限制为 1MB，避免异常接口耗尽烟雾测试内存。
        body = {"raw": payload.decode("utf-8", errors="replace")}
    return status, body


def run_smoke(args: argparse.Namespace) -> Dict[str, Any]:
    """执行健康、项目、指标和可选项目看板只读检查。

    参数:
        - `args`：命令行参数，包含地址、项目、认证要求和超时。

    返回:
        包含每项检查状态和整体 `passed` 的 JSON 可序列化摘要。
    """
    api_key = os.getenv("RAGENT_API_KEY", "")
    checks = []

    status, health = request_json(args.base_url, "/health/ready", "", args.timeout)
    checks.append(
        {
            "name": "readiness",
            "passed": status == 200 and health.get("status") == "ok",
            "status_code": status,
        }
    )
    if args.require_api:
        status, projects = request_json(args.base_url, "/api/projects", api_key, args.timeout)
        checks.append(
            {
                "name": "authenticated_projects",
                "passed": status == 200 and isinstance(projects.get("projects"), list),
                "status_code": status,
            }
        )
        status, metrics = request_json(args.base_url, "/api/metrics", api_key, args.timeout)
        checks.append(
            {
                "name": "database_metrics",
                "passed": status == 200 and "ragent_database_up 1" in metrics.get("raw", ""),
                "status_code": status,
            }
        )
    if args.project:
        from urllib.parse import quote

        status, board = request_json(
            args.base_url,
            f"/api/projects/{quote(args.project, safe='')}/task-board",
            api_key,
            args.timeout,
        )
        checks.append(
            {
                "name": "project_task_board",
                "passed": status == 200 and board.get("project") == args.project,
                "status_code": status,
            }
        )
    return {"base_url": args.base_url, "checks": checks, "passed": all(item["passed"] for item in checks)}


def main() -> None:
    """脚本入口：失败时以非零退出码阻断发布验收。"""
    args = parse_args()
    try:
        result = run_smoke(args)
    except Exception as exc:
        result = {"checks": [], "passed": False, "error_type": type(exc).__name__}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for check in result["checks"]:
            print(f"{'PASS' if check['passed'] else 'FAIL'} {check['name']} HTTP {check['status_code']}")
        if result.get("error_type"):
            print(f"FAIL smoke_test {result['error_type']}")
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
