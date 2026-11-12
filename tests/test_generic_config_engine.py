"""通用配置图、问题类型路由和运行时术语扩展的回归测试。

这些测试覆盖“新增业务不需要新增映射”的三个通用能力：配置结构图按等级枚举、
问题类型路由选择解释器，以及模型候选词必须通过真实索引校验。测试只使用精简
配置片段和内存索引，不访问真实仓库、数据库或模型。
"""

import json
from typing import Optional

import pytest

from app.business_analysis import build_deterministic_findings
from app.domain.diagnosis.analyzers.common import EvidenceLine
from app.domain.diagnosis.config_graph import (
    configuration_enumeration_facts,
    configuration_env_value_facts,
    parse_config_graph,
)
from app.question_type import QuestionType, classify_question
from app.settings import Settings
from app.term_expansion import (
    _extract_candidate_terms,
    _validated_terms,
    _validation_tokens,
    expand_question_terms,
)


def _lines(file_name: str, contents: list[str]) -> list[EvidenceLine]:
    """构造带连续行号的源码证据行。

    参数:
        - `file_name`：项目内相对路径，类型为 `str`。
        - `contents`：从第 1 行开始的源码文本，类型为 `list[str]`。

    Returns:
        行号从 1 递增的 `EvidenceLine` 列表，供配置图和业务分析直接消费。
    """
    return [
        EvidenceLine(
            file=file_name,
            line=index,
            content=content,
            ref=f"{file_name}:{index}",
        )
        for index, content in enumerate(contents, start=1)
    ]


def _generic_activity_config() -> list[str]:
    """构造一个与具体项目无关的等级奖励配置片段。

    Returns:
        仿照 PHP 活动配置写法的源码行。每个等级同时包含测试和正式分支，
        用于验证通用枚举不会把正式配置冒充测试配置。
    """
    return [
        "<?php",
        "return [",
        "    'activity' => [",
        "        'reward' => [",
        "            1 => getByTestMode(",
        "                [",
        "                    ['type' => RewardModel::TYPE_GOLD, 'num' => 10],",
        "                ],",
        "                //正式配置",
        "                [",
        "                    ['type' => RewardModel::TYPE_GOLD, 'num' => 20],",
        "                ],",
        "            ),",
        "            2 => getByTestMode(",
        "                [",
        "                    ['type' => RewardModel::TYPE_GOLD, 'num' => 30],",
        "                ],",
        "                //正式配置",
        "                [",
        "                    ['type' => RewardModel::TYPE_GOLD, 'num' => 40],",
        "                ],",
        "            ),",
        "        ],",
        "        'callback_url' => 'https://secret.example.com/hook',",
        "    ],",
        "];",
    ]


def test_config_graph_enumerates_repeated_levels_without_business_mapping() -> None:
    """验证未知活动的重复等级配置可以被通用枚举逐级回答。"""
    file_name = "waky3_miyouactivity/crontab/newActivity/config.php"
    lines = _lines(file_name, _generic_activity_config())

    facts = configuration_enumeration_facts(
        lines,
        "测试环境中每个等级的活动奖励是什么？",
    )

    assert len(facts) == 2
    statements = " ".join(str(item["statement"]) for item in facts)
    assert "等级 1" in statements and "num=10" in statements
    assert "等级 2" in statements and "num=30" in statements
    # 正式分支的数值不能出现在测试环境答案中。
    assert "num=20" not in statements
    assert "num=40" not in statements


def test_config_graph_drops_sensitive_string_values() -> None:
    """验证配置图不会把 URL、密钥等字符串写入事实。"""
    file_name = "waky3_miyouactivity/crontab/newActivity/config.php"
    graph = parse_config_graph(_lines(file_name, _generic_activity_config()))

    rendered = " ".join(
        f"{entry.path}:{entry.value_text}" for entry in graph.entries
    )

    assert "secret" not in rendered
    assert "callback_url" not in rendered
    assert "RewardModel::TYPE_GOLD" in rendered


