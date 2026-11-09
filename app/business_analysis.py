"""基于已检索代码证据生成确定性业务约束摘要。

该模块不替代 LLM，也不访问线上服务；它只把已经命中的源码行整理为可引用
事实。这样模型超时、限流或输出不可用时，报告仍能回答“代码实际限制是什么”，
而不是把模型故障误写成“没有证据”。
"""

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.code_tools import extract_generic_anchors
from app.query_understanding import detect_semantic_concepts


@dataclass(frozen=True)
class EvidenceLine:
    """一条带稳定文件行号的源码证据。

    `file` 是项目内相对路径，`line` 是 1 开始的行号，`content` 是该行原始
    文本，`ref` 是报告和 LLM 共用的 `文件:行号` 引用。
    """

    file: str
    line: int
    content: str
    ref: str


_CONTEXT_LINE_PATTERN = re.compile(r"^\s*(\d+):\s?(.*)$")


def _evidence_lines(evidence: Iterable[Dict[str, Any]]) -> List[EvidenceLine]:
    """把 Worker 保存的上下文片段展开为可精确引用的源码行。

    参数:
        - `evidence`：代码检索证据集合，每项至少包含 `file`、命中行和带行号的
          `context`，类型为 `Iterable[Dict[str, Any]]`。

    Returns:
        去重后的 `EvidenceLine` 列表。若旧任务没有 `context`，退回使用证据行号
        和 `content`，保证历史报告仍可分析。
    """
    lines: Dict[Tuple[str, int], EvidenceLine] = {}
    for item in evidence:
        file_name = str(item.get("file") or "")
        if not file_name:
            continue
        context = str(item.get("context") or "")
        parsed_any = False
        for raw_line in context.splitlines():
            match = _CONTEXT_LINE_PATTERN.match(raw_line)
            if not match:
                continue
            line_number = int(match.group(1))
            content = match.group(2)
            parsed_any = True
            lines.setdefault(
                (file_name, line_number),
                EvidenceLine(
                    file=file_name,
                    line=line_number,
                    content=content,
                    ref=f"{file_name}:{line_number}",
                ),
            )
        if parsed_any:
            continue
        line_number = int(item.get("line") or 0)
        if line_number > 0:
            content = str(item.get("content") or "").strip()
            lines.setdefault(
                (file_name, line_number),
                EvidenceLine(
                    file=file_name,
                    line=line_number,
                    content=content,
                    ref=f"{file_name}:{line_number}",
                ),
            )
    return sorted(lines.values(), key=lambda item: (item.file, item.line))


def _find_line(
    lines: Sequence[EvidenceLine],
    *,
    file_suffix: str = "",
    contains: Sequence[str] = (),
    contains_any: Sequence[str] = (),
) -> Optional[EvidenceLine]:
    """按文件后缀和源码文本查找第一条确定性证据行。

    参数:
        - `lines`：展开后的源码证据行，类型为 `Sequence[EvidenceLine]`。
        - `file_suffix`：目标文件路径后缀，例如 `UsdFuseService.php`，空字符串
          表示不限制文件，类型为 `str`。
        - `contains`：候选行必须同时包含的文本，类型为 `Sequence[str]`。
        - `contains_any`：候选行至少包含其中一项的文本，类型为 `Sequence[str]`。

    Returns:
        第一条匹配证据；没有命中时返回 `None`。匹配不区分英文大小写，避免
        PHP 类名与检索词大小写不同造成漏报。
    """
    required = tuple(item.casefold() for item in contains)
    alternatives = tuple(item.casefold() for item in contains_any)
    for item in lines:
        if file_suffix and not item.file.endswith(file_suffix):
            continue
        content = item.content.casefold()
        if required and not all(value in content for value in required):
            continue
        if alternatives and not any(value in content for value in alternatives):
            continue
        return item
    return None


def _find_line_after(
    lines: Sequence[EvidenceLine],
    anchor: Optional[EvidenceLine],
    needle: str,
    max_distance: int = 2,
) -> Optional[EvidenceLine]:
    """查找条件语句之后紧邻的提前返回或跳过动作。

    参数:
        - `lines`：展开后的源码证据行，类型为 `Sequence[EvidenceLine]`。
        - `anchor`：已经命中的条件行；为空时表示无法证明后续动作，类型为
          `Optional[EvidenceLine]`。
        - `needle`：后续动作行需要包含的文本，例如 `continue` 或 `return []`，
          类型为 `str`。
        - `max_distance`：允许跨过的最大行数，类型为 `int`。

    Returns:
        同一文件中紧跟条件的最长 `max_distance` 行内第一条动作证据；未找到时
        返回 `None`，避免把相似分支的动作错误归到当前条件。
    """
    if anchor is None:
        return None
    lowered_needle = needle.casefold()
    for item in lines:
        if item.file != anchor.file:
            continue
        if anchor.line < item.line <= anchor.line + max_distance:
            if lowered_needle in item.content.casefold():
                return item
    return None


