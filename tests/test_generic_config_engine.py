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
from app.diagnosis_workflow import _full_config_window_files
from app.domain.diagnosis.analyzers.configuration import (
    _configuration_literal_facts,
    scoped_config_evidence,
)
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

    # 名称合并为“名称=...”，内部 id 不再作为技术字段输出。
    assert "名称=100美元" in resolved
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


def test_config_graph_enumerates_anonymous_list_levels() -> None:
    """验证单行匿名列表元素也能按 1、2、3 分级枚举。

    活动配置常用 `'levels' => [ ['id' => 1, 'limit' => 6000], ... ]` 的写法：
    元素没有数字键，旧解析器会把 id/limit 全部挂在 `levels` 下。这里用三段
    精简配置验证分级、序号收窄和提示性数量都能得到稳定答案。
    """
    file_name = "waky3_miyouactivity/Logic/demo/boot/config.php"
    lines = _lines(
        file_name,
        [
            "<?php",
            "return [",
            "    'levels' => [",
            "        ['id' => 1, 'name' => 'Jungle', 'limit' => 6000,],",
            "        ['id' => 2, 'name' => 'Desert', 'limit' => 20000,],",
            "        ['id' => 3, 'name' => 'Ocean', 'limit' => 100000,],",
            "    ],",
            "];",
        ],
    )

    facts = configuration_enumeration_facts(lines, "六个俱乐部等级的门槛分别是多少？", [])
    text = " ".join(str(item["statement"]) for item in facts)
    assert "6000" in text and "20000" in text and "100000" in text

    ordinal_facts = configuration_enumeration_facts(
        lines,
        "第 3 个等级的门槛是多少？",
        [],
    )
    ordinal_text = " ".join(str(item["statement"]) for item in ordinal_facts)
    assert "100000" in ordinal_text
    assert "6000" not in ordinal_text


def test_config_graph_counts_group_members_and_scalar_list() -> None:
    """验证计数问题按数组成员数量回答，而不是给出无关等级值。

    国旗使用字符串键、监听类型使用多行标量列表；两者都没有数字等级键，
    但都属于“配置了多少个/哪些”的典型问题。
    """
    flag_file = "waky3_miyouactivity/Logic/demo/boot/config.php"
    flag_lines = _lines(
        flag_file,
        [
            "<?php",
            "return [",
            "    'country_code_flag' => [",
            "        'ALG' => 'uc/img/a.png',",
            "        'ARG' => 'uc/img/b.png',",
            "        'BRA' => 'uc/img/c.png',",
            "    ],",
            "];",
        ],
    )
    flag_facts = configuration_enumeration_facts(
        flag_lines,
        "活动配置了多少支国家队的国旗？",
        [],
    )
    flag_text = " ".join(str(item["statement"]) for item in flag_facts)
    assert "country_code_flag" in flag_text
    assert "共 3 项" in flag_text

    type_file = "waky3_miyouactivity/crontab/demo/config.php"
    type_lines = _lines(
        type_file,
        [
            "<?php",
            "return [",
            "    //要监听的游戏发奖类型",
            "    'allow_types' => [",
            "        70002, //塔罗牌",
            "        100101, //小游戏",
            "        100501, //骰子",
            "    ],",
            "];",
        ],
    )
    type_facts = configuration_enumeration_facts(
        type_lines,
        "活动监听哪些游戏类型的发奖？",
        [],
    )
    type_text = " ".join(str(item["statement"]) for item in type_facts)
    assert "allow_types" in type_text
    assert "共 3 项" in type_text


def test_configuration_facts_read_expression_dates_and_inline_comments() -> None:
    """验证表达式日期和行尾注释后的数值都能进入配置结论。

    活动起止时间写成 `modify('2026-09-23 12:00:00')`，奖池公会数写成
    `top_n_guild => 3 //注释`；旧解析器会同时漏掉这两类值。
    """
    file_name = "waky3_miyouactivity/crontab/demo/config.php"
    lines = _lines(
        file_name,
        [
            "<?php",
            "return [",
            "    'stime' => $_dateTime->modify('2026-09-23 12:00:00')->getTimestamp(),",
            "    'etime' => $_dateTime->modify('2026-10-01 23:59:59')->getTimestamp(),",
            "    //奖池配置",
            "    'pool' => [",
            "        'init_coin' => 50000, //初始化金额",
            "        'gift_percent' => 0.001, //每轮送礼比例",
            "        'top_n_guild' => 3 //前N名公会可以瓜分宝箱",
            "    ],",
            "];",
        ],
    )

    date_facts = _configuration_literal_facts(lines, "活动起止时间是什么？")
    date_text = " ".join(str(item["statement"]) for item in date_facts)
    assert "2026-09-23 12:00:00" in date_text
    assert "2026-10-01 23:59:59" in date_text

    pool_facts = _configuration_literal_facts(
        lines,
        "奖池初始金币、送礼进池比例、可瓜分宝箱的公会数分别是多少？",
    )
    pool_text = " ".join(str(item["statement"]) for item in pool_facts)
    assert "init_coin" in pool_text and "50000" in pool_text
    assert "gift_percent" in pool_text and "0.001" in pool_text
    assert "top_n_guild" in pool_text and "3" in pool_text