def test_business_analysis_routes_new_activity_to_generic_enumeration() -> None:
    """验证未注册分析器的新活动问题仍能通过配置图给出逐级结论。"""
    config_file = "waky3_miyouactivity/crontab/newActivity/config.php"
    boot_file = "waky3_miyouactivity/crontab/newActivity/newActivityBoot.php"
    contents = _generic_activity_config()
    context = "\n".join(
        f"{index}: {content}" for index, content in enumerate(contents, start=1)
    )
    result = {
        "evidence": [
            {
                "file": config_file,
                "line": 1,
                "content": "<?php",
                "context": context,
            },
            {
                "file": boot_file,
                "line": 1,
                "content": "<?php",
                "context": "1: <?php",
            },
        ],
        "retrieval_plan": {"retrieval_mode": "semantic_discovery"},
    }

    findings = build_deterministic_findings(
        "测试环境中每个等级的活动奖励是什么？",
        result,
    )

    assert "按等级确认" in findings["conclusion"]
    assert "等级 1" in findings["conclusion"]
    assert {item["category"] for item in findings["facts"]} == {
        "reward等级1",
        "reward等级2",
    }


def test_config_graph_uses_expanded_terms_to_scope_chinese_activity() -> None:
    """验证中文活动名无法直接匹配目录时，扩展词可定位真实活动配置。"""
    game_file = "waky3_miyouactivity/crontab/gameRanking/config.php"
    other_file = "waky3_miyouactivity/crontab/patronChargeRankingMonth/config.php"
    game_lines = _lines(game_file, _generic_activity_config())
    other_lines = [
        EvidenceLine(
            file=other_file,
            line=item.line,
            content=item.content,
            ref=f"{other_file}:{item.line}",
        )
        for item in _lines(other_file, _generic_activity_config())
    ]

    facts = configuration_enumeration_facts(
        [*game_lines, *other_lines],
        "游戏榜前 10 名分别奖励什么？",
        ["game_ranking", "Ranking"],
    )

    assert facts
    assert all(
        ref.startswith(game_file)
        for fact in facts
        for ref in fact["evidence_refs"]
    )


def test_config_graph_resolves_numeric_reference_to_prize_definition() -> None:
    """验证档位表引用奖品 id 时能通用解析到奖品名称，而不是只回答 id。"""
    file_name = "waky3_miyouactivity/crontab/gameRanking/config.php"
    lines = _lines(
        file_name,
        [
            "<?php",
            "$globalConfig = [",
            "    'sub_config' => [",
            "        [",
            "            'rank_prizes' => [",
            "                1 => [['id' => 100,]],",
            "                2 => [['id' => 101,]],",
            "            ],",
            "            'prizes' => [",
            "                100 => ['id' => 100, 'num' => 0, 'name' => '100美元',],",
            "                101 => ['id' => 101, 'num' => 0, 'name' => '60美元',],",
            "            ],",
            "        ],",
            "    ],",
            "];",
        ],
    )
    graph = parse_config_graph(lines)
    reference = next(
        entry
        for entry in graph.entries
        if entry.key == "id"
        and "rank_prizes" in entry.path
        and entry.value_text == "100"
    )

    resolved = graph._referenced_text(graph._resolve_reference(reference))

    assert "name=100美元" in resolved
    assert "id=100" not in resolved


def test_config_graph_reads_env_values_and_masks_sensitive_fields() -> None:
    """验证商户号等环境配置可回答，密钥和地址值不会被明文输出。"""
    file_name = "waky3_pay-api/myapp/config/fupay.php"
    lines = _lines(
        file_name,
        [
            "<?php",
            "//商户id  S204",
            "'merchantId' => getByTestMode('', 'alvV4011fAdY'),",
            "//秘钥",
            "'secretKey' => getByTestMode('test-secret', 'prod-secret'),",
            "//回调地址",
            "'callbackUrl' => getByTestMode('http://test/notify', 'https://prod/notify'),",
        ],
    )

    merchant_facts = configuration_env_value_facts(
        lines,
        "fupay 的商户号在配置里怎么写的？",
    )
    merchant_text = " ".join(
        str(item["statement"]) for item in merchant_facts
    )
    assert "merchantId" in merchant_text
    assert "alvV4011fAdY" in merchant_text
    assert "测试环境值为 空" in merchant_text

    secret_facts = configuration_env_value_facts(
        lines,
        "fupay 的 secretKey 和 callbackUrl 在配置里怎么写的？",
    )
    secret_text = " ".join(str(item["statement"]) for item in secret_facts)
    assert "敏感值不展示" in secret_text
    assert "地址或特殊值不展示" in secret_text
    assert "test-secret" not in secret_text
    assert "http://test/notify" not in secret_text