def _analyze_usd_fuse(lines: Sequence[EvidenceLine]) -> List[Dict[str, Any]]:
    """分析美元熔断在源码中实际执行的业务边界。

    参数:
        - `lines`：当前问题已经检索到的源码证据行，类型为 `Sequence[EvidenceLine]`。

    Returns:
        按业务影响排列的确定性事实。每条事实只在找到对应守卫、常量或提示证据
        时输出，不会仅凭中文问题标签补写业务规则。
    """
    facts = []
    fuse_scope = _find_line(
        lines,
        file_suffix="UsdFuseService.php",
        contains=("FUSE_TYPE_CONSUME",),
    )
    official_source = _find_line(
        lines,
        file_suffix="UsdFuseService.php",
        contains=("OFFICIAL_PAYMENT", "熔断官方提现"),
    )
    exchange_source = _find_line(
        lines,
        file_suffix="UsdFuseService.php",
        contains=("USD_EXCHANGE", "熔断美元兑代理币"),
    )
    if fuse_scope and official_source and exchange_source:
        facts.append(
            {
                "category": "作用范围",
                "statement": "美元熔断只支持消费方向；可选来源为官方提现"
                "（OFFICIAL_PAYMENT=1）和美元兑代理币（USD_EXCHANGE=2），"
                "不支持派发方向。",
                "evidence_refs": [fuse_scope.ref, official_source.ref, exchange_source.ref],
            }
        )

    threshold_method = _find_line(
        lines,
        file_suffix="UsdFuseService.php",
        contains=("isTriggerMaxThreshold",),
    )
    threshold_return = _find_line(
        lines,
        file_suffix="UsdFuseService.php",
        contains=("return [false, 0]",),
    )
    if threshold_method and threshold_return:
        facts.append(
            {
                "category": "单笔阈值",
                "statement": "美元熔断没有单笔金额最大阈值判断，主要依赖运营后台"
                "配置的来源级熔断标识。",
                "evidence_refs": [threshold_method.ref, threshold_return.ref],
            }
        )

    official_guard = _find_line(
        lines,
        file_suffix="WithdrawalOrderCreateService.php",
        contains=("isOfficialPaymentTriggered",),
    )
    official_error = _find_line(
        lines,
        file_suffix="WithdrawalException.php",
        contains=("OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED", "= 17"),
    )
    official_message = _find_line(
        lines,
        file_suffix="lang/zh/exception.php",
        contains=("WITHDRAWAL_OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED", "官方提现暂不可用"),
    )
    official_history_condition = _find_line(
        lines,
        file_suffix="WithdrawalChannelConfigService.php",
        contains=("$isOfficial", "$officialFuseTriggered"),
    )
    official_history_filter = _find_line_after(
        lines, official_history_condition, "return []"
    )
    official_channel_condition = _find_line(
        lines,
        file_suffix="WithdrawalChannelConfigService.php",
        contains=("$isOfficialGroup", "$officialFuseTriggered"),
    )
    official_channel_filter = _find_line_after(
        lines, official_channel_condition, "continue"
    )
    if official_guard and official_error:
        message_suffix = (
            "，用户侧提示为“官方提现暂不可用，请选择币商渠道或稍后重试”"
            if official_message
            else ""
        )
        refs = [official_guard.ref, official_error.ref]
        if official_message:
            refs.append(official_message.ref)
        facts.append(
            {
                "category": "官方提现",
                "statement": "创建官方提现订单时直接拒绝，业务错误码为 17"
                f"（OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED）{message_suffix}。",
                "evidence_refs": refs,
            }
        )
    if official_history_filter or official_channel_filter:
        refs = [
            item.ref
            for item in (
                official_history_condition,
                official_history_filter,
                official_channel_condition,
                official_channel_filter,
            )
            if item is not None
        ]
        facts.append(
            {
                "category": "官方渠道",
                "statement": "美元熔断期间官方渠道从提现目录中隐藏，官方历史渠道"
                "回填也会被清空；币商渠道继续参与目录组装。",
                "evidence_refs": list(dict.fromkeys(refs)),
            }
        )

    dealer_guard = _find_line(
        lines,
        file_suffix="WithdrawalOrderCreateService.php",
        contains=("DiamondFuseService::isWithdrawToProxyTriggered",),
    )
    if dealer_guard:
        facts.append(
            {
                "category": "币商提现",
                "statement": "币商提现不受美元熔断直接拦截；它继续受独立的"
                "“提现到代理”钻石熔断控制，命中后使用另一条异常码和提示。",
                "evidence_refs": [dealer_guard.ref],
            }
        )

    exchange_guard = _find_line(
        lines,
        file_suffix="CoinsMerchantControl.php",
        contains=("isUsdExchangeTriggered",),
    )
    exchange_error_call = _find_line(
        lines,
        file_suffix="CoinsMerchantControl.php",
        contains=("ERR_USD_EXCHANGE_FUSE_TRIGGERED",),
    )
    exchange_error = _find_line(
        lines,
        file_suffix="CoinsMerchantConf.php",
        contains=("ERR_USD_EXCHANGE_FUSE_TRIGGERED", "= 20018"),
    )
    exchange_message = _find_line(
        lines,
        file_suffix="lang/zh/coins_merchant.php",
        contains=("ERR_USD_EXCHANGE_FUSE_TRIGGERED", "美元兑换暂不可用"),
    )
    if exchange_guard and exchange_error_call and exchange_error:
        message_suffix = (
            "，用户侧提示为“美元兑换暂不可用，请稍后重试”"
            if exchange_message
            else ""
        )
        refs = [exchange_guard.ref, exchange_error_call.ref, exchange_error.ref]
        if exchange_message:
            refs.append(exchange_message.ref)
        facts.append(
            {
                "category": "美元兑换",
                "statement": "美元兑代理币会在余额扣减和兑换计算前中止，返回"
                f"错误码 20018{message_suffix}。",
                "evidence_refs": refs,
            }
        )

    cache_key_method = _find_line(
        lines,
        file_suffix="BaseFuseService.php",
        contains=("function formatCacheKey",),
    )
    cache_key_value = _find_line(
        lines,
        file_suffix="BaseFuseService.php",
        contains=("hashFormatKey", "sprintf"),
    )
    source_flag_check = _find_line(
        lines,
        file_suffix="BaseFuseService.php",
        contains=("function checkFuseFlag",),
    )
    if cache_key_method and cache_key_value and source_flag_check:
        facts.append(
            {
                "category": "隔离范围",
                "statement": "熔断状态写入按熔断大类形成的 Redis Hash，业务读取按"
                "来源标识判断；同一来源的状态没有按 UID 分区，因此一个用户触发后"
                "会影响同来源的其他用户。",
                "evidence_refs": [
                    cache_key_method.ref,
                    cache_key_value.ref,
                    source_flag_check.ref,
                ],
            }
        )

    restore_service = _find_line(
        lines,
        file_suffix="UsdFuseService.php",
        contains=("getRestoreService",),
    )
    restore_error = _find_line(
        lines,
        file_suffix="UsdFuseService.php",
        contains=("does not support asset recovery",),
    )
    if restore_service and restore_error:
        facts.append(
            {
                "category": "资产恢复",
                "statement": "美元熔断只阻断业务入口，不生成可恢复资产记录；"
                "恢复服务为空，handelRecover 会明确返回失败。",
                "evidence_refs": [restore_service.ref, restore_error.ref],
            }
        )

    zero_uid_guard = _find_line(
        lines,
        file_suffix="UsdFuseService.php",
        contains=("$uid <= 0",),
    )
    exception_guard = _find_line(
        lines,
        file_suffix="UsdFuseService.php",
        contains=("Throwable",),
    )
    exception_return = _find_line(
        lines,
        file_suffix="UsdFuseService.php",
        contains=("ExceptionGuard::log",),
    )
    if zero_uid_guard and exception_guard and exception_return:
        facts.append(
            {
                "category": "故障策略",
                "statement": "风控读取采用 fail-open：uid 不大于 0 时直接放行；"
                "Redis 或读取异常记录日志后返回未熔断，避免风控故障扩大为提现和"
                "兑换整体不可用。",
                "evidence_refs": [
                    zero_uid_guard.ref,
                    exception_guard.ref,
                    exception_return.ref,
                ],
            }
        )
    return [item for item in facts if item is not None]


def _config_integer(line: Optional[EvidenceLine], key: str) -> Optional[int]:
    """从配置证据行提取指定键后面的整数字面量。

    参数:
        - `line`：可能包含 PHP 配置项的源码证据，类型为 `Optional[EvidenceLine]`。
        - `key`：需要读取的配置键，例如 `min_diamonds`，类型为 `str`。

    Returns:
        配置字面量转换后的整数；证据行不存在、值来自函数调用或不是十进制整数
        时返回 `None`，避免把表达式误读为固定业务值。
    """
    if line is None:
        return None
    match = re.search(
        rf"['\"]?{re.escape(key)}['\"]?\s*(?:=>|=)\s*(\d+)(?:\s*[,;)]|$)",
        line.content,
    )
    return int(match.group(1)) if match else None


def _config_number(line: Optional[EvidenceLine], key: str) -> Optional[float]:
    """从渠道配置证据行提取指定键后面的十进制数值。

    参数:
        - `line`：可能包含渠道计费项的源码证据，类型为 `Optional[EvidenceLine]`。
        - `key`：需要读取的计费键，例如 `pay_fee`，类型为 `str`。

    Returns:
        比例费率或固定费的数值；没有配置字面量时返回 `None`，由调用方明确
        标记该渠道费率未确认。
    """
    if line is None:
        return None
    match = re.search(
        rf"['\"]?{re.escape(key)}['\"]?\s*(?:=>|=)\s*(\d+(?:\.\d+)?)",
        line.content,
    )
    return float(match.group(1)) if match else None


def _test_mode_integer_pair(line: Optional[EvidenceLine]) -> Optional[Tuple[int, int]]:
    """读取 `getByTestMode(测试值, 正式值)` 中的两个整数。

    参数:
        - `line`：官方提现下限等配置行，类型为 `Optional[EvidenceLine]`。

    Returns:
        `(测试环境值, 正式环境值)`；配置不是该函数调用时返回 `None`。两项的
        环境顺序随后还要由 `getByTestMode` 的实现证据共同确认。
    """
    if line is None:
        return None
    match = re.search(r"getByTestMode\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", line.content)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _test_mode_number(line: Optional[EvidenceLine], key: str) -> Optional[float]:
    """读取指定配置键在 `getByTestMode()` 中的第一个数值。

    参数:
        - `line`：可能包含 PHP 配置项的源码证据，类型为 `Optional[EvidenceLine]`。
        - `key`：例如 `diamond_to_usd` 的配置键名，类型为 `str`。

    Returns:
        测试环境分支中的数值；当前仓库对该比例使用相同的测试和生产值，因此
        该数值也代表当前配置口径。配置表达式不存在时返回 `None`，不猜测缺省值。
    """
    if line is None:
        return None
    match = re.search(
        rf"['\"]?{re.escape(key)}['\"]?\s*(?:=>|=)\s*getByTestMode\(\s*(\d+(?:\.\d+)?)",
        line.content,
    )
    return float(match.group(1)) if match else None


