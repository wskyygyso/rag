"""确定性业务分析器注册表的选择与扩展边界测试。"""

from app.domain.diagnosis.analyzers.registry import ANALYZER_REGISTRY, AnalyzerRegistry
from app.query_understanding import detect_semantic_concepts


def _concepts(question: str) -> set[str]:
    """提取问题中的通用业务概念名，供分析器路由断言使用。

    参数:
        - `question`：用户原始诊断问题；测试只依赖通用概念识别，不构造完整
          问句模板。

    Returns:
        当前问题识别出的稳定概念名集合。
    """
    return {item.name for item in detect_semantic_concepts(question)}


def test_registered_analyzer_ids_are_unique_and_ordered() -> None:
    """验证分析器插件 ID 唯一且注册顺序稳定，避免 Worker 间路由漂移。"""
    analyzer_ids = ANALYZER_REGISTRY.analyzer_ids

    assert len(analyzer_ids) == len(set(analyzer_ids))
    assert analyzer_ids == [
        "usd_fuse",
        "device_registration",
        "frontend_diamond_display",
        "diamond_withdrawal",
        "anchor_withdrawal",
        "diamond_exchange",
        "spaceship_fragments",
    ]


def test_registry_selects_analyzers_from_composable_business_features() -> None:
    """验证问题通过业务概念组合选择分析器，而不是绑定完整问句。"""
    cases = {
        "WAKY6 项目 排查触发美元熔断时，业务有哪些具体限制？": "usd_fuse",
        "同一设备号最多能注册多少个用户？": "device_registration",
        "前端钱包页面展示的钻石档位依据是什么？": "frontend_diamond_display",
        "钻石提现最小兑换金额和手续费怎么算？": "diamond_withdrawal",
        "waky6 钻石兑换美元官方渠道最低兑换数是多少？": "diamond_withdrawal",
        "钻石换美元官方最少多少钻？": "diamond_withdrawal",
        "钻石如何兑换金币？": "diamond_exchange",
        "官方渠道钻石兑换金币最低多少？": "diamond_exchange",
        "年度活动飞船商品碎片怎么分配？": "spaceship_fragments",
    }

    for question, expected in cases.items():
        selected = ANALYZER_REGISTRY.select(question.casefold(), _concepts(question))
        assert selected is not None, question
        assert selected.analyzer_id == expected, question


def test_registry_returns_none_for_unknown_business_question() -> None:
    """验证未知问题进入显式符号或通用控制点兜底，不误用领域分析器。"""
    question = "会员头像边框到期后如何续费？"

    selected = ANALYZER_REGISTRY.select(question.casefold(), _concepts(question))

    assert selected is None


def test_registry_accepts_new_analyzer_without_changing_pipeline_contract() -> None:
    """验证新增分析器按声明注册即可参与选择，无需修改诊断主流程。"""
    calls = []

    def matches(question: str, concepts: set[str]) -> bool:
        """判断测试问题是否包含新增插件声明的通用概念。

        参数:
            - `question`：已规范化的用户问题。
            - `concepts`：问题识别出的业务概念名集合。

        Returns:
            同时出现 `avatar` 和 `renewal` 概念时返回真。
        """
        return {"avatar", "renewal"}.issubset(concepts)

    def analyze(lines):
        """返回固定事实，仅用于验证注册表能调用新插件。

        参数:
            - `lines`：证据分析器可读取的源码行；测试插件不需要实际访问文件。

        Returns:
            一条可识别的测试事实。
        """
        calls.append(lines)
        return [{"category": "测试", "statement": "插件已执行", "evidence_refs": []}]

    registry = AnalyzerRegistry([])
    from app.domain.diagnosis.analyzers.registry import AnalyzerSpec

    registry.register(
        AnalyzerSpec(
            analyzer_id="avatar_renewal",
            topic="头像续费",
            matches=matches,
            analyze=analyze,
        ),
    )
    selected = registry.select("头像续费", {"avatar", "renewal"})

    assert selected is not None
    assert selected.analyze([]) == [{"category": "测试", "statement": "插件已执行", "evidence_refs": []}]
    assert calls == [[]]
