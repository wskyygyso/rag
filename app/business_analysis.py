"""基于已检索代码证据生成确定性业务约束摘要。

该兼容入口汇总领域分析器并生成固定报告结构，不访问线上服务或模型。具体业务分析
已经拆分到 `app.domain.diagnosis.analyzers`，调用方仍可从本模块使用原有公开函数。
"""

import re
from typing import Any, Dict

from app.domain.diagnosis.analyzers.common import (
    EvidenceLine,
    _evidence_lines,
)
from app.domain.diagnosis.analyzers.configuration import (
    _configuration_fact_conclusion,
    _configuration_literal_facts,
)
from app.domain.diagnosis.analyzers.activity import _analyze_spaceship_fragments
from app.domain.diagnosis.analyzers.display import _analyze_frontend_diamond_display
from app.domain.diagnosis.analyzers.exchange import _analyze_diamond_exchange
from app.domain.diagnosis.analyzers.explicit import (
    _explicit_class_names,
    _explicit_fact_conclusion,
    _explicit_question_category_bonus,
    _explicit_symbol_facts,
    _explicit_symbol_line_signal,
    _generic_facts,
    _requested_action_gaps,
)
from app.domain.diagnosis.analyzers.history import analyze_history_events
from app.domain.diagnosis.analyzers.registration import _analyze_device_registration_limit
from app.domain.diagnosis.analyzers.registry import ANALYZER_REGISTRY
from app.domain.diagnosis.analyzers.risk_control import _analyze_usd_fuse
from app.domain.diagnosis.analyzers.withdrawal import (
    _analyze_anchor_withdrawal,
    _analyze_diamond_withdrawal,
)
from app.query_understanding import detect_semantic_concepts


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
    history_events = result.get("history_events") or []
    retrieval_plan = result.get("retrieval_plan") or {}
    retrieval_support = retrieval_plan.get("retrieval_support") or {}
    inherited_target_files = retrieval_plan.get("inherited_target_files") or []
    normalized_question = question.casefold()
    question_concepts = {
        concept.name for concept in detect_semantic_concepts(question)
    }
    history_facts = analyze_history_events(lines, history_events)
    if history_facts:
        facts = history_facts
        topic = "功能版本历史"
        explicit_topic = False
    elif (
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
    else:
        explicit_classes = _explicit_class_names(question)
        explicit_topic = False
        analyzer = ANALYZER_REGISTRY.select(normalized_question, question_concepts)
        configuration_facts = (
            _configuration_literal_facts(lines, question)
            if analyzer is None
            else []
        )
        if configuration_facts:
            facts = configuration_facts
            topic = "活动配置值"
            explicit_topic = True
        elif analyzer is not None:
            facts = analyzer.analyze(lines)
            topic = analyzer.topic
            if analyzer.analyzer_id == "diamond_withdrawal" and not facts:
                # 当前仓库可能尚未合入新版用户提现服务，沿用现有旧链路分析器。
                facts = _analyze_anchor_withdrawal(lines)
                topic = "当前仓库主播钻石提现"
            if not facts and analyzer.analyzer_id in {
                "usd_fuse",
                "frontend_diamond_display",
                "diamond_withdrawal",
                "spaceship_fragments",
            }:
                explicit_facts = _explicit_symbol_facts(
                    lines,
                    question,
                    inherited_files=inherited_target_files,
                )
                if explicit_facts:
                    facts = explicit_facts
                    topic = f"显式{analyzer.topic}逻辑"
                    explicit_topic = True
            if not facts and analyzer.analyzer_id != "diamond_exchange":
                facts = _generic_facts(lines, question)
                topic = "当前问题"
        else:
            facts = _explicit_symbol_facts(
                lines,
                question,
                inherited_files=inherited_target_files,
            )
            if facts:
                topic = f"{', '.join(explicit_classes)} 业务逻辑"
                explicit_topic = True
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
    # `inherited_target_files` 表示 Worker 验证过的全部父类，不等于这些父类一定
    # 进入了事实。结论范围只根据最终 evidence_refs 中实际使用的父类文件计算，
    # 避免目标类证据充足时仍声称结论覆盖了无关的 `ServiceHelper` 或 `BaseCrontab`。
    fact_inherited_files = [
        file_name
        for file_name in inherited_target_files
        if any(ref.startswith(f"{file_name}:") for ref in evidence_refs)
    ]
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
    if explicit_topic:
        if topic == "活动配置值":
            uncertainties.append(
                "上述事实来自问题指定的活动配置及其当前证据窗口；数据库覆盖值、"
                "运行环境切换和未进入证据窗口的配置分支仍需继续核对。"
            )
        else:
            fact_scope = "目标类及已验证直接基类" if fact_inherited_files else "目标类"
            uncertainties.append(
                f"上述事实来自用户显式指定的{fact_scope}及其当前证据窗口；"
                "类外调用方、运行态数据和未展开的私有方法仍需继续核对。"
            )
        uncertainties.extend(
            _requested_action_gaps(
                lines,
                question,
                inherited_files=inherited_target_files,
            )
        )
    if topic == "活动配置值":
        conclusion = _configuration_fact_conclusion(facts)
    elif topic == "VIP等级附赠奖品":
        # 枚举型问题必须逐级回答，不能只取最高分的一条配置。事实本身来自
        # newNobleLv.php 的测试分支，结论只拼接已验证的等级语句。
        conclusion = "测试环境中各 VIP 等级附赠的奖品为：" + "；".join(
            str(item.get("statement") or "") for item in facts
        ) + "。以上取值来自贵族等级配置中 `getByTestMode` 的测试分支。"
    elif topic == "美元熔断":
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
    elif topic == "设备注册上限":
        limit_fact = next(
            (item for item in facts if item.get("category") == "测试环境设备注册上限"),
            None,
        )
        environment_label = "测试环境"
        if limit_fact is None:
            limit_fact = next(
                (item for item in facts if item.get("category") == "设备注册上限"),
                None,
            )
            environment_label = ""
        if limit_fact is None:
            limit_fact = next(
                (item for item in facts if item.get("category") == "正式环境设备注册上限"),
                None,
            )
            environment_label = "正式环境"
        limit_match = re.search(
            r"(?:上限为|为)\s*(\d+)",
            str((limit_fact or {}).get("statement") or ""),
        )
        category_names = {str(item.get("category") or "") for item in facts}
        if limit_match:
            prefix = f"{environment_label}中，" if environment_label else ""
            conclusion = (
                f"已从源码确认{prefix}同一设备最多可注册 {int(limit_match.group(1))} 个用户。"
            )
            if "测试环境设备注册上限" in category_names and "正式环境设备注册上限" in category_names:
                production_fact = next(
                    (item for item in facts if item.get("category") == "正式环境设备注册上限"),
                    None,
                )
                production_match = re.search(
                    r"上限为\s*(\d+)",
                    str((production_fact or {}).get("statement") or ""),
                )
                if production_match:
                    conclusion += (
                        f"正式环境相应用户数量上限为 {int(production_match.group(1))} 个。"
                    )
            if "注册拦截条件" in category_names:
                conclusion += "设备注册计数达到或超过该上限后，注册校验会拒绝继续创建账号。"
            if "短信注册前置校验" in category_names:
                conclusion += "发送注册短信前也会执行同类数量校验。"
        else:
            conclusion = "已定位设备注册控制点，但本次证据未包含明确的数值上限。"
    elif topic == "功能版本历史":
        history_fact = next(
            (item for item in facts if item.get("category") == "历史首次出现"),
            None,
        )
        category_names = {str(item.get("category") or "") for item in facts}
        if history_fact:
            conclusion = str(history_fact.get("statement") or "")
            version_fact = next(
                (item for item in facts if item.get("category") == "版本门槛"),
                None,
            )
            encoding_fact = next(
                (item for item in facts if item.get("category") == "版本编码规则"),
                None,
            )
            if version_fact:
                conclusion += str(version_fact.get("statement") or "")
            if encoding_fact:
                conclusion += str(encoding_fact.get("statement") or "")
            conclusion += "这里证明的是可访问代码历史中的首次相关实现，不等同于线上商店首发日期。"
        else:
            conclusion = "已检索到相关 Git 历史，但尚未形成可确认的版本起始结论。"
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
        requested_fee = "fee" in question_concepts or any(
            term in normalized_question for term in ("手续费", "费率")
        )
        requested_frequency = "count" in question_concepts or any(
            term in normalized_question for term in ("次数", "几次", "多少次")
        )
        requested_minimum = any(
            term in normalized_question for term in ("最低", "最小", "最少")
        )
        official_minimum_fact = next(
            (
                item
                for item in facts
                if item.get("category") == "官方美元最低兑换"
            ),
            None,
        )
        frequency_fact = next(
            (
                item
                for item in facts
                if item.get("category") in {"官方金额与频次", "官方提现每日次数"}
            ),
            None,
        )
        if requested_minimum and official_minimum_fact:
            conclusion_parts.append(
                str(official_minimum_fact.get("statement") or "").rstrip("。")
            )
        elif frequency_fact:
            conclusion_parts.append(str(frequency_fact.get("statement") or ""))
        if requested_frequency and "官方次数拦截" in category_names:
            guard_fact = next(
                item for item in facts if item.get("category") == "官方次数拦截"
            )
            conclusion_parts.append(str(guard_fact.get("statement") or ""))
        if requested_frequency and "官方次数计数口径" in category_names:
            counting_fact = next(
                item for item in facts if item.get("category") == "官方次数计数口径"
            )
            conclusion_parts.append(str(counting_fact.get("statement") or ""))
        if not (requested_minimum and official_minimum_fact):
            granularity_fact = next(
                (item for item in facts if item.get("category") == "提现粒度"),
                None,
            )
            minimum_fact = next(
                (item for item in facts if item.get("category") == "提现下限"),
                None,
            )
            if granularity_fact:
                conclusion_parts.append(
                    str(granularity_fact.get("statement") or "").rstrip("。")
                )
            if minimum_fact:
                conclusion_parts.append(
                    str(minimum_fact.get("statement") or "").rstrip("。")
                )
        if requested_fee:
            dealer_minimum_fact = next(
                (item for item in facts if item.get("category") == "提现下限"),
                None,
            )
            if dealer_minimum_fact:
                dealer_clause = str(
                    dealer_minimum_fact.get("statement") or ""
                ).split("；", 1)[0]
                if "币商" in dealer_clause:
                    conclusion_parts.append(dealer_clause)
            for category in ("手续费承担", "手续费公式", "渠道费率"):
                fee_fact = next(
                    (item for item in facts if item.get("category") == category),
                    None,
                )
                if fee_fact:
                    conclusion_parts.append(
                        str(fee_fact.get("statement") or "").rstrip("。")
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
        conclusion = _explicit_fact_conclusion(
            facts,
            explicit_classes,
            inherited_files=fact_inherited_files,
        )
    if topic == "钻石提现" and (
        "fee" in question_concepts
        or any(term in normalized_question for term in ("手续费", "费率"))
    ):
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
    if topic == "设备注册上限":
        uncertainties.append(
            "该结论表示源码注册校验的上限；历史清理、设备号伪造、第三方登录异常路径"
            "或运行态计数器修复仍需结合目标环境数据核对。"
        )
    if topic == "功能版本历史":
        uncertainties.append(
            "Git 历史只能证明当前挂载仓库中可访问提交的首次相关实现；对外发布包是否"
            "在更早或更晚版本启用，仍需结合发布记录、商店版本和客户端配置核对。"
        )
    return {
        "conclusion": conclusion,
        "facts": facts,
        "evidence_refs": evidence_refs,
        "uncertainties": uncertainties,
    }


__all__ = [
    "EvidenceLine",
    "build_deterministic_findings",
    "_explicit_fact_conclusion",
    "_explicit_question_category_bonus",
    "_explicit_symbol_line_signal",
]