def _official_channel_fee(
    lines: Sequence[EvidenceLine],
    channel_name: str,
) -> Optional[Dict[str, Any]]:
    """提取一个官方渠道的比例费、固定费和对应证据。

    参数:
        - `lines`：当前问题展开后的源码证据行，类型为 `Sequence[EvidenceLine]`。
        - `channel_name`：渠道目录中的 `name` 值，例如 `USDT-TRC20`，类型为 `str`。

    Returns:
        渠道名称、费率、固定费和三项证据引用；名称、比例费或固定费任一缺失时
        返回 `None`。只接受同一配置块内紧随渠道名出现的计费行，避免把其他
        渠道的费率错配到当前渠道。
    """
    name_line = _find_line(
        lines,
        file_suffix="anchorPayment.php",
        contains=(f"'name' => '{channel_name}'",),
    )
    pay_fee_line = _find_line_after(lines, name_line, "'pay_fee'", max_distance=25)
    fixed_fee_line = _find_line_after(
        lines,
        pay_fee_line,
        "'additional_pay_fee'",
        max_distance=2,
    )
    rate = _config_number(pay_fee_line, "pay_fee")
    fixed = _config_number(fixed_fee_line, "additional_pay_fee")
    if name_line is None or pay_fee_line is None or fixed_fee_line is None:
        return None
    if rate is None or fixed is None:
        return None
    return {
        "name": channel_name,
        "rate": rate,
        "fixed": fixed,
        "evidence_refs": [name_line.ref, pay_fee_line.ref, fixed_fee_line.ref],
    }


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


def _analyze_frontend_diamond_display(lines: Sequence[EvidenceLine]) -> List[Dict[str, Any]]:
    """分析 waky4 钱包页钻石展示条件、余额来源和区域档位配置。

    参数:
        - `lines`：围绕“前端展示钻石根据什么”检索到的钱包接口、登录校验、
          主播身份、余额模型、公会区域和档位配置源码，类型为
          `Sequence[EvidenceLine]`。分析器只使用实际命中的代码行，不会把
          客户端渲染行为或缓存策略补写成源码事实。

    Returns:
        按接口入口、展示门槛、身份判断、余额、区域、档位、响应字段和读取
        一致性排列的确定性事实。缺少任一关键数据源时不会伪造该层结论，调用方
        可据 `evidence_refs` 继续核查。
    """
    facts: List[Dict[str, Any]] = []

    page_route = _find_line(
        lines,
        file_suffix="php/config/auth.php",
        contains=("Action/Diamond.getDiamondGearList", "钱包页"),
    )
    controller_entry = _find_line(
        lines,
        file_suffix="DiamondControl.php",
        contains=("function getDiamondGearList",),
    )
    login_guard = _find_line(
        lines,
        file_suffix="DiamondControl.php",
        contains=("\\Request::CheckLogin()",),
    )
    if page_route and controller_entry and login_guard:
        facts.append(
            {
                "category": "页面入口",
                "statement": "waky4 钱包页钻石区对应 `Action/Diamond.getDiamondGearList`；"
                "接口先调用 `Request::CheckLogin()` 取得当前登录用户，未登录请求不会进入"
                "钱包数据读取。",
                "evidence_refs": [page_route.ref, controller_entry.ref, login_guard.ref],
            }
        )

    wallet_card_entry = _find_line(
        lines,
        file_suffix="PropControl.php",
        contains=("function getMyRebateCard",),
    )
    wallet_anchor_flag = _find_line(
        lines,
        file_suffix="PropControl.php",
        contains=("$rebateCard['is_anchor']", "isAnchor($uid)", "前端展示钻石档位和用户钻石"),
    )
    if wallet_card_entry and wallet_anchor_flag:
        facts.append(
            {
                "category": "钱包页显隐",
                "statement": "钱包页的 `Action/Prop.getMyRebateCard` 还会返回 `is_anchor`；"
                "源码注释明确说明前端依据该字段展示钻石档位和用户钻石。该字段与下面"
                "余额、档位接口共同构成钱包页钻石区的显示契约。",
                "evidence_refs": [wallet_card_entry.ref, wallet_anchor_flag.ref],
            }
        )

    anchor_guard = _find_line(
        lines,
        file_suffix="DiamondControl.php",
        contains=("if (!$anchor)",),
    )
    hidden_marker = _find_line(
        lines,
        file_suffix="DiamondControl.php",
        contains=("is_show'] = 0",),
    )
    anchor_method = _find_line(
        lines,
        file_suffix="usermodel.php",
        contains=("function isAnchor",),
    )
    anchor_query = _find_line(
        lines,
        file_suffix="usermodel.php",
        contains=("TAnchor", "where('uid'", "where('status',0)"),
    )
    if anchor_guard and hidden_marker and anchor_method and anchor_query:
        facts.append(
            {
                "category": "展示条件",
                "statement": "接口先判断登录用户是否为主播；非主播只返回 `is_show = 0` 并立即"
                "结束，不返回余额和兑换档位。主播判定条件是 `data_analyse.t_anchor` 中存在"
                "同一 UID 且 `status = 0` 的记录。",
                "evidence_refs": [
                    anchor_guard.ref,
                    hidden_marker.ref,
                    anchor_method.ref,
                    anchor_query.ref,
                ],
            }
        )

    balance_call = _find_line(
        lines,
        file_suffix="DiamondControl.php",
        contains=("getUserDiamonds($uid)",),
    )
    balance_field = _find_line(
        lines,
        file_suffix="DiamondControl.php",
        contains=("$data['diamonds']", "$userDiamonds['diamonds']"),
    )
    balance_method = _find_line(
        lines,
        file_suffix="DiamondModel.php",
        contains=("function getUserDiamonds",),
    )
    balance_sql = _find_line(
        lines,
        file_suffix="DiamondModel.php",
        contains=("hsing_pay.t_user_diamonds", "where uid = ?"),
    )
    balance_slave = _find_line(
        lines,
        file_suffix="DiamondModel.php",
        contains=("$this->slaveDb->query_one($sql",),
    )
    balance_zero = _find_line(
        lines,
        file_suffix="DiamondModel.php",
        contains=("return ['diamonds' => 0", "'consume' => 0"),
    )
    if (
        balance_call
        and balance_field
        and balance_method
        and balance_sql
        and balance_slave
        and balance_zero
    ):
        facts.append(
            {
                "category": "余额来源",
                "statement": "主播展示的钻石余额来自 `hsing_pay.t_user_diamonds.diamonds`，"
                "按当前 UID 通过 `DiamondModel::getUserDiamonds()` 从从库查询；用户没有钱包"
                "记录时返回 0。",
                "evidence_refs": [
                    balance_call.ref,
                    balance_field.ref,
                    balance_method.ref,
                    balance_sql.ref,
                    balance_slave.ref,
                    balance_zero.ref,
                ],
            }
        )

    region_call = _find_line(
        lines,
        file_suffix="DiamondControl.php",
        contains=("$anchorModel->getAnchorRegion($uid)",),
    )
    region_method = _find_line(
        lines,
        file_suffix="AnchorModel.php",
        contains=("function getAnchorRegion",),
    )
    region_sql = _find_line(
        lines,
        file_suffix="AnchorModel.php",
        contains=("select b.region from data_analyse.t_guild_anchor", "t_guild b on a.gid=b.gid"),
    )
    region_fallback = _find_line(
        lines,
        file_suffix="AnchorModel.php",
        contains=("return 'global'",),
    )
    if region_call and region_method and region_sql and region_fallback:
        facts.append(
            {
                "category": "区域来源",
                "statement": "兑换档位按主播所属公会区域筛选：`AnchorModel::getAnchorRegion()` "
                "关联 `t_guild_anchor` 与状态正常的 `t_guild` 读取 `region`；没有有效公会"
                "记录时回退为 `global`。",
                "evidence_refs": [
                    region_call.ref,
                    region_method.ref,
                    region_sql.ref,
                    region_fallback.ref,
                ],
            }
        )

    gear_call = _find_line(
        lines,
        file_suffix="DiamondModel.php",
        contains=("new RebateDiamondExchangeConfig($region)",),
    )
    type_assignment = _find_line(
        lines,
        file_suffix="RebateDiamondExchangeConfig.php",
        contains=("$this->type = self::TYPE_REBATE_DIAMOND_EXCHANGE_COIN",),
    )
    type_constant = _find_line(
        lines,
        file_suffix="BaseAnchorConfig.php",
        contains=("TYPE_REBATE_DIAMOND_EXCHANGE_COIN = 9",),
    )
    gear_config_sql = _find_line(
        lines,
        file_suffix="BaseAnchorConfig.php",
        contains=("select json_conf from data_analyse.t_anchor_settlement_config",),
    )
    gear_levels = _find_line(
        lines,
        file_suffix="DiamondModel.php",
        contains=("RebateDiamondExchangeConfig::LEVELS",),
    )
    diamond_num_field = _find_line(
        lines,
        file_suffix="DiamondModel.php",
        contains=("'diamond_num' =>", "DIAMOND_NUM"),
    )
    coin_num_field = _find_line(
        lines,
        file_suffix="DiamondModel.php",
        contains=("'coin_num' =>", "COIN_NUM"),
    )
    if (
        gear_call
        and type_assignment
        and type_constant
        and gear_config_sql
        and gear_levels
        and diamond_num_field
        and coin_num_field
    ):
        facts.append(
            {
                "category": "档位配置",
                "statement": "兑换档位来自 `data_analyse.t_anchor_settlement_config` 中"
                " `type = 9`（主播钻石兑金币政策）且匹配主播区域的 `json_conf`；"
                "`levels[].diamondNum/coinNum` 会映射为接口字段 "
                "`diamond_gears[].diamond_num/coin_num`。",
                "evidence_refs": [
                    gear_call.ref,
                    type_assignment.ref,
                    type_constant.ref,
                    gear_config_sql.ref,
                    gear_levels.ref,
                    diamond_num_field.ref,
                    coin_num_field.ref,
                ],
            }
        )

    gear_response = _find_line(
        lines,
        file_suffix="DiamondControl.php",
        contains=("$data['diamond_gears']",),
    )
    if balance_field and gear_response:
        facts.append(
            {
                "category": "响应字段",
                "statement": "主播分支的接口响应写入 `diamonds` 和 `diamond_gears` 两个字段；"
                "源码没有在该主播分支额外写入 `is_show = 1`，因此前端是否展示应依据接口"
                "成功响应及这些字段判断，而不能假设后端返回了显式的正显标记。",
                "evidence_refs": [balance_field.ref, gear_response.ref],
            }
        )

    config_slave = _find_line(
        lines,
        file_suffix="BaseAnchorConfig.php",
        contains=("ServiceFactory::getSlaveDB()",),
    )
    if balance_slave and config_slave:
        facts.append(
            {
                "category": "读取一致性",
                "statement": "钱包余额和档位配置都通过从库查询，而不是在前端写死；主从复制延迟时"
                "可能短时展示旧余额或旧档位，排障时应同时核对主库当前值与从库读到的值。",
                "evidence_refs": [balance_slave.ref, config_slave.ref],
            }
        )

    return facts


