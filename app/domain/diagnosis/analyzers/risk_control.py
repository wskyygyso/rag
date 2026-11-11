"""美元熔断等风险控制事实分析器。

分析器只读取 `EvidenceLine` 和问题画像，不负责检索或持久化。
"""

import math
import re
from typing import Any, Dict, List, Sequence

from app.domain.diagnosis.analyzers.common import EvidenceLine, _find_line, _find_line_after, _evidence_lines


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