def test_full_config_window_matches_camel_case_activity_dirs() -> None:
    """验证完整配置窗口对驼峰活动目录生效。

    真实仓库使用 `gameRanking`、`giftWeekStarNew` 这类驼峰目录；选择窗口时
    若只比较原始大小写，后续按小写路径判断会失配，配置只能读到第 1 行。
    """
    evidence = [
        {
            "file": "waky3_miyouactivity/crontab/gameRanking/config.php",
            "line": 1,
            "score": 0.5,
        },
        {
            "file": "waky3_miyouactivity/crontab/gameRanking/gameRankingBoot.php",
            "line": 1,
            "score": 0.4,
        },
    ]
    selected = _full_config_window_files(
        "游戏榜活动监听哪些游戏类型的发奖？",
        evidence,
    )
    assert "waky3_miyouactivity/crontab/gameranking/config.php" in selected


def test_rank_prizes_resolve_reward_definition_by_id() -> None:
    """验证名次表中的奖品 `id` 会解析到同文件奖品定义。

    游戏榜前 10 名只在 `rank_prizes` 中保存 id，金额和金币数量位于 `prizes`；
    只输出 id 无法回答“第 1 名奖励多少美元”。
    """
    file_name = "waky3_miyouactivity/crontab/gameRanking/config.php"
    lines = _lines(
        file_name,
        [
            "<?php",
            "return [",
            "    'rank_prizes' => [",
            "        1 => [['id' => 100]],",
            "        2 => [['id' => 101]],",
            "        3 => [['id' => 102]],",
            "        4 => [['id' => 103]],",
            "        5 => [['id' => 104]],",
            "        6 => [['id' => 105]],",
            "    ],",
            "    'prizes' => [",
            "        100 => ['id' => 100, 'type' => 'dollar', 'num' => 0, 'name' => '100美元'],",
            "        105 => ['id' => 105, 'type' => 'coin', 'num' => 80000, 'name' => '80000金币'],",
            "    ],",
            "];",
        ],
    )

    first = configuration_enumeration_facts(lines, "第 1 名奖励多少美元？", [])
    first_text = " ".join(str(item["statement"]) for item in first)
    assert "100美元" in first_text

    sixth = configuration_enumeration_facts(lines, "第 6 名奖励多少金币？", [])
    sixth_text = " ".join(str(item["statement"]) for item in sixth)
    assert "80000金币" in sixth_text


def test_personal_level_ordinal_is_not_stolen_by_day_rank_table() -> None:
    """验证“第 3 个个人等级”不会被日榜名次数组抢答。

    同一文件同时存在匿名 `levels` 和 `day_rank_top_prizes`，问题明确问等级
    门槛时只能选择含 `limit` 的等级表。
    """
    file_name = "waky3_miyouactivity/crontab/demo/config.php"
    lines = _lines(
        file_name,
        [
            "<?php",
            "return [",
            "    //个人等级奖励",
            "    'levels' => [",
            "        ['id' => 1, 'limit' => 100000, 'name' => 'LV1'],",
            "        ['id' => 2, 'limit' => 550000, 'name' => 'LV2'],",
            "        ['id' => 3, 'limit' => 1500000, 'name' => 'LV3'],",
            "    ],",
            "    //日榜前三奖励",
            "    'day_rank_top_prizes' => [",
            "        1 => ['rank' => 1, 'prizes' => [501]],",
            "        2 => ['rank' => 2, 'prizes' => [502]],",
            "        3 => ['rank' => 3, 'prizes' => [503]],",
            "    ],",
            "];",
        ],
    )
    facts = configuration_enumeration_facts(
        lines,
        "第 3 个个人等级门槛是多少？",
        [],
    )
    text = " ".join(str(item["statement"]) for item in facts)
    assert "1500000" in text
    assert "503" not in text