def test_question_type_routes_generic_question_shapes() -> None:
    """验证问题类型路由不依赖具体项目、类名或完整问句模板。"""
    cases = {
        "每个VIP等级附赠的奖品是哪些？": QuestionType.CONFIG_ENUMERATION,
        "活动配置里兑换比例是多少？": QuestionType.CONFIG_LOOKUP,
        "这个功能哪个版本开始上线？": QuestionType.HISTORY,
        "前端钱包页面钻石档位怎么展示？": QuestionType.FRONTEND_DISPLAY,
        "FupayService::pay 的调用链是什么？": QuestionType.CALL_CHAIN,
        "注册流程有哪些校验条件？": QuestionType.FLOW_CONTROL,
        "会员头像边框到期后如何续费？": QuestionType.UNSUPPORTED,
    }

    for question, expected in cases.items():
        assert classify_question(question, set()) == expected, question


def _fake_project_index() -> dict:
    """构造用于术语校验的最小项目索引。

    Returns:
        包含符号、配置和文件路径的索引字典；其中 `NewNobleLv`、
        `diamond_to_usd` 是真实存在的词，`ImaginaryModule` 不存在。
    """
    return {
        "symbols": [
            {
                "symbol_name": "NewNobleLv::getPrize",
                "symbol_type": "method",
                "file": "waky3_miyouphp/php/config/newNobleLv.php",
                "line": 10,
            }
        ],
        "configs": [
            {
                "key": "anchor.diamond_to_usd",
                "file": "waky3_miyouphp/php/config/anchor.php",
                "line": 20,
            }
        ],
        "references": [],
        "sql": [],
    }


def test_term_validation_keeps_only_real_index_tokens() -> None:
    """验证术语校验只保留真实类名、配置键或文件名干。"""
    tokens = _validation_tokens(_fake_project_index())

    kept = _validated_terms(
        ["NewNobleLv", "diamond_to_usd", "ImaginaryModule"],
        tokens,
        limit=8,
    )

    assert kept == ["NewNobleLv", "diamond_to_usd"]


def test_term_validation_maps_fuzzy_candidate_to_real_directory_token() -> None:
    """验证模型组合词可映射到真实活动目录词，但编造词仍被拒绝。"""
    tokens = _validation_tokens(
        {
            "symbols": [],
            "configs": [
                {
                    "key": "game.rank.prize",
                    "file": "waky3_miyouactivity/crontab/gameRanking/config.php",
                    "line": 1,
                }
            ],
            "references": [],
            "sql": [],
        }
    )

    kept = _validated_terms(
        ["GameRankReward", "ImaginaryModule"],
        tokens,
        limit=8,
    )

    assert kept == ["gameRanking"]


def test_term_extraction_rejects_generic_and_question_terms() -> None:
    """验证模型正文解析会过滤通用词和问题原文词。"""
    terms = _extract_candidate_terms(
        json.dumps({"terms": ["NewNobleLv", "config", "Redis", "myyquestion"]}),
        excluded={"myyquestion"},
    )

    assert terms == ["NewNobleLv"]


class _FakeRedis:
    """记录缓存读写的最小异步 Redis 替身。"""

    def __init__(self, cached: Optional[str] = None) -> None:
        """初始化替身。

        参数:
            - `cached`：`get` 要返回的缓存正文，类型为 `str | None`。
        """
        self.cached = cached
        self.written: Optional[tuple[str, int, str]] = None
        self.closed = False

    async def get(self, key: str):
        """返回预设缓存值，忽略键名。

        参数:
            - `key`：缓存键，类型为 `str`。

        Returns:
            构造时传入的缓存正文或 `None`。
        """
        return self.cached

    async def setex(self, key: str, ttl: int, value: str):
        """记录写入内容，供测试断言缓存已保存。

        参数:
            - `key`：缓存键，类型为 `str`。
            - `ttl`：缓存秒数，类型为 `int`。
            - `value`：缓存正文，类型为 `str`。
        """
        self.written = (key, ttl, value)

    async def aclose(self):
        """标记替身已关闭；本测试不真正连接 Redis。"""
        self.closed = True