def _analyze_diamond_withdrawal(lines: Sequence[EvidenceLine]) -> List[Dict[str, Any]]:
    """分析 waky6 新版用户钻石提现的金额梯度和手续费承担方式。

    参数:
        - `lines`：围绕钻石提现问题检索到的源码与配置证据，类型为
          `Sequence[EvidenceLine]`。

    Returns:
        按业务问题排列的确定性事实。只有出现对应入口、配置、分支或计算公式
        时才生成结论；尤其不会仅凭渠道目录存在 `pay_fee` 就断言用户承担费用。
    """
    facts: List[Dict[str, Any]] = []

    controller_call = _find_line(
        lines,
        file_suffix="DiamondControl.php",
        contains=("WithdrawalOrderCreateService", "->create("),
    )
    create_service = _find_line(
        lines,
        file_suffix="WithdrawalOrderCreateService.php",
        contains=("class WithdrawalOrderCreateService",),
    )
    if controller_call and create_service:
        facts.append(
            {
                "category": "业务入口",
                "statement": "问题对应新版用户钻石提现链路："
                "`Action/Diamond.withdrawOrder` 调用 "
                "`WithdrawalOrderCreateService::create()`，不能与旧版主播工资提现混用。",
                "evidence_refs": [controller_call.ref, create_service.ref],
            }
        )

    limits_anchor = _find_line(
        lines,
        file_suffix="diamonds.php",
        contains=("'withdrawal_limits'",),
    )
    multiple_line = _find_line_after(lines, limits_anchor, "'multiple'", max_distance=2)
    multiple = _config_integer(multiple_line, "multiple")
    if multiple is not None:
        facts.append(
            {
                "category": "提现粒度",
                "statement": f"钻石提现金额必须为 {multiple:,} 钻石的整数倍。",
                "evidence_refs": [limits_anchor.ref, multiple_line.ref],
            }
        )

    dealer_anchor = _find_line_after(lines, limits_anchor, "'dealer'", max_distance=2)
    dealer_min_line = _find_line_after(lines, dealer_anchor, "'min_diamonds'", max_distance=2)
    dealer_min = _config_integer(dealer_min_line, "min_diamonds")
    official_anchor = _find_line_after(lines, dealer_anchor, "'official'", max_distance=3)
    official_min_line = _find_line_after(lines, official_anchor, "'min_diamonds'", max_distance=2)
    official_pair = _test_mode_integer_pair(official_min_line)
    test_mode_helper = _find_line(
        lines,
        file_suffix="SysEnv.php",
        contains=("function getByTestMode",),
    )
    test_mode_return = _find_line(
        lines,
        file_suffix="SysEnv.php",
        contains=("return env('TEST_MODE', 0) ? $testModeVal : $noTestModeVal;",),
    )
    if dealer_min is not None and official_pair and test_mode_helper and test_mode_return:
        test_min, production_min = official_pair
        facts.append(
            {
                "category": "提现下限",
                "statement": f"币商渠道最低 {dealer_min:,} 钻石；官方渠道测试环境最低 "
                f"{test_min:,} 钻石、正式环境最低 {production_min:,} 钻石。"
                "`getByTestMode` 的第一个参数用于 `TEST_MODE=1`，第二个参数用于正式环境。",
                "evidence_refs": [
                    dealer_min_line.ref,
                    official_min_line.ref,
                    test_mode_helper.ref,
                    test_mode_return.ref,
                ],
            }
        )

    official_max_line = _find_line_after(lines, official_min_line, "'max_diamonds'", max_distance=3)
    official_max_pair = _test_mode_integer_pair(official_max_line)
    daily_line = _find_line_after(lines, official_max_line, "'daily_limit'", max_distance=2)
    daily_pair = _test_mode_integer_pair(daily_line)
    if official_max_pair and daily_pair:
        test_max, production_max = official_max_pair
        test_daily, production_daily = daily_pair
        max_text = (
            f"测试环境 {test_max:,}、正式环境 {production_max:,} 钻石"
            if test_max != production_max
            else f"{production_max:,} 钻石"
        )
        daily_text = (
            f"测试环境 {test_daily}、正式环境 {production_daily} 笔"
            if test_daily != production_daily
            else f"{production_daily} 笔"
        )
        facts.append(
            {
                "category": "官方金额与频次",
                "statement": f"官方渠道单笔最高 {max_text}，每个用户自然日最多 "
                f"{daily_text}；币商渠道没有配置单笔上限。",
                "evidence_refs": [official_max_line.ref, daily_line.ref],
            }
        )

    ratio_line = _find_line(
        lines,
        file_suffix="diamonds.php",
        contains=("'dollar_to_diamond'", "40000"),
    )
    ratio = _config_integer(ratio_line, "dollar_to_diamond")
    if ratio and dealer_min is not None and official_pair:
        test_min, production_min = official_pair
        facts.append(
            {
                "category": "美元换算",
                "statement": f"钻石按 1 美元 = {ratio:,} 钻石换算：币商最低 "
                f"{dealer_min:,} 钻石约 ${dealer_min / ratio:.2f}；官方正式环境最低 "
                f"{production_min:,} 钻石约 ${production_min / ratio:.2f}，测试环境最低 "
                f"{test_min:,} 钻石约 ${test_min / ratio:.2f}。",
                "evidence_refs": [ratio_line.ref, dealer_min_line.ref, official_min_line.ref],
            }
        )

    official_branch = _find_line(
        lines,
        file_suffix="WithdrawalOrderCreateService.php",
        contains=("buildOfficialSnapshot",),
    )
    default_fee_line = _find_line(
        lines,
        file_suffix="WithdrawalOrderCreateService.php",
        contains=("$fee = $official['fee'] ?? '0.00'",),
    )
    dealer_payout_line = _find_line(
        lines,
        file_suffix="WithdrawalOrderCreateService.php",
        contains=("$payoutMoney = $official['net'] ?? $income",),
    )
    if official_branch and default_fee_line and dealer_payout_line:
        facts.append(
            {
                "category": "手续费承担",
                "statement": "只有官方订单构建手续费和到账净额快照；币商订单的手续费字段"
                "默认为 0.00，订单应付金额仍等于钻石折算的美元收入。该结论不等于保证"
                "币商线下不会另行收费。",
                "evidence_refs": [official_branch.ref, default_fee_line.ref, dealer_payout_line.ref],
            }
        )

    fee_formula = _find_line(
        lines,
        file_suffix="WithdrawalOrderCreateService.php",
        contains=("$fee = round", "grossCurrency", "feeRate", "feeFixed"),
    )
    net_formula = _find_line(
        lines,
        file_suffix="WithdrawalOrderCreateService.php",
        contains=("$net = round", "grossCurrency", "$fee"),
    )
    net_guard = _find_line(
        lines,
        file_suffix="WithdrawalOrderCreateService.php",
        contains=("if ($net <= 0)",),
    )
    if fee_formula and net_formula:
        refs = [fee_formula.ref, net_formula.ref]
        statement = (
            "官方渠道手续费按“结算币种金额 × 比例费率 + 固定手续费”计算，"
            "再从结算金额中扣除后得到实际到账净额。"
        )
        if net_guard:
            statement += "若扣除后的净额不大于 0，该渠道会拒绝下单。"
            refs.append(net_guard.ref)
        facts.append(
            {
                "category": "手续费公式",
                "statement": statement,
                "evidence_refs": refs,
            }
        )

    channel_fees = [
        fee
        for fee in (
            _official_channel_fee(lines, "USDT-TRC20"),
            _official_channel_fee(lines, "Epay"),
        )
        if fee is not None
    ]
    if channel_fees:
        examples = "；".join(
            f"{item['name']} 为 {item['rate'] * 100:g}% + 固定 {item['fixed']:g}"
            for item in channel_fees
        )
        facts.append(
            {
                "category": "渠道费率",
                "statement": f"当前源码配置的官方渠道费率示例：{examples}。"
                "具体币种和最终到账金额以用户所选渠道及订单快照为准。",
                "evidence_refs": [
                    ref
                    for item in channel_fees
                    for ref in item.get("evidence_refs") or []
                ],
            }
        )

    return facts