def test_jump_switch_filters_supported_levels_only() -> None:
    """验证“哪些阶段支持跳转”只返回布尔开关为真的阶段。

    关卡名称与 `is_can_jump_x` 写在同一行数组项中，解析器必须把关闭内层
    `name` 数组后的兄弟键仍归到当前等级，而不是挂到 `name` 下。
    """
    file_name = "waky3_miyouactivity/crontab/lostCivilization/config.php"
    lines = _lines(
        file_name,
        [
            "<?php",
            "return [",
            "    //任务送礼阶段奖励",
            "    'levels' => [",
            "        ['id' => 1, 'limit' => 30000, \"name\" => [",
            "            'zh' => '巴比伦空中花园',",
            "        ], \"is_can_jump_x\" => 0, 'prizes' => [1001]],",
            "        ['id' => 2, 'limit' => 90000, \"name\" => [",
            "            'zh' => '泰姬陵',",
            "        ], \"is_can_jump_x\" => 0, 'prizes' => [2001]],",
            "        ['id' => 3, 'limit' => 190000, \"name\" => [",
            "            'zh' => '佩特拉古城',",
            "        ], \"is_can_jump_x\" => 1, 'prizes' => [3001]],",
            "        ['id' => 4, 'limit' => 350000, \"name\" => [",
            "            'zh' => '长城',",
            "        ], \"is_can_jump_x\" => 1, 'prizes' => [4001]],",
            "        ['id' => 5, 'limit' => 580000, \"name\" => [",
            "            'zh' => '吴哥窟',",
            "        ], \"is_can_jump_x\" => 1, 'prizes' => [5001]],",
            "    ],",
            "];",
        ],
    )
    facts = configuration_enumeration_facts(
        lines,
        "哪些阶段支持跳转 X 平台？",
        [],
    )
    text = " ".join(str(item["statement"]) for item in facts)
    assert "佩特拉古城" in text and "长城" in text and "吴哥窟" in text
    assert "巴比伦空中花园" not in text and "泰姬陵" not in text


def test_new_version_hint_selects_new_activity_directory() -> None:
    """验证“新版”能按目录后缀 `new` 选择活动，而不是旧版配置。"""
    old_file = "waky3_miyouactivity/crontab/giftWeekStar/config.php"
    new_file = "waky3_miyouactivity/Logic/giftWeekStarNew/boot/config.php"
    old_lines = _lines(old_file, ["<?php", "return ['year' => 2022];"])
    new_lines = _lines(new_file, ["<?php", "return ['year' => 2026];"])
    facts = _configuration_literal_facts(
        [*old_lines, *new_lines],
        "礼物周星新版配置里的 year 是什么？",
    )
    text = " ".join(str(item["statement"]) for item in facts)
    assert "2026" in text
    assert "2022" not in text


def test_activity_display_terms_disambiguate_chinese_marketing_names() -> None:
    """验证中文营销名只出现在 pushmsg/注释时也能选中正确活动。"""
    hunt_file = "waky3_miyouactivity/crontab/guildTreasureHunt/config.php"
    monster_file = "waky3_miyouactivity/crontab/guildVsMonsters/config.php"
    hunt_lines = _lines(
        hunt_file,
        [
            "<?php",
            "return [",
            "    'pushmsg' => ['levels_up_prize' => '【公会夺宝赛】恭喜你到达新的寻宝点'],",
            "];",
        ],
    )
    monster_lines = _lines(
        monster_file,
        [
            "<?php",
            "return [",
            "    'pushmsg' => ['level' => '【公会怪物】挑战失败'],",
            "];",
        ],
    )
    hunt_boot = _lines(
        "waky3_miyouactivity/crontab/guildTreasureHunt/guildTreasureHuntBoot.php",
        ["<?php", "// 公会寻宝入口"],
    )
    monster_boot = _lines(
        "waky3_miyouactivity/crontab/guildVsMonsters/guildVsMonstersBoot.php",
        ["<?php", "// 公会怪物入口"],
    )
    selected = scoped_config_evidence(
        [*hunt_lines, *hunt_boot, *monster_lines, *monster_boot],
        "公会寻宝设置了哪几个送礼阶段？",
        [],
    )
    assert selected is not None
    assert {item.file for item in selected} == {hunt_file, hunt_boot[0].file}


def _constant_level_config() -> list[str]:
    """构造用类常量做等级键的门槛配置片段。

    Returns:
        仿照贵族配置写的 PHP 源码行：等级键是 `Model\\UserNobleModel::LEVEL_*`
        常量，门槛写成 `score => getByTestMode(测试, 正式)`，并混入 id/gid/step
        和多语言名称等技术字段，用于验证可读渲染与门槛提取。
    """
    return [
        "<?php",
        "return [",
        "    'level' => [",
        "        \\Model\\UserNobleModel::LEVEL_RANGER => [",
        "            'id' => 1,",
        "            'gid' => 1001,",
        "            'step' => 1,",
        "            'name' => [",
        "                'en' => 'Ranger',",
        "                'in' => 'Ranger',",
        "                'zh' => '游侠',",
        "            ],",
        "            'score' => getByTestMode(1000, 1000000),",
        "        ],",
        "        \\Model\\UserNobleModel::LEVEL_KNIGHT => [",
        "            'id' => 2,",
        "            'gid' => 1002,",
        "            'name' => [",
        "                'zh' => '骑士',",
        "            ],",
        "            'score' => getByTestMode(2000, 2000000),",
        "        ],",
        "    ],",
        "];",
    ]