def _llm_settings() -> Settings:
    """构造启用术语扩展的最小配置。

    Returns:
        仅包含模型地址、模型标识和密钥的配置对象；密钥只用于请求头，不会发送给
        测试替身以外的任何服务。
    """
    return Settings(
        llm_enabled=True,
        llm_term_expansion_enabled=True,
        llm_model="test-model",
        llm_api_key="test-key",
    )


@pytest.mark.asyncio
async def test_term_expansion_is_disabled_by_default() -> None:
    """验证 LLM 未启用时不发起请求并明确返回禁用状态。"""
    result = await expand_question_terms(
        "新活动每个等级奖励是什么？",
        project="waky3",
        project_index=_fake_project_index(),
        settings=Settings(llm_enabled=False, llm_term_expansion_enabled=False),
        redis_client=_FakeRedis(),
    )

    assert result["status"] == "disabled"
    assert result["terms"] == []


@pytest.mark.asyncio
async def test_term_expansion_uses_cached_terms() -> None:
    """验证命中缓存时直接返回已校验词，不重复调用模型。"""
    redis_client = _FakeRedis(cached=json.dumps({"terms": ["NewNobleLv"]}))

    result = await expand_question_terms(
        "新活动每个等级奖励是什么？",
        project="waky3",
        project_index=_fake_project_index(),
        settings=_llm_settings(),
        redis_client=redis_client,
    )

    assert result["status"] == "cache_hit"
    assert result["terms"] == ["NewNobleLv"]


@pytest.mark.asyncio
async def test_term_expansion_degrades_when_model_fails(monkeypatch) -> None:
    """验证模型故障时静默降级为空词表，不影响诊断主流程。

    参数:
        - `monkeypatch`：pytest 补丁工具，用于替换模型请求函数。
    """

    async def fail_request(settings, question, *, timeout_seconds):
        """模拟模型请求异常。

        参数:
            - `settings`：应用配置，测试中未使用。
            - `question`：用户问题，测试中未使用。
            - `timeout_seconds`：请求超时秒数，测试中未使用。

        Raises:
            RuntimeError：始终抛出，用于验证调用方降级。
        """
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(
        "app.term_expansion._request_expansion",
        fail_request,
    )
    redis_client = _FakeRedis()

    result = await expand_question_terms(
        "新活动每个等级奖励是什么？",
        project="waky3",
        project_index=_fake_project_index(),
        settings=_llm_settings(),
        redis_client=redis_client,
    )

    assert result["status"] == "degraded"
    assert result["terms"] == []
    assert "术语扩展降级" in result["reason"]


@pytest.mark.asyncio
async def test_term_expansion_filters_model_candidates_and_caches(monkeypatch) -> None:
    """验证模型候选词经真实性校验后写入缓存并返回。

    参数:
        - `monkeypatch`：pytest 补丁工具，用于替换模型请求函数。
    """

    async def fake_request(settings, question, *, timeout_seconds):
        """返回包含真实词和不存在的词的模型正文。

        参数:
            - `settings`：应用配置，测试中未使用。
            - `question`：用户问题，测试中未使用。
            - `timeout_seconds`：请求超时秒数，测试中未使用。

        Returns:
            严格 JSON 正文，包含一个真实词和一个不存在词。
        """
        return json.dumps({"terms": ["NewNobleLv", "ImaginaryModule"]})

    monkeypatch.setattr(
        "app.term_expansion._request_expansion",
        fake_request,
    )
    redis_client = _FakeRedis()

    result = await expand_question_terms(
        "新活动每个等级奖励是什么？",
        project="waky3",
        project_index=_fake_project_index(),
        settings=_llm_settings(),
        redis_client=redis_client,
    )

    assert result["status"] == "succeeded"
    assert result["terms"] == ["NewNobleLv"]
    assert result["rejected_terms"] == ["ImaginaryModule"]
    assert redis_client.written is not None
    assert json.loads(redis_client.written[2]) == {"terms": ["NewNobleLv"]}