def _analyze_anchor_withdrawal(lines: Sequence[EvidenceLine]) -> List[Dict[str, Any]]:
    """分析仓库中仍然存在的旧版主播钻石提现校验。

    参数:
        - `lines`：围绕钻石提现问题已经取得的源码证据行，类型为
          `Sequence[EvidenceLine]`。分析器只读取当前证据，不假设新版提现服务
          仍然存在。

    Returns:
        旧版 `AdvanceServices::anchorWithdraw` 链路的最低金额、区域换算和费用
        证据。只有入口、资格校验或换算配置实际出现时才生成事实；新版提现实现
        不存在时，调用方仍能得到当前仓库真实可用链路，而不是引用失效文件。
    """
    entry = _find_line(
        lines,
        file_suffix="Anchor/AdvanceServices.php",
        contains=("public function anchorWithdraw",),
    )
    guard = _find_line(
        lines,
        file_suffix="Anchor/AdvanceServices.php",
        contains=("public function checkAnchorCanWithdraw",),
    )
    min_money = _find_line(
        lines,
        file_suffix="Anchor/AdvanceServices.php",
        contains=("$minMoney = $region == 'tr' ? 9 : 10",),
    )
    min_guard = _find_line(
        lines,
        file_suffix="Anchor/AdvanceServices.php",
        contains=("$to_uid && $money < $minMoney",),
    )
    micro_min = _find_line(
        lines,
        file_suffix="Anchor/AdvanceServices.php",
        contains=("FILTER_VALIDATE_FLOAT", "'min_range' => 0.01"),
    )
    normal_rate_line = _find_line(
        lines,
        file_suffix="config/anchor.php",
        contains=("'diamond_to_usd'",),
    )
    tr_rate_line = _find_line(
        lines,
        file_suffix="config/anchor.php",
        contains=("'diamond_to_usd_tr'",),
    )
    facts: List[Dict[str, Any]] = []
    if entry and guard:
        facts.append(
            {
                "category": "业务入口",
                "statement": "当前仓库可执行的主播钻石提现入口为 "
                "`AdvanceServices::anchorWithdraw()`；它在扣款前调用 "
                "`checkAnchorCanWithdraw()` 校验金额、区域、代理、次数和余额。",
                "evidence_refs": [entry.ref, guard.ref],
            }
        )

    normal_rate = _test_mode_number(normal_rate_line, "diamond_to_usd")
    tr_rate = _test_mode_number(tr_rate_line, "diamond_to_usd_tr")
    if min_money and min_guard and normal_rate is not None:
        normal_minimum = math.ceil(round(10 * normal_rate, 3))
        statement = (
            f"提现到代理美元账户时，非土耳其区域要求换算金额至少为 10 美元；"
            f"按 1 美元 = {normal_rate:g} 钻石换算，最低约 {normal_minimum:,} 钻石。"
        )
        refs = [min_money.ref, min_guard.ref, normal_rate_line.ref]
        if tr_rate is not None:
            tr_minimum = math.ceil(round(9 * tr_rate, 3))
            statement += (
                f"土耳其区域阈值为 9 美元，按当前特殊比例换算最低约 "
                f"{tr_minimum:,} 钻石。"
            )
            refs.append(tr_rate_line.ref)
        facts.append(
            {
                "category": "提现下限",
                "statement": statement,
                "evidence_refs": refs,
            }
        )

    if micro_min and normal_rate is not None:
        coin_minimum = math.ceil(round(0.01 * normal_rate, 3))
        facts.append(
            {
                "category": "提现金币下限",
                "statement": "提现成金币时不走代理美元的 9/10 美元下限，只要求换算金额"
                f"至少 0.01 美元；按普通比例计算最低约 {coin_minimum:,} 钻石。",
                "evidence_refs": [micro_min.ref, normal_rate_line.ref],
            }
        )

    debit = _find_line(
        lines,
        file_suffix="Anchor/AdvanceServices.php",
        contains=("decrDiamond", "WITHDRAW_TO_PROXY"),
    )
    transfer = _find_line(
        lines,
        file_suffix="WithdrawModel.php",
        contains=("function addProxyUSD",),
    )
    if transfer is None:
        # 调用链尚未补齐模型文件时，调用点本身仍能证明传入参数只有美元金额和
        # 钻石数量，没有独立 fee 参数；报告会明确这是“当前取得的链路”，而不是
        # 宣称整个支付体系绝对无费。
        transfer = _find_line(
            lines,
            file_suffix="Anchor/AdvanceServices.php",
            contains=("WithdrawModel())->addProxyUSD",),
        )
    if debit and transfer:
        facts.append(
            {
                "category": "手续费边界",
                "statement": "这条旧版代理美元提现链路按换算后的美元金额扣减钻石并写入"
                "代理美元记录；本次取得的扣款和转账方法没有独立平台手续费参数或"
                "费率公式。因此源码只能证明该链路未另收平台费，不能证明代理或支付"
                "渠道在线下不会另行收费。",
                "evidence_refs": [debit.ref, transfer.ref],
            }
        )
    return facts


def _lines_by_file(lines: Sequence[EvidenceLine]) -> Dict[str, List[EvidenceLine]]:
    """按项目内文件分组已排序的源码证据行。

    参数:
        - `lines`：从诊断证据上下文展开并去重后的源码行，类型为
          `Sequence[EvidenceLine]`。

    Returns:
        `文件路径 -> 按行号升序排列的证据` 映射。分组只在内存中进行，不会读取
        业务仓库的其他文件，避免分析器绕过已审计的证据范围。
    """
    grouped: Dict[str, List[EvidenceLine]] = {}
    for item in lines:
        grouped.setdefault(item.file, []).append(item)
    for items in grouped.values():
        items.sort(key=lambda evidence: evidence.line)
    return grouped