def test_constant_key_level_table_answers_threshold_readably() -> None:
    """验证常量键等级表能逐级回答门槛，并隐藏技术字段。"""
    file_name = "waky3_miyouphp/php/config/noble.php"
    lines = _lines(file_name, _constant_level_config())

    facts = configuration_enumeration_facts(
        lines,
        "vip升级活动每个等级的门槛是多少？罗列出每个等级的门槛",
        [],
    )

    assert len(facts) == 2
    text = "\n".join(str(item["statement"]) for item in facts)
    assert "等级 1（游侠）" in text and "等级 2（骑士）" in text
    assert "1000" in text and "1000000" in text
    assert "2000" in text and "2000000" in text
    # 技术字段和多语言重复名称不应进入结论正文。
    assert "id=" not in text and "gid=" not in text and "step=" not in text
    assert "Ranger" not in text


def test_threshold_question_without_level_config_reports_gap() -> None:
    """验证没有等级配置时门槛问题明确报告缺口，而不是回退到其他数值。"""
    file_name = "waky3_miyouactivity/crontab/demo/config.php"
    contents = ["<?php", "return [", "    'title' => '普通活动',", "];"]
    context = "\n".join(
        f"{index}: {content}" for index, content in enumerate(contents, start=1)
    )
    result = {
        "evidence": [
            {"file": file_name, "line": 1, "content": "<?php", "context": context}
        ],
        "retrieval_plan": {"retrieval_mode": "semantic_discovery"},
    }

    findings = build_deterministic_findings(
        "vip升级活动每个等级的门槛是多少？",
        result,
    )

    assert findings["gap"] is True
    assert findings["facts"] == []
    assert findings["conclusion"] == ""
    assert any("等级门槛配置" in item for item in findings["uncertainties"])


def test_rank_reward_question_keeps_config_beyond_twelve_candidates() -> None:
    """验证名次奖励题的完整配置窗口不会被十二个噪声配置挤掉。

    真实检索会同时带入多个活动的 `config.php`，若按普通上限 12 截断，
    `gameRanking/config.php` 可能因为只命中文件头而排在噪声配置之后。
    """
    evidence = [
        {
            "file": f"waky3_miyouactivity/crontab/gameRank{a}aa/config.php",
            "line": 1,
        }
        for a in ("0", "0b", "0c", "0d", "0e", "0f", "0g", "0h", "0i", "0j", "0k", "0l", "0m", "0n", "0o")
    ]
    evidence.append(
        {
            "file": "waky3_miyouactivity/crontab/gameRanking/config.php",
            "line": 1,
        }
    )
    selected = _full_config_window_files(
        "游戏榜第 6 名奖励多少金币？",
        evidence,
        ["GameRankPrizeService"],
    )
    assert "waky3_miyouactivity/crontab/gameranking/config.php" in selected


def test_top_three_rank_question_only_returns_first_three() -> None:
    """验证“前 3 名”只输出前三名，并使用名次语义而不是等级语义。"""
    file_name = "waky3_miyouactivity/crontab/gameRanking/config.php"
    lines = _lines(
        file_name,
        [
            "<?php",
            "return [",
            "    'rank_prizes' => [",
            "        1 => [['id' => 100]],",
            "        2 => [['id' => 101]],",
            "        3 => [['id' => 102]],",
            "        4 => [['id' => 103]],",
            "        5 => [['id' => 104]],",
            "        6 => [['id' => 105]],",
            "    ],",
            "    'prizes' => [",
            "        100 => ['id' => 100, 'type' => 'dollar', 'num' => 0, 'name' => '100美元'],",
            "        101 => ['id' => 101, 'type' => 'dollar', 'num' => 0, 'name' => '60美元'],",
            "        102 => ['id' => 102, 'type' => 'dollar', 'num' => 0, 'name' => '40美元'],",
            "        103 => ['id' => 103, 'type' => 'dollar', 'num' => 0, 'name' => '20美元'],",
            "        104 => ['id' => 104, 'type' => 'dollar', 'num' => 0, 'name' => '10美元'],",
            "        105 => ['id' => 105, 'type' => 'coin', 'num' => 80000, 'name' => '80000金币'],",
            "    ],",
            "];",
        ],
    )
    facts = configuration_enumeration_facts(lines, "游戏榜前 3 名分别奖励什么？", [])
    assert len(facts) == 3
    text = " ".join(str(item["statement"]) for item in facts)
    assert "名次 1" in text and "100美元" in text
    assert "名次 3" in text and "40美元" in text
    assert "80000金币" not in text
