"""钻石与主播提现规则事实分析器。

分析器只读取 `EvidenceLine` 和问题画像，不负责检索或持久化。
"""

import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.domain.diagnosis.analyzers.common import EvidenceLine, _config_integer, _config_number, _find_line, _find_line_after, _test_mode_integer_pair, _test_mode_number, _evidence_lines


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

    daily_scope_line = _find_line(
        lines,
        file_suffix="diamonds.php",
        contains=("自然日按用户手机时区计算",),
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
        scope_text = (
            "在按用户手机时区计算的自然日内"
            if daily_scope_line
            else "在每个自然日内"
        )
        refs = [official_max_line.ref, daily_line.ref]
        if daily_scope_line:
            refs.append(daily_scope_line.ref)
        facts.append(
            {
                "category": "官方金额与频次",
                "statement": f"官方渠道单笔最高 {max_text}，每个用户{scope_text}最多 "
                f"下单 {daily_text}。",
                "evidence_refs": refs,
            }
        )
    elif daily_pair:
        test_daily, production_daily = daily_pair
        daily_text = (
            f"测试环境 {test_daily} 笔、正式环境 {production_daily} 笔"
            if test_daily != production_daily
            else f"{production_daily} 笔"
        )
        scope_text = (
            "在按用户手机时区计算的自然日内"
            if daily_scope_line
            else "在每个自然日内"
        )
        refs = [daily_line.ref]
        if daily_scope_line:
            refs.append(daily_scope_line.ref)
        facts.append(
            {
                "category": "官方提现每日次数",
                "statement": f"官方钻石提现每个用户{scope_text}最多下单 {daily_text}。",
                "evidence_refs": refs,
            }
        )

    official_daily_call = _find_line(
        lines,
        file_suffix="WithdrawalOrderCreateService.php",
        contains=("$this->assertOfficialDailyLimit($uid, $orderType)",),
    )
    dealer_exclusion = _find_line(
        lines,
        file_suffix="WithdrawalOrderCreateService.php",
        contains=("$orderType !== self::ORDER_TYPE_OFFICIAL",),
    )
    daily_guard = _find_line(
        lines,
        file_suffix="WithdrawalOrderCreateService.php",
        contains=("$limit > 0", "$existingCount >= $limit"),
    )
    if official_daily_call and daily_guard:
        refs = [official_daily_call.ref, daily_guard.ref]
        scope_statement = ""
        if dealer_exclusion:
            refs.append(dealer_exclusion.ref)
            scope_statement = "该计数只拦截官方订单，币商订单不进入此上限判断。"
        facts.append(
            {
                "category": "官方次数拦截",
                "statement": "官方提现下单前会统计并校验自然日次数；`limit > 0` 时，"
                "已存在订单数达到或超过上限后，新订单直接抛出频繁操作异常。"
                + scope_statement,
                "evidence_refs": refs,
            }
        )

    counted_orders = _find_line(
        lines,
        file_suffix="WithdrawalOrderCreateService.php",
        contains=("countUserWithdrawalOrders(",),
    )
    all_orders_comment = _find_line(
        lines,
        file_suffix="WithdrawalOrderCreateService.php",
        contains=("统计包含当天已创建的所有官方订单", "不区分后续成功或失败"),
    )
    if counted_orders and all_orders_comment:
        facts.append(
            {
                "category": "官方次数计数口径",
                "statement": "每日次数按当天已创建的全部官方提现订单统计，不区分订单后续"
                "成功或失败；失败订单同样占用当期名额，不能用来绕过次数上限。",
                "evidence_refs": [counted_orders.ref, all_orders_comment.ref],
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

    if ratio and official_pair and test_mode_helper and test_mode_return:
        test_min, production_min = official_pair
        multiple_sentence = (
            f"所有提现金额还必须为 {multiple:,} 钻石的整数倍。"
            if multiple is not None
            else ""
        )
        refs = [
            official_min_line.ref,
            ratio_line.ref,
            test_mode_helper.ref,
            test_mode_return.ref,
        ]
        if multiple_line:
            refs.append(multiple_line.ref)
        facts.append(
            {
                "category": "官方美元最低兑换",
                "statement": f"官方美元提现最低兑换数：测试环境 {test_min:,} 钻石"
                f"（按 1 美元 = {ratio:,} 钻石换算约为 ${test_min / ratio:.2f}），"
                f"正式环境 {production_min:,} 钻石（约为 ${production_min / ratio:.2f}）。"
                f"{multiple_sentence}",
                "evidence_refs": refs,
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