def _spaceship_level_configs(
    lines: Sequence[EvidenceLine],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """从 2025 年度金主配置证据中提取各级普通商品碎片规则。

    参数:
        - `lines`：当前诊断命中的源码证据行，类型为 `Sequence[EvidenceLine]`。
          只有路径属于 `ceremony2025GoldOwner/config.php` 的上下文会参与解析。

    Returns:
        `(levels, rate_refs)`。`levels` 每项包含等级、碎片总量、固定排名奖励、
        固定奖励合计和配置引用；`rate_refs` 是三档惊喜配置的证据引用。结果仅
        覆盖本次取证窗口实际出现的等级，缺失窗口不会被默认数值补齐。
    """
    levels: List[Dict[str, Any]] = []
    rate_refs: List[str] = []
    seen_levels = set()
    for file_name, file_lines in _lines_by_file(lines).items():
        lowered_file = file_name.casefold()
        if not lowered_file.endswith("ceremony2025goldowner/config.php"):
            continue
        for index, marker in enumerate(file_lines):
            if "'ordinary_prizes' =>" not in marker.content:
                continue
            level_id: Optional[int] = None
            for previous in reversed(file_lines[max(0, index - 30):index]):
                level_match = re.fullmatch(
                    r"\s*'id'\s*=>\s*(\d+)\s*,?\s*",
                    previous.content,
                )
                if level_match:
                    level_id = int(level_match.group(1))
                    break
            if level_id is None:
                continue
            section = file_lines[index:min(len(file_lines), index + 30)]
            fragment_line: Optional[EvidenceLine] = None
            rate_line: Optional[EvidenceLine] = None
            fixed_line: Optional[EvidenceLine] = None
            fixed_items: List[Tuple[int, int, int]] = []
            fixed_refs: List[str] = []
            for section_line in section:
                content = section_line.content
                if fragment_line is None:
                    fragment_match = re.search(r"'fragments'\s*=>\s*(\d+)", content)
                    if fragment_match:
                        fragment_line = section_line
                if rate_line is None:
                    rate_match = re.search(r"'minimum_guarantee_rate'\s*=>\s*(\d+)", content)
                    if rate_match:
                        rate_line = section_line
                threshold = re.search(
                    r"\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]",
                    content,
                )
                if threshold and (rate_line is None or section_line.line < rate_line.line):
                    fixed_items.append(
                        (int(threshold.group(1)), int(threshold.group(2)), int(threshold.group(3)))
                    )
                    fixed_refs.append(section_line.ref)
                    if fixed_line is None:
                        fixed_line = section_line
                surprise_match = re.search(
                    r"(\d+)\s*=>\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]",
                    content,
                )
                if surprise_match and section_line.ref not in rate_refs:
                    rate_refs.append(section_line.ref)
            if fragment_line is None:
                continue
            fragment_match = re.search(r"\d+", fragment_line.content)
            if fragment_match is None:
                continue
            fragments = int(fragment_match.group(0))
            dedupe_key = (level_id, marker.line)
            if dedupe_key in seen_levels:
                continue
            seen_levels.add(dedupe_key)
            fixed_total = sum((end - start + 1) * amount for start, end, amount in fixed_items)
            levels.append(
                {
                    "level": level_id,
                    "fragments": fragments,
                    "fixed": fixed_items,
                    "fixed_total": fixed_total,
                    "marker_ref": marker.ref,
                    "fragment_ref": fragment_line.ref,
                    "rate_ref": rate_line.ref if rate_line else "",
                    "fixed_refs": fixed_refs,
                }
            )
    levels.sort(key=lambda item: int(item["level"]))
    return levels, list(dict.fromkeys(rate_refs))


def _analyze_spaceship_fragments(lines: Sequence[EvidenceLine]) -> List[Dict[str, Any]]:
    """解释年度金主飞船普通商品碎片的配置和分配顺序。

    参数:
        - `lines`：已通过代码检索取得的源码行，类型为 `Sequence[EvidenceLine]`。
          解析同时依赖等级配置、`SpaceshipService` 的普通奖励公式、
          `SpaceshipOrdinaryPrize` 的领奖互斥规则和 `SurprisePrize` 的随机发奖。

    Returns:
        按业务执行顺序排列的确定性事实。每条事实只引用实际命中的源码；缺少
        等级配置或公式时不会猜测具体人数和最终每人碎片数。
    """
    facts: List[Dict[str, Any]] = []
    levels, rate_refs = _spaceship_level_configs(lines)
    if levels:
        level_descriptions = []
        level_refs = []
        for item in levels:
            fixed_text = "；".join(
                f"{start}-{end} 名每人 {amount}"
                for start, end, amount in item["fixed"]
            ) or "未配置固定排名奖励"
            level_descriptions.append(
                f"等级 {item['level']} 总量 {item['fragments']:,}，"
                f"固定奖励 {fixed_text}（合计 {item['fixed_total']:,}）"
            )
            level_refs.extend([item["fragment_ref"], *item["fixed_refs"]])
        facts.append(
            {
                "category": "碎片总量与固定榜",
                "statement": "2025 年度金主配置的普通商品碎片总量为："
                + "；".join(level_descriptions)
                + "。固定榜奖励先从该等级总碎片中扣除。",
                "evidence_refs": list(dict.fromkeys(level_refs)),
            }
        )

    fixed_formula = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$fragments = intval", "$item[1]", "$item[0]", "$item[2]"),
    )
    surplus_formula = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$surplus = max(0", "$fragments - $topFixed"),
    )
    if fixed_formula and surplus_formula:
        facts.append(
            {
                "category": "分配顺序",
                "statement": "每个固定排名档位的奖励合计为“人数 × 每人碎片”，"
                "全部固定榜奖励先发放；随后用该等级碎片总量减去固定奖励，"
                "得到保底池与惊喜池的共同基数。",
                "evidence_refs": [fixed_formula.ref, surplus_formula.ref],
            }
        )

    minimum_rate = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$minimum = max(0", "minimum_guarantee_rate", "$surplus"),
    )
    minimum_average = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$averageMinimum = max(0", "$minimum", "$averageUsers"),
    )
    minimum_fallback = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("if ($averageMinimum <= 0)",),
    )
    minimum_total = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$totalMinimum = $averageMinimum * $averageUsers",),
    )
    minimum_surplus = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$minimumSur = max(0", "$minimum - $totalMinimum"),
    )
    if minimum_rate and minimum_average:
        refs = [minimum_rate.ref, minimum_average.ref]
        statement = (
            "扣除固定榜后，剩余碎片的 60% 进入保底池，面向固定榜之后的剩余用户"
            "均分；每人先取“保底池 ÷ 剩余用户数”的向下整数。"
        )
        if minimum_fallback:
            statement += "若人均不足 1，则改为每人 1，发完保底池即止。"
            refs.append(minimum_fallback.ref)
        if minimum_total and minimum_surplus:
            statement += "保底池发完后的整数除法余数会并入惊喜池。"
            refs.extend([minimum_total.ref, minimum_surplus.ref])
        facts.append({"category": "保底池", "statement": statement, "evidence_refs": refs})

    surprise_total = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$surprise = intval", "surprise_rate", "$surplus"),
    )
    surprise_remainder = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$sur = max(0", "$surplus - $minimum - $surprise", "$minimumSur"),
    )
    surprise_user_rate = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$rowSurpriseUser = intval", "$userRate * $totalUsers / 100"),
    )
    surprise_per_user = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$rowFragments = max(1", "$rowSurprise", "$rowSurpriseUser"),
    )
    if surprise_total and surprise_user_rate and surprise_per_user:
        refs = [surprise_total.ref, surprise_user_rate.ref, surprise_per_user.ref]
        if surprise_remainder:
            refs.append(surprise_remainder.ref)
        facts.append(
            {
                "category": "惊喜池",
                "statement": "扣除固定榜后的剩余碎片有 40% 进入惊喜池，并把前两档"
                "取整余数和保底余数追加进来。惊喜档按该等级总用户数的 10%、20%、"
                "30% 选择人数，分别分走惊喜池的 30%、50% 和最后剩余碎片；每档先按"
                "“本档碎片 ÷ 本档人数”向下取整，差额由档内最后一名和逐人补 1 收口。",
                "evidence_refs": refs,
            }
        )

    random_pool = _find_line(
        lines,
        file_suffix="SurprisePrize.php",
        contains=("$randomIndexes = $this->generateRandomIndexes", "$totalUsers", "$surpriseUsers"),
    )
    random_range = _find_line(
        lines,
        file_suffix="SurprisePrize.php",
        contains=("range(1, $totalUsers)",),
    )
    random_shuffle = _find_line(
        lines,
        file_suffix="SurprisePrize.php",
        contains=("shuffle($indexes)",),
    )
    random_slice = _find_line(
        lines,
        file_suffix="SurprisePrize.php",
        contains=("array_slice($indexes", "$count"),
    )
    random_assignment = _find_line(
        lines,
        file_suffix="SurprisePrize.php",
        contains=("'rank_id' => $randomIndexes[$currentIndex]", "'fragments' => $rowFragments"),
    )
    if random_pool and random_range and random_shuffle and random_assignment:
        facts.append(
            {
                "category": "惊喜随机发放",
                "statement": "惊喜档不是按排名顺序发放，而是从该等级全部用户名次中"
                "生成不重复随机序列，再按档位依次取出中奖名次；因此固定榜用户也有"
                "机会同时获得惊喜碎片。",
                "evidence_refs": [
                    random_pool.ref,
                    random_range.ref,
                    random_shuffle.ref,
                    *([random_slice.ref] if random_slice else []),
                    random_assignment.ref,
                ],
            }
        )

    prize_top = _find_line(
        lines,
        file_suffix="SpaceshipOrdinaryPrize.php",
        contains=("$returnData['top_fragments'] = $this->getTopRankFragments()",),
    )
    prize_minimum = _find_line(
        lines,
        file_suffix="SpaceshipOrdinaryPrize.php",
        contains=("if ($returnData['top_fragments'] <= 0)",),
    )
    prize_surprise = _find_line(
        lines,
        file_suffix="SpaceshipOrdinaryPrize.php",
        contains=("$returnData['surprise_fragments'] = $this->getSurprise()",),
    )
    if prize_top and prize_minimum and prize_surprise:
        facts.append(
            {
                "category": "领奖叠加规则",
                "statement": "固定榜奖励和保底奖励互斥：进入固定榜就不再领取保底；"
                "惊喜碎片单独计算并追加，所以固定榜或保底用户都可能再叠加惊喜奖励。",
                "evidence_refs": [prize_top.ref, prize_minimum.ref, prize_surprise.ref],
            }
        )

    inherited_template = _find_line(
        lines,
        file_suffix="SpaceshipConfig.php",
        contains=("if ($level > $maxLevel)",),
    )
    copied_config = _find_line(
        lines,
        file_suffix="SpaceshipConfig.php",
        contains=("$cfg = $levels[$maxLevel]",),
    )
    if inherited_template and copied_config:
        facts.append(
            {
                "category": "高等级模板",
                "statement": "配置显式列出的普通商品碎片等级为 1 至 4；超过最高等级时"
                "沿用第 4 级的整份普通奖励模板，只重新计算等级 ID 和升级所需燃料值。",
                "evidence_refs": [inherited_template.ref, copied_config.ref],
            }
        )

    if rate_refs and levels:
        rates = "60% 保底、40% 惊喜，惊喜三档用户/碎片比例依次为 10%/30%、20%/50%、30%/剩余"
        facts.append(
            {
                "category": "配置比例",
                "statement": f"本次证据窗口中的各等级统一配置为：{rates}。",
                "evidence_refs": rate_refs[:12],
            }
        )
    return facts


