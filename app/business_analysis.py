"""基于已检索代码证据生成确定性业务约束摘要。

该兼容入口汇总领域分析器并生成固定报告结构，不访问线上服务或模型。具体业务分析
已经拆分到 `app.domain.diagnosis.analyzers`，调用方仍可从本模块使用原有公开函数。
"""

import re
from typing import Any, Dict, List

from app.domain.diagnosis.analyzers.common import (
    EvidenceLine,
    _evidence_lines,
)
from app.domain.diagnosis.analyzers.configuration import (
    _configuration_fact_conclusion,
    _configuration_literal_facts,
    activity_decision_source_facts,
    execution_source_facts,
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
from app.domain.diagnosis.config_graph import (
    configuration_enumeration_conclusion,
    configuration_enumeration_facts,
    configuration_env_value_conclusion,
    configuration_env_value_facts,
)
from app.query_understanding import detect_semantic_concepts
from app.question_type import QuestionType, asks_member_count, classify_question


def _unresolved_activity_name(
    question: str,
    activity_link: Dict[str, Any],
) -> str:
    """识别知识库未能链接的显式中文活动名。

    参数:
        - `question`：用户原始业务问题，类型为 `str`。
        - `activity_link`：工作流写入的实体链接摘要，类型为 `Dict[str, Any]`；
          `available` 表示知识库是否成功构建，`ambiguous` 表示分差不足。

    Returns:
        问题中 `X活动` 形式的未链接活动名；知识库不可用、已成功链接或问题未
        显式点名活动时返回空字符串。该名称只用于报告缺口，不参与事实生成。
    """
    if not activity_link.get("available"):
        return ""
    if activity_link.get("activity_id") and not activity_link.get("ambiguous"):
        return ""
    match = re.search(r"([\u4e00-\u9fff]{2,12})活动", question or "")
    return match.group(1) if match else ""


def _scope_lines_to_linked_activity(
    lines: List[EvidenceLine],
    activity_link: Dict[str, Any],
) -> List[EvidenceLine]:
    """把证据行收敛到知识库链接到的活动。

    参数:
        - `lines`：当前诊断证据展开后的源码行，类型为 `List[EvidenceLine]`；
          可能同时包含多个活动的配置和实现文件。
        - `activity_link`：工作流写入的实体链接摘要，类型为 `Dict[str, Any]`；
          提供 `activity_id`、`config_file`、`related_files` 和 `ambiguous`。

    Returns:
        只属于链接活动的配置与相关实现文件证据行。未链接、链接歧义或过滤后
        为空时原样返回，保证普通代码问题和其他分析器不受影响。该过滤让链接
        结果成为配置取值的唯一活动边界，不再依赖中文别名打分。
    """
    if not activity_link.get("activity_id") or activity_link.get("ambiguous"):
        return lines
    config_file = str(activity_link.get("config_file") or "").replace(
        "\\", "/"
    ).casefold()
    related = {
        str(path or "").replace("\\", "/").casefold()
        for path in (activity_link.get("related_files") or [])
    }
    related.add(config_file)
    related.discard("")
    if not related:
        return lines
    scoped = [
        item
        for item in lines
        if item.file.replace("\\", "/").casefold() in related
    ]
    return scoped or lines


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
    activity_link = retrieval_plan.get("activity_link") or {}
    # 知识库链接成功后，后续配置枚举、取值和判定入口只读取该活动的配置与
    # 相关实现文件；未链接的问题保持原证据窗口不变。
    lines = _scope_lines_to_linked_activity(lines, activity_link)
    history_facts = analyze_history_events(lines, history_events)
    # 显式类名只在通用分支填充；知识库链接或缺口分支提前返回时也要保证
    # 后续报告组装能安全读取该变量。
    explicit_classes: list[str] = []
    unresolved_activity = _unresolved_activity_name(question, activity_link)
    linked_activity_resolved = bool(activity_link.get("activity_id")) and not activity_link.get(
        "ambiguous"
    )
    if history_facts:
        facts = history_facts
        topic = "功能版本历史"
        explicit_topic = False
    elif unresolved_activity:
        # 知识库明确未能把“X活动”链接到唯一活动时，禁止继续读取证据中的
        # 其他活动配置；否则未知活动会被答成恰好同在证据窗口的活动数值。
        return {
            "conclusion": "",
            "facts": [],
            "evidence_refs": [],
            "uncertainties": [
                f"未在项目知识库中定位到活动「{unresolved_activity}」，"
                "已停止用其他活动配置推断。",
                "请确认活动中文名是否出现在配置、语言包或后台文案中。",
            ],
            "gap": True,
        }
    elif (
        decision_facts := activity_decision_source_facts(
            lines,
            question,
            activity_link,
        )
    ):
        facts = decision_facts
        topic = "等级判定入口"
        # 判定入口事实已经包含类名、方法名和配置路径，不需要再套用显式目标类
        # 的通用段落，避免结论出现“等级判定入口包括 `等级判定入口为 …`”。
        explicit_topic = False
    elif (
        retrieval_support.get("grounded") is False
        or retrieval_plan.get("retrieval_mode") == "insufficient_support"
    ) and not linked_activity_resolved:
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
        # 领域分析器只覆盖高风险、强业务语义的问题。其余问题先按通用问题类型
        # 路由：配置枚举走配置图，配置取值走现有配置解析，流程和调用链继续使用
        # 显式符号与通用控制点。新增活动或业务名不再需要新增映射。
        question_type = classify_question(question, question_concepts)
        expanded_scope_terms = [
            str(value)
            for value in (retrieval_plan.get("expanded_code_terms") or [])
        ]
        # 配置类问题优先走配置图/配置取值。领域分析器（飞船碎片、钻石兑换等）
        # 解释的是业务流程，不能代替“某个等级门槛/奖励/比例是多少”的配置取值；
        # 只有非配置问题才交给领域分析器。
        # 用户直接写出点号配置路径或反引号键时，即使问题缺少“是多少”等取值
        # 动作词，也属于配置取值问题，必须交给配置解析器而不是流程分析器。
        mentions_config_key = bool(
            re.search(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_.]*", question)
            or re.search(r"`[A-Za-z_][A-Za-z0-9_.:-]+`", question)
        )
        config_question = (
            question_type
            in {
                QuestionType.CONFIG_ENUMERATION,
                QuestionType.CONFIG_LOOKUP,
            }
            or asks_member_count(question)
            or mentions_config_key
            # 活动起止时间来自活动配置的 `stime`/`etime`，属于配置取值，
            # 不能因为“什么时候开始”的问法被当成流程或历史问题。
            or bool(
                re.search(
                    r"活动.*(?:开始|结束|起止)"
                    r"|(?:开始|结束|开放|截止).*(?:时间|日期)",
                    question,
                )
            )
        )
        # 门槛问题需要逐级枚举给出完整等级表；阈值问法与其他配置取值共用。
        threshold_intent = any(
            token in question
            for token in ("门槛", "阈值", "所需积分", "要求积分")
        )
        # “四个等级门槛及头衔奖励”这类问题在问法上不一定被分类成枚举，但
        # 明确点了等级/阶段，逐级配置图能同时给出名称、门槛和奖励，比只取
        # 同文件散落字面量更完整。
        level_intent = any(
            token in question.casefold()
            for token in ("等级", "档位", "级别", "阶段", "vip")
        )
        enumeration_facts = (
            configuration_enumeration_facts(
                lines,
                question,
                expanded_scope_terms,
            )
            if config_question
            and (
                question_type == QuestionType.CONFIG_ENUMERATION
                or asks_member_count(question)
                or level_intent
            )
            else []
        )
        # 问题以中文活动名开头时，该名称必须真实出现在证据里；否则当前候选
        # 属于其他活动。宁可报告缺口，也不能拿别的活动等级表作答。
        activity_prefix = re.match(r"[\u4e00-\u9fff]{4,}", question.strip())
        if enumeration_facts and activity_prefix:
            # 去掉“测试环境/每个/当前”这类问法外壳，剩下的前四个字才当作
            # 活动名；若剩余部分仍是“等级/阶段/奖励”等泛化词，则不启用
            # 名称门控，避免把正常的通用枚举问题误判成缺口。
            name_candidate = re.sub(
                r"^(?:测试环境|正式环境|生产环境|线上环境|当前|所有|每个|全部"
                r"|各个|活动|中|里|下)+",
                "",
                activity_prefix.group(0),
            )
            # 问题开头的连续中文会把“游戏榜第/家族城堡前”这类问法结构一起
            # 截进来；去掉尾部序号词后再判断是否真的是活动名，避免把
            # “游戏榜第 6 名”误判成需要四个字活动名的证据缺口。
            name_candidate = re.sub(
                r"(?:第|前|中|里|下|的|共|有|是|为|多|少|几|个|名|档|级|轮)+$",
                "",
                name_candidate,
            )
            generic_head = any(
                name_candidate.startswith(token)
                for token in ("每个", "等级", "阶段", "奖励", "门槛",
                              "环境", "测试", "所有", "全部", "各个")
            )
            if not generic_head and len(name_candidate) >= 4:
                evidence_text = " ".join(line.content for line in lines)
                if name_candidate[:4] not in evidence_text:
                    enumeration_facts = []
        # 用户明确追问“每个等级/档位的门槛”却没有解析出逐级配置时，继续回退
        # 到任意配置字面量会把其他活动的数值当成答案。这里直接报告证据缺口，
        # 并标记 `gap` 让上层跳过 AI 补充，避免模型继续输出无关活动。
        # 只有“确实尝试过逐级枚举”的问题才走缺口门控；普通取值问题
        # （“四个冒险点的门槛是多少”）按配置字面量回答是正确的，不能误判。
        enumeration_attempted = config_question and (
            question_type == QuestionType.CONFIG_ENUMERATION
            or asks_member_count(question)
            or level_intent
        )
        if enumeration_attempted and threshold_intent and not enumeration_facts:
            return {
                "conclusion": "",
                "facts": [],
                "evidence_refs": [],
                "gap": True,
                "uncertainties": [
                    "未在已检索证据中找到与问题对应的等级门槛配置；"
                    "为避免用其他活动的数值作答，本次不输出等级结论。",
                    "请补充活动名称、配置目录或门槛字段（例如 score/limit）后重新诊断。",
                ],
            }
        # 点名“第 99 名”这类超出配置编号的名次时，奖品表不会有对应条目。
        # 此时枚举结果为空，必须报告缺口，不能退化为“输出整张名次表”。
        if (
            not enumeration_facts
            and re.search(r"第\s*\d+\s*名", question)
            and any(
                token in question for token in ("奖励", "奖品", "奖金", "金币")
            )
        ):
            # 名次表通常位于配置文件中部。若证据里连 `rank_prizes` 都没有，
            # 更可能是窗口只读到了文件头，而不是该名次真的不存在；文案要
            # 区分这两种情况，便于验收时定位是检索问题还是配置问题。
            rank_table_missing = not any(
                "rank_prizes" in line.content or "top_prizes" in line.content
                for line in lines
            )
            hint = (
                "已检索到活动配置，但证据窗口未包含 rank_prizes/top_prizes "
                "名次表，可能是完整配置窗口未生效；请重跑或扩大证据窗口后复核。"
                if rank_table_missing
                else "名次表已完整读取，但其中没有该序号对应的奖励配置。"
            )
            return {
                "conclusion": "",
                "facts": [],
                "evidence_refs": [],
                "gap": True,
                "uncertainties": [
                    hint
                    + "为避免用其他名次的数值作答，本次不输出奖励结论。",
                ],
            }
        configuration_facts = (
            _configuration_literal_facts(
                lines,
                question,
                scope_terms=expanded_scope_terms,
            )
            if config_question and not enumeration_facts
            else []
        )
        # `getByTestMode` 的字符串分支（商户号、开关、默认地址等）不属于
        # 数字/布尔字面量。没有专用分析器时先按通用配置图读取环境分支，避免
        # 每新增一个支付渠道就要补一套取值代码。
        # 数据来源/结算防重问题看的是活动服务里的数据库模型调用。它优先于
        # 配置字面量：`config.php` 只剩基础项时，继续回答 title/vol 会掩盖
        # “期数来自数据库、结算通过 existsByVol 防重”的真正答案。
        execution_facts = execution_source_facts(lines, question)
        env_value_facts = (
            configuration_env_value_facts(
                lines,
                question,
                expanded_scope_terms,
            )
            if analyzer is None
            and not enumeration_facts
            and not configuration_facts
            and not execution_facts
            else []
        )
        if enumeration_facts:
            facts = enumeration_facts
            topic = "配置枚举"
            explicit_topic = True
        elif execution_facts:
            facts = execution_facts
            topic = "执行实现"
            explicit_topic = True
        elif env_value_facts:
            facts = env_value_facts
            topic = "环境配置值"
            explicit_topic = True
        elif configuration_facts:
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
            "gap": True,
            "uncertainties": [f"未从已检索源码中定位到{topic}对应的明确业务控制点"],
        }

    uncertainties = []
    if len(facts) < 6:
        uncertainties.append("部分业务分支未在本次检索证据中完整出现，建议沿已引用文件继续核对。")
    if explicit_topic:
        if topic in {"活动配置值", "配置枚举", "环境配置值"}:
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
    if topic == "配置枚举":
        # 通用配置图已按等级/档位逐条解析，直接拼接已验证的配置行，不推断
        # 证据之外的奖励名称或业务标签。
        conclusion = configuration_enumeration_conclusion(facts)
    elif topic == "等级判定入口":
        # 知识库已把问题链接到活动，并定位到读取该活动等级配置的类和方法；
        # 这里直接复用事实中的可读语句，不额外推断调用顺序。
        conclusion = "；".join(
            str(item.get("statement") or "") for item in facts
        )
        if conclusion and not conclusion.endswith("。"):
            conclusion += "。"
    elif topic == "环境配置值":
        conclusion = configuration_env_value_conclusion(facts)
    elif topic == "活动配置值":
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
        # 有事实但拼不出可读结论时同样视为证据缺口，禁止返回空结论却不标记，
        # 否则前端会把空字符串展示成“已确认”。
        "gap": not conclusion.strip(),
    }


__all__ = [
    "EvidenceLine",
    "build_deterministic_findings",
    "_explicit_fact_conclusion",
    "_explicit_question_category_bonus",
    "_explicit_symbol_line_signal",
]
