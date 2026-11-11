"""钻石兑换等数值换算事实分析器。

分析器只读取 `EvidenceLine` 和问题画像，不负责检索或持久化。
"""

import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.domain.diagnosis.analyzers.common import EvidenceLine, _config_integer, _find_line, _find_line_after, _test_mode_integer_pair, _evidence_lines


def _analyze_diamond_exchange(lines: Sequence[EvidenceLine]) -> List[Dict[str, Any]]:
    """分析用户钻石兑金币的配置值和服务端输入守卫。

    参数:
        - `lines`：未知业务问题经通用检索和动态符号扩展后得到的源码证据行，
          类型为 `Sequence[EvidenceLine]`。分析器要求配置定义和兑换方法守卫
          同时出现，避免仅凭注释或展示文案猜测规则。

    Returns:
        钻石兑金币的比例、最低数量、整数倍和上限事实。没有可信配置证据时返回
        空列表；值为 0 的上限只解释为“当前配置未限制”，不推断产品最终口径。
    """
    config_anchor = _find_line(
        lines,
        file_suffix="php/config/diamonds.php",
        contains=("'diamond_exchange'", "=>"),
    )
    if config_anchor is None:
        return []
    ratio_line = _find_line_after(lines, config_anchor, "'diamond_to_coin'", max_distance=6)
    minimum_line = _find_line_after(lines, config_anchor, "'min_diamonds'", max_distance=8)
    multiple_line = _find_line_after(lines, config_anchor, "'multiple'", max_distance=10)
    maximum_line = _find_line_after(lines, config_anchor, "'max_diamonds'", max_distance=12)
    exchange_guard = _find_line(
        lines,
        file_suffix="DiamondExchangeService.php",
        contains=("$diamonds %", "$conf['multiple']"),
    )
    facts: List[Dict[str, Any]] = []
    minimum = _config_integer(minimum_line, "min_diamonds")
    multiple = _config_integer(multiple_line, "multiple")
    maximum = _config_integer(maximum_line, "max_diamonds")
    ratio_pair = _test_mode_integer_pair(ratio_line)
    if multiple is not None:
        refs = [multiple_line.ref]
        if exchange_guard is not None:
            refs.append(exchange_guard.ref)
        facts.append(
            {
                "category": "兑换粒度",
                "statement": f"用户钻石兑金币的数量必须是 {multiple:,} 钻石的整数倍。",
                "evidence_refs": refs,
            }
        )
    if minimum is not None:
        refs = [minimum_line.ref]
        if exchange_guard is not None:
            refs.append(exchange_guard.ref)
        facts.append(
            {
                "category": "最低兑换",
                "statement": f"单次钻石兑金币至少需要 {minimum:,} 钻石"
                + (
                    f"，且必须同时满足 {multiple:,} 钻石整数倍"
                    if multiple is not None
                    else ""
                )
                + "。",
                "evidence_refs": refs,
            }
        )
    if ratio_pair is not None:
        test_ratio, production_ratio = ratio_pair
        ratio_copy = (
            f"{test_ratio} 钻石兑换 1 金币"
            if test_ratio == production_ratio
            else f"测试环境 {test_ratio} 钻石兑换 1 金币，正式环境 {production_ratio} 钻石兑换 1 金币"
        )
        facts.append(
            {
                "category": "换算比例",
                "statement": f"当前配置固定为 {ratio_copy}；服务端只在配置有效时执行整除换算。",
                "evidence_refs": [ratio_line.ref],
            }
        )
    if maximum is not None:
        facts.append(
            {
                "category": "单次上限",
                "statement": (
                    "当前配置未设置单次钻石兑金币上限（0 表示不限）。"
                    if maximum == 0
                    else f"单次钻石兑金币上限为 {maximum:,} 钻石。"
                ),
                "evidence_refs": [maximum_line.ref],
            }
        )
    return facts