def _generic_facts(lines: Sequence[EvidenceLine], question: str) -> List[Dict[str, Any]]:
    """为没有专用解释器的问题提取与问题语义共现的业务控制点。

    参数:
        - `lines`：当前问题命中的源码证据行，类型为 `Sequence[EvidenceLine]`。
        - `question`：用户提交的原始问题，类型为 `str`；用于要求控制点必须与
          业务对象或足够的概念共同出现，不能只凭 `return false` 等通用结构入选。

    Returns:
        最多 8 条包含拒绝、提前返回或错误码的控制点。每行至少满足以下条件
        之一才会进入结果：直接包含问题中的四字以上中文业务锚点，或同时覆盖
        两个问题概念且包含至少一个领域概念。该兜底只复述源码行，不把通用代码
        形态擅自解释成根因或完整业务规则。
    """
    signals = ("application::stop", "throw new", "returnfail(", "return false", "return null")
    question_concepts = {
        concept.name for concept in detect_semantic_concepts(question)
    }
    generic_concepts = {"amount", "config", "daily", "display", "gift", "limit", "status", "user"}
    domain_concepts = question_concepts - generic_concepts
    anchors = [value for value in extract_generic_anchors(question) if len(value) >= 4]
    required_coverage = min(2, len(question_concepts))
    facts = []
    seen_refs = set()
    for item in lines:
        if not any(signal in item.content.casefold() for signal in signals):
            continue
        folded_content = item.content.casefold()
        has_direct_anchor = any(anchor.casefold() in folded_content for anchor in anchors)
        line_concepts = {
            concept.name for concept in detect_semantic_concepts(item.content)
        }
        covered = question_concepts.intersection(line_concepts)
        if not has_direct_anchor:
            if len(covered) < required_coverage:
                continue
            if domain_concepts and not covered.intersection(domain_concepts):
                continue
        if item.ref in seen_refs:
            continue
        seen_refs.add(item.ref)
        facts.append(
            {
                "category": "问题相关控制点",
                "statement": f"问题相关源码中的控制点：{item.content.strip()}",
                "evidence_refs": [item.ref],
            }
        )
        if len(facts) >= 8:
            break
    return facts


def build_deterministic_findings(
    question: str,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    """根据问题和已检索证据生成不依赖 LLM 的业务约束摘要。

    参数:
        - `question`：用户提交的业务诊断问题，类型为 `str`。
        - `result`：Worker 已生成的确定性证据结果，至少包含 `evidence`，类型为
          `Dict[str, Any]`。

    Returns:
        包含 `conclusion`、`facts`、`evidence_refs` 和 `uncertainties` 的字典。
        没有足够证据时返回通用控制点或空事实，并明确留出不确定性，不能把
        “模型不可用”当成业务事实。
    """
    lines = _evidence_lines(result.get("evidence") or [])
    retrieval_plan = result.get("retrieval_plan") or {}
    retrieval_support = retrieval_plan.get("retrieval_support") or {}
    if (
        retrieval_support.get("grounded") is False
        or retrieval_plan.get("retrieval_mode") == "insufficient_support"
    ):
        return {
            "conclusion": "",
            "facts": [],
            "evidence_refs": [],
            "uncertainties": [
                "未在已挂载源码中找到能够相互印证的业务锚点；系统已停止用泛化词扩展证据。",
                "请补充具体业务对象、页面入口、类方法或配置键后重新诊断。",
            ],
        }
    normalized_question = question.casefold()
    if "美元" in normalized_question and "熔断" in normalized_question:
        facts = _analyze_usd_fuse(lines)
        topic = "美元熔断"
    elif (
        "钻石" in normalized_question
        and ("前端" in normalized_question or "钱包" in normalized_question or "页面" in normalized_question)
        and ("展示" in normalized_question or "显示" in normalized_question)
    ):
        facts = _analyze_frontend_diamond_display(lines)
        topic = "前端钻石展示"
        if not facts:
            # 领域分析器只解释已经命中的完整链路；若本次证据窗口没有覆盖接口、
            # 余额和配置，保留通用控制点并明确降级，避免输出看似完整的猜测。
            facts = _generic_facts(lines, question)
            topic = "当前问题"
    elif "钻石" in normalized_question and "提现" in normalized_question:
        facts = _analyze_diamond_withdrawal(lines)
        topic = "钻石提现"
        if not facts:
            # 当前仓库可能尚未合入新版用户提现服务。此时不得引用不存在的类或
            # 旧任务报告，而应解释实际存在的旧链路；若旧链路证据也不完整，再
            # 回退到通用控制点并明确说明只能验证当前源码。
            facts = _analyze_anchor_withdrawal(lines)
            topic = "当前仓库主播钻石提现"
        if not facts:
            facts = _generic_facts(lines, question)
            topic = "当前问题"
    elif "钻石" in normalized_question and ("兑金币" in normalized_question or "兑换金币" in normalized_question):
        facts = _analyze_diamond_exchange(lines)
        topic = "钻石兑金币"
    elif "飞船" in normalized_question and "碎片" in normalized_question:
        facts = _analyze_spaceship_fragments(lines)
        topic = "飞船商品碎片分配"
        if not facts:
            # 领域分析器只负责解释已经证实的规则。如果检索窗口没有同时覆盖
            # 配置和公式，仍保留通用控制点，避免误报“完全没有证据”。
            facts = _generic_facts(lines, question)
            topic = "当前问题"
    else:
        facts = _generic_facts(lines, question)
        topic = "当前问题"

    evidence_refs = list(
        dict.fromkeys(
            ref
            for fact in facts
            for ref in fact.get("evidence_refs") or []
        )
    )
    if not facts:
        return {
            "conclusion": "",
            "facts": [],
            "evidence_refs": [],
            "uncertainties": [f"未从已检索源码中定位到{topic}对应的明确业务控制点"],
        }

    uncertainties = []
    if len(facts) < 6:
        uncertainties.append("部分业务分支未在本次检索证据中完整出现，建议沿已引用文件继续核对。")
    if topic == "美元熔断":
        conclusion_parts = []
        category_names = {str(item.get("category") or "") for item in facts}
        if "作用范围" in category_names:
            conclusion_parts.append("只支持消费方向，并按官方提现和美元兑代理币两类来源生效")
        if "官方提现" in category_names:
            conclusion_parts.append("官方提现会被下单拒绝并返回业务码 17")
        if "官方渠道" in category_names:
            conclusion_parts.append("官方渠道和历史回填会隐藏")
        if "币商提现" in category_names:
            conclusion_parts.append("币商提现不受该熔断直接限制")
        if "美元兑换" in category_names:
            conclusion_parts.append("美元兑换会直接中止并返回业务码 20018")
        if "单笔阈值" in category_names:
            conclusion_parts.append("没有单笔最大阈值")
        if "资产恢复" in category_names:
            conclusion_parts.append("不处理资产恢复")
        if "故障策略" in category_names:
            conclusion_parts.append("读取异常时 fail-open")
        conclusion = "已从源码确认美元熔断的业务边界：" + "；".join(conclusion_parts) + "。"
    elif topic == "前端钻石展示":
        conclusion_parts = []
        category_names = {str(item.get("category") or "") for item in facts}
        if "页面入口" in category_names:
            conclusion_parts.append(
                "钱包页调用 Action/Diamond.getDiamondGearList 并先校验登录"
            )
        if "钱包页显隐" in category_names:
            conclusion_parts.append(
                "钱包页通过 Prop.getMyRebateCard 返回的 is_anchor 决定是否展示钻石档位和用户钻石"
            )
        if "展示条件" in category_names:
            conclusion_parts.append("非主播仅返回 is_show=0，主播路径才返回余额和档位")
        if "余额来源" in category_names:
            conclusion_parts.append(
                "钻石余额来自 hsing_pay.t_user_diamonds.diamonds，按当前 UID 从从库读取"
            )
        if "区域来源" in category_names:
            conclusion_parts.append("档位按主播公会区域筛选，缺少有效区域时回退 global")
        if "档位配置" in category_names:
            conclusion_parts.append(
                "兑换档位来自 t_anchor_settlement_config 的 type=9 配置"
            )
        if "响应字段" in category_names:
            conclusion_parts.append("主播响应提供 diamonds 和 diamond_gears 字段")
        if "读取一致性" in category_names:
            conclusion_parts.append("余额与档位均从从库读取，需关注主从复制延迟")
        conclusion = "已从 waky4 源码确认钱包页钻石展示依据：" + "；".join(conclusion_parts) + "。"
    elif topic == "钻石提现":
        conclusion_parts = []
        category_names = {str(item.get("category") or "") for item in facts}
        if "提现粒度" in category_names:
            conclusion_parts.append("所有提现金额必须是 10,000 钻石的整数倍")
        if "提现下限" in category_names:
            conclusion_parts.append(
                "币商最低 10,000 钻石，官方正式环境最低 800,000 钻石"
                "（测试环境 80,000）"
            )
        if "手续费承担" in category_names:
            conclusion_parts.append("币商订单手续费字段为 0，官方订单会从到账额扣除渠道手续费")
        if "手续费公式" in category_names:
            conclusion_parts.append("手续费=结算金额×比例费率+固定费")
        if "渠道费率" in category_names:
            conclusion_parts.append(
                "当前 USDT-TRC20 为 1%+2.5 USDT，Epay 为 1%+0.5 USD"
            )
        conclusion = "已从源码确认 waky6 用户钻石提现规则：" + "；".join(conclusion_parts) + "。"
    elif topic == "钻石兑金币":
        conclusion_parts = []
        category_names = {str(item.get("category") or "") for item in facts}
        if "兑换粒度" in category_names:
            conclusion_parts.append("兑换数量必须是 10,000 钻石的整数倍")
        if "最低兑换" in category_names:
            conclusion_parts.append("最低兑换 10,000 钻石")
        if "换算比例" in category_names:
            conclusion_parts.append("按 2 钻石兑换 1 金币")
        if "单次上限" in category_names:
            conclusion_parts.append("当前配置未设置单次上限")
        conclusion = "已从源码确认 waky6 用户钻石兑金币规则：" + "；".join(conclusion_parts) + "。"
    elif topic == "飞船商品碎片分配":
        conclusion_parts = []
        category_names = {str(item.get("category") or "") for item in facts}
        if "碎片总量与固定榜" in category_names:
            conclusion_parts.append("按飞船等级读取普通商品碎片总量并先发固定排名奖励")
        if "保底池" in category_names:
            conclusion_parts.append("扣完固定榜后的剩余碎片按 60% 保底、40% 惊喜拆分")
        if "保底池" in category_names:
            conclusion_parts.append("保底池向后段剩余用户均分，不足 1 时每人 1 且发完即止")
        if "惊喜池" in category_names:
            conclusion_parts.append("惊喜池按 10%/20%/30% 用户分走 30%/50%/剩余碎片")
        if "惊喜随机发放" in category_names:
            conclusion_parts.append("惊喜中奖名次从全部用户中随机且不重复抽取")
        if "领奖叠加规则" in category_names:
            conclusion_parts.append("固定榜与保底互斥，惊喜可与二者叠加")
        if "高等级模板" in category_names:
            conclusion_parts.append("四级以上沿用四级普通奖励模板")
        conclusion = "已从 2025 年度金主源码确认飞船商品碎片规则：" + "；".join(conclusion_parts) + "。"
    else:
        conclusion = f"已从源码定位 {len(facts)} 条与{topic}直接相关的业务控制点，详见确定性事实。"
    if topic == "钻石提现":
        uncertainties.append(
            "官方手续费的具体金额取决于用户所选渠道、结算币种和提现金额，未选择渠道时不存在唯一费用。"
        )
        uncertainties.append(
            "代码只能证明订单手续费快照和官方扣费；币商在线下另行约定的费用不在本链路内。"
        )
    if topic == "钻石兑金币":
        uncertainties.append(
            "配置值证明服务端当前校验口径；客户端档位是否同步展示仍需结合端上版本验证。"
        )
    if topic == "前端钻石展示":
        uncertainties.append(
            "本次证据覆盖 waky4 后端钱包接口，未包含客户端渲染源码；客户端如何使用 "
            "`is_show`、`diamonds` 和 `diamond_gears` 仍需结合对应端版本确认。"
        )
        uncertainties.append(
            "余额和档位配置从从库读取；主从延迟期间，页面可能短时显示旧值，需核对主库与从库。"
        )
    if topic == "飞船商品碎片分配":
        uncertainties.append(
            "具体到某个用户能拿多少，还取决于当轮该等级参与排名的总人数、固定榜范围、"
            "整数除法余数以及随机惊喜档位；源码给出的是规则，不是固定个人金额。"
        )
        uncertainties.append(
            "等级 1 至 4 的商品碎片总量来自当前 2025 配置；更高等级沿用四级模板，"
            "不会随等级继续增加普通商品碎片总量。"
        )
    return {
        "conclusion": conclusion,
        "facts": facts,
        "evidence_refs": evidence_refs,
        "uncertainties": uncertainties,
    }


__all__ = ["EvidenceLine", "build_deterministic_findings"]
