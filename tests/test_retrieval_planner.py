"""通用检索的概念排序、词到符号和配置反向扩展测试。"""

from app.retrieval_planner import (
    assess_retrieval_support,
    config_definition_terms,
    discover_related_queries,
    rank_discovery_hits,
    select_question_config_keys,
    select_semantic_hits,
)


def test_config_definition_terms_are_derived_from_discovered_keys() -> None:
    """验证点号配置键会生成末级定义检索词，且不携带项目专属符号模板。"""
    terms = config_definition_terms(
        [
            "anchor.diamond_to_usd",
            "coins_merchant.ar.min_money",
            "dynamic.",
            "invalid key",
            "anchor.diamond_to_usd",
        ],
        limit=8,
    )

    assert terms == ["diamond_to_usd", "min_money"]


def test_question_concepts_select_specific_config_keys_from_full_index() -> None:
    """验证设备注册数量问题会从全量配置读取中选出组合覆盖最高的键。"""
    index = {
        "symbols": [],
        "references": [],
        "configs": [
            {"key": "sms.countryList", "file": "php/action/Login.php", "line": 10},
            {"key": "login.is_limit_cn_ip_register", "file": "php/action/Login.php", "line": 18},
            {
                "key": "device.device_user_count_limit",
                "file": "php/model/UserModel.php",
                "line": 31,
            },
        ],
        "sql": [],
    }

    selected = select_question_config_keys(
        index,
        ["device", "registration", "account", "count", "limit"],
        limit=2,
    )

    assert selected[0] == "device.device_user_count_limit"


def test_question_config_selection_handles_english_word_forms() -> None:
    """验证配置键的复数、名词化词形不会漏掉问题中的基础业务概念。"""
    index = {
        "symbols": [],
        "references": [],
        "configs": [
            {
                "key": "diamonds.diamond_exchange",
                "file": "php/config/diamonds.php",
                "line": 9,
            },
            {
                "key": "anchor.joinBDUserRegTimeLimit",
                "file": "php/action/BDCenterControl.php",
                "line": 377,
            },
            {
                "key": "diamonds.withdrawal_limits.official.daily_limit",
                "file": "php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php",
                "line": 336,
            },
        ],
        "sql": [],
    }

    selected = select_question_config_keys(
        index,
        ["diamond", "withdraw", "official", "limit", "exchange"],
        limit=3,
    )

    assert selected[0] == "diamonds.withdrawal_limits.official.daily_limit"
    assert "diamonds.diamond_exchange" in selected


def test_retrieval_support_accepts_direct_business_anchor() -> None:
    """验证生产源码中的四字业务锚点可以继续扩展调用链。"""
    support = assess_retrieval_support(
        [
            {
                "file": "php/services/Anchor/AdvanceServices.php",
                "line": 576,
                "content": "//主播钻石提现",
                "matched_queries": ["钻石提现"],
                "matched_concepts": ["diamond", "withdrawal"],
                "retrieval_stage": "business_anchor",
            }
        ],
        {"diamond", "withdrawal", "limit", "fee"},
    )

    assert support["grounded"] is True
    assert support["level"] == "business_anchor"
    assert support["direct_anchor_count"] == 1


def test_retrieval_support_rejects_generic_four_character_action_anchor() -> None:
    """验证“每日兑换”等通用动作锚点不能证明未知业务实体存在。"""
    support = assess_retrieval_support(
        [
            {
                "file": "php/action/FamilyJourneyControl.php",
                "line": 1039,
                "content": "//每日兑换次数统计",
                "matched_queries": ["每日兑换", "每日"],
                "matched_concepts": ["daily", "exchange"],
                "retrieval_stage": "business_anchor",
            }
        ],
        {"daily", "limit", "exchange"},
    )

    assert support["grounded"] is False
    assert support["direct_anchor_count"] == 0
    assert support["rejected_generic_anchor_count"] == 1


def test_retrieval_support_stops_when_only_generic_concepts_cooccur() -> None:
    """验证“用户 + 阈值”不能冒充风控冻结阈值并触发全仓扩展。"""
    support = assess_retrieval_support(
        [
            {
                "file": "php/legacy.php",
                "line": 20,
                "content": "threshold for user",
                "matched_queries": ["user", "threshold"],
                "matched_concepts": ["user", "limit"],
                "retrieval_stage": "concept_expansion",
            }
        ],
        {"user", "risk", "freeze", "limit"},
    )

    assert support["grounded"] is False
    assert support["level"] == "insufficient"
    assert support["best_concept_coverage"] == 2
    assert support["best_domain_coverage"] == 0


def test_retrieval_support_accepts_extensible_domain_concept_cooccurrence() -> None:
    """验证未配置专用词条的领域只要概念同行共现仍可继续源码验证。"""
    support = assess_retrieval_support(
        [
            {
                "file": "php/RiskControl.php",
                "line": 20,
                "content": "if ($riskScore >= $riskThreshold) { return false; }",
                "matched_queries": ["risk", "threshold"],
                "matched_concepts": ["risk", "limit"],
                "retrieval_stage": "concept_expansion",
            }
        ],
        {"risk", "limit"},
    )

    assert support["grounded"] is True
    assert support["level"] == "semantic_concepts"


def test_retrieval_support_requires_registration_when_device_and_count_are_present() -> None:
    """验证“同一设备 + 数量”不能替代设备注册业务对象。"""
    support = assess_retrieval_support(
        [
            {
                "file": "php/model/TaskModel.php",
                "line": 574,
                "content": "// 是否同一设备超过X个uid",
                "matched_queries": ["同一设备", "设备"],
                "matched_concepts": ["device", "limit", "user", "count"],
                "retrieval_stage": "business_anchor",
            }
        ],
        {"device", "registration", "count", "limit", "user"},
    )

    assert support["grounded"] is False
    assert support["required_domain_coverage"] == 2


def test_retrieval_support_accepts_device_registration_cooccurrence() -> None:
    """验证设备与注册动作在同一源码窗口共现后可进入证据补全。"""
    support = assess_retrieval_support(
        [
            {
                "file": "php/api/sendCode.php",
                "line": 52,
                "content": "device registered users exceeds the limit",
                "matched_queries": ["device", "register", "count", "limit"],
                "matched_concepts": ["device", "registration", "count", "limit", "user"],
                "retrieval_stage": "concept_expansion",
            }
        ],
        {"device", "registration", "count", "limit", "user"},
    )

    assert support["grounded"] is True
    assert support["best_domain_coverage"] == 2


def test_rank_discovery_hits_prefers_business_source_over_language_pack() -> None:
    """验证同等中文命中的生产代码和文档优先于语言包与测试。"""
    hits = [
        {
            "file": "php/lang/zh/risk.php",
            "line": 10,
            "content": "风控冻结",
            "matched_queries": ["风控冻结"],
        },
        {
            "file": "php/RiskControl.php",
            "line": 20,
            "content": "// 用户风控冻结阈值",
            "matched_queries": ["风控冻结", "冻结阈值"],
        },
    ]
    ranked = rank_discovery_hits(hits)
    assert ranked[0]["file"] == "php/RiskControl.php"


def test_rank_discovery_hits_prefers_activity_crontab_over_admin_and_reports() -> None:
    """验证生产活动脚本高于后台页面和旧报告，避免实现文件被入口文件挤出。"""
    hits = [
        {
            "file": "reports/ceremony2024-spaceship.md",
            "line": 10,
            "content": "商品碎片分配",
            "matched_queries": ["商品碎片", "分配"],
        },
        {
            "file": "waky6_miyouadmin/activity/SpaceshipPage.php",
            "line": 20,
            "content": "商品碎片分配",
            "matched_queries": ["商品碎片", "分配"],
        },
        {
            "file": "waky6_miyouactivity/crontab/ceremony2025GoldOwner/SpaceshipService.php",
            "line": 30,
            "content": "商品碎片分配",
            "matched_queries": ["商品碎片", "分配"],
        },
    ]
    ranked = rank_discovery_hits(hits)
    assert ranked[0]["file"].endswith("ceremony2025GoldOwner/SpaceshipService.php")


def test_rank_discovery_hits_prefers_multi_concept_coverage() -> None:
    """验证同时覆盖多个业务概念的行胜过只命中单字泛词的大文件。"""
    hits = [
        {
            "file": "waky4_miyouadmin/activity/DisplayPage.php",
            "line": 10,
            "content": "展示",
            "matched_queries": ["展示"],
            "matched_concepts": ["display"],
        },
        {
            "file": "waky4_miyouphp/php/action/DiamondControl.php",
            "line": 20,
            "content": "// 前端展示主播钻石档位",
            "matched_queries": ["展示"],
            "matched_concepts": ["display", "diamond", "anchor", "gear"],
        },
    ]

    ranked = rank_discovery_hits(hits)

    assert ranked[0]["file"].endswith("DiamondControl.php")


def test_rank_discovery_hits_preserves_direct_business_anchor_over_concepts() -> None:
    """验证四字业务锚点不会被两个通用概念词的高覆盖命中挤出候选窗口。"""
    config = {
        "file": "php/ceremony/config.php",
        "line": 173,
        "content": "//商品碎片数",
        "matched_queries": ["商品碎片", "碎片"],
        "matched_concepts": ["fragment"],
        "retrieval_stage": "business_anchor",
    }
    generic = {
        "file": "php/ceremony/service.php",
        "line": 175,
        "content": "fragment prize",
        "matched_queries": ["fragment", "prize"],
        "matched_concepts": ["fragment", "prize"],
        "retrieval_stage": "concept_expansion",
    }

    ranked = rank_discovery_hits([generic, config])

    assert ranked[0] == config


def test_rank_discovery_hits_adds_same_repository_without_hard_filtering() -> None:
    """验证首选子仓库只获得权重，其他子仓的真实候选仍保留。"""
    hits = [
        {
            "file": "waky4_miyouadmin/php/action/DiamondPage.php",
            "line": 10,
            "content": "钻石展示",
            "matched_queries": ["钻石展示"],
            "matched_concepts": ["diamond", "display"],
        },
        {
            "file": "waky4_miyouphp/php/action/DiamondControl.php",
            "line": 20,
            "content": "钻石展示",
            "matched_queries": ["钻石展示"],
            "matched_concepts": ["diamond", "display"],
        },
    ]

    ranked = rank_discovery_hits(hits, preferred_repository="waky4_miyouphp")

    assert ranked[0]["file"].startswith("waky4_miyouphp/")
    assert {item["file"] for item in ranked} == {item["file"] for item in hits}


def test_select_semantic_hits_keeps_each_concept_representative() -> None:
    """验证聚焦命中保留每个概念的代表行，并降低单一泛词候选的优先级。"""
    hits = [
        {
            "file": "php/DiamondControl.php",
            "line": 10,
            "content": "前端展示钻石",
            "matched_concepts": ["display", "diamond"],
        },
        {
            "file": "php/AnchorModel.php",
            "line": 30,
            "content": "主播身份",
            "matched_concepts": ["anchor"],
        },
        {
            "file": "php/Page.php",
            "line": 50,
            "content": "展示",
            "matched_concepts": ["display"],
        },
    ]

    selected = select_semantic_hits(hits, limit=3)

    assert selected[0]["file"] == "php/DiamondControl.php"
    assert {item["file"] for item in selected} == {
        "php/DiamondControl.php",
        "php/AnchorModel.php",
        "php/Page.php",
    }


def test_select_semantic_hits_preserves_verified_explicit_symbol() -> None:
    """验证没有中文注释的显式类声明不会被概念覆盖阈值挤出候选。"""
    explicit = {
        "file": "php/services/UserScoreService.php",
        "line": 12,
        "content": "class UserScoreService extends ServiceHelper",
        "matched_queries": ["UserScoreService"],
        "matched_concepts": [],
        "retrieval_stage": "explicit_symbol",
    }
    generic = {
        "file": "php/services/OtherService.php",
        "line": 20,
        "content": "user score limit",
        "matched_queries": ["user", "score", "limit"],
        "matched_concepts": ["user", "limit"],
        "retrieval_stage": "concept_expansion",
    }

    selected = select_semantic_hits([explicit, generic], limit=1)

    assert selected == [explicit]


def test_select_semantic_hits_reserves_direct_business_anchor() -> None:
    """验证通用英文概念的高覆盖行不能挤掉问题原句直接命中的业务注释。"""
    anchor = {
        "file": "php/services/Anchor/AdvanceServices.php",
        "line": 576,
        "content": "//主播钻石提现",
        "matched_queries": ["钻石提现"],
        "matched_concepts": ["diamond", "withdrawal"],
        "retrieval_stage": "business_anchor",
    }
    generic_hits = [
        {
            "file": f"php/domain/Pay/Service/Package{i}.php",
            "line": 100 + i,
            "content": "min rate amount exchange",
            "matched_queries": ["min", "rate", "amount", "exchange"],
            "matched_concepts": ["coin", "limit", "fee", "amount", "exchange", "usd"],
            "retrieval_stage": "concept_expansion",
        }
        for i in range(8)
    ]

    selected = select_semantic_hits([*generic_hits, anchor], limit=12)

    assert selected[0] == anchor


def test_select_semantic_hits_keeps_repeated_rare_anchor_blocks() -> None:
    """验证稀有业务词可保留多个同类配置块，不被高频活动标题挤掉。"""
    annual_hits = [
        {
            "file": f"php/Entry{i}.php",
            "line": 10 + i * 30,
            "content": "年度金主入口",
            "matched_queries": ["年度金主"],
            "matched_concepts": ["annual", "gold_owner"],
            "retrieval_stage": "business_anchor",
        }
        for i in range(10)
    ]
    config_hits = [
        {
            "file": "php/ceremony/config.php",
            "line": line,
            "content": "//商品碎片数",
            "matched_queries": ["商品碎片", "碎片"],
            "matched_concepts": ["fragment"],
            "retrieval_stage": "business_anchor",
        }
        for line in (173, 211, 249, 288)
    ]

    selected = select_semantic_hits([*annual_hits, *config_hits], limit=18)

    selected_lines = {
        item["line"]
        for item in selected
        if item["file"] == "php/ceremony/config.php"
    }
    assert selected_lines == {173, 211, 249, 288}


def test_discover_related_queries_expands_hit_to_symbols_and_configs() -> None:
    """验证命中文档或注释后能自动发现同文件的类、方法和配置键。"""
    index = {
        "symbols": [
            {
                "symbol_type": "class",
                "symbol_name": "RiskControl",
                "file": "php/RiskControl.php",
                "line": 3,
            },
            {
                "symbol_type": "method",
                "symbol_name": "RiskControl::checkFreezeLimit",
                "file": "php/RiskControl.php",
                "line": 8,
            },
            {
                "symbol_type": "method",
                "symbol_name": "AuditService::unrelated",
                "file": "php/AuditService.php",
                "line": 3,
            },
        ],
        "configs": [
            {
                "key": "risk.freeze_threshold",
                "file": "php/RiskControl.php",
                "line": 12,
            }
        ],
        "references": [],
        "sql": [],
    }
    hits = [
        {
            "file": "php/RiskControl.php",
            "line": 2,
            "content": "// 用户风控冻结阈值",
            "matched_queries": ["风控冻结", "冻结阈值"],
        }
    ]
    plan = discover_related_queries(
        index,
        hits,
        existing_queries=["风控冻结"],
        limit=10,
    )
    assert {"RiskControl", "RiskControl::checkFreezeLimit", "checkFreezeLimit", "risk.freeze_threshold"} <= set(plan["queries"])
    assert "AuditService::unrelated" not in plan["queries"]
    assert plan["sources"]


def test_discover_related_queries_prefers_real_activity_class() -> None:
    """验证活动源码命中会反向发现真实服务类，而不是优先发现泛化基础类。"""
    index = {
        "symbols": [
            {
                "symbol_type": "class",
                "symbol_name": "SpaceshipService",
                "file": "waky6_miyouactivity/crontab/ceremony2025GoldOwner/SpaceshipService.php",
                "line": 3,
            },
            {
                "symbol_type": "method",
                "symbol_name": "SpaceshipService::ordinaryPrizes",
                "file": "waky6_miyouactivity/crontab/ceremony2025GoldOwner/SpaceshipService.php",
                "line": 462,
            },
            {
                "symbol_type": "class",
                "symbol_name": "FamilyBaseService",
                "file": "waky6_miyouphp/php/services/family/FamilyBaseService.php",
                "line": 3,
            },
        ],
        "configs": [],
        "references": [],
        "sql": [],
    }
    hits = [
        {
            "file": "waky6_miyouactivity/crontab/ceremony2025GoldOwner/SpaceshipService.php",
            "line": 493,
            "content": "// 普通奖励计算 $ordinaryPrizes = SpaceshipConfig::getOrdinaryPrizesByLevel($level)",
            "matched_queries": ["普通奖励", "商品碎片"],
        },
        {
            "file": "waky6_miyouphp/php/services/family/FamilyBaseService.php",
            "line": 570,
            "content": "// 分配金库 FamilyBaseService",
            "matched_queries": ["分配"],
        },
    ]
    plan = discover_related_queries(index, hits, limit=8)
    assert "SpaceshipService" in plan["queries"]
    assert "SpaceshipService::ordinaryPrizes" in plan["queries"]


def test_discovering_unmapped_business_term_has_no_domain_mapping_dependency() -> None:
    """验证任意新增中文业务词只依赖命中文件，不读取领域映射。"""
    index = {
        "symbols": [
            {
                "symbol_type": "class",
                "symbol_name": "PromotionInviteControl",
                "file": "php/PromotionInviteControl.php",
                "line": 5,
            }
        ],
        "configs": [
            {
                "key": "promotion.invite_daily_limit",
                "file": "php/PromotionInviteControl.php",
                "line": 18,
            }
        ],
        "references": [],
        "sql": [],
    }
    hits = [
        {
            "file": "php/PromotionInviteControl.php",
            "line": 4,
            "content": "// 活动邀请码每日上限",
            "matched_queries": ["活动邀请码", "邀请码每日", "每日上限"],
        }
    ]
    plan = discover_related_queries(index, hits, limit=8)
    assert "PromotionInviteControl" in plan["queries"]
    assert "promotion.invite_daily_limit" in plan["queries"]


def test_discovery_filters_real_but_generic_symbols_and_dynamic_config_prefixes() -> None:
    """验证索引中存在的泛化类名和动态配置前缀不能污染业务检索计划。"""
    index = {
        "symbols": [
            {
                "symbol_type": "class",
                "symbol_name": "User",
                "file": "docs/business.md",
                "line": 10,
            }
        ],
        "configs": [
            {
                "key": "user_service.",
                "file": "docs/business.md",
                "line": 12,
            }
        ],
        "references": [],
        "sql": [],
    }
    hits = [
        {
            "file": "docs/business.md",
            "line": 9,
            "content": "| User | 活动邀请码每日上限 |",
            "matched_queries": ["活动邀请码", "每日上限"],
        }
    ]
    plan = discover_related_queries(index, hits, limit=8)
    assert "User" not in plan["queries"]
    assert "user_service." not in plan["queries"]


def test_retrieval_support_accepts_strong_real_semantic_vector() -> None:
    """验证真实强向量候选可以触发源码发现和调用链验证。"""
    support = assess_retrieval_support(
        [
            {
                "file": "php/service/RegistrationService.php",
                "line": 20,
                "line_start": 20,
                "line_end": 90,
                "content": "if ($deviceUserCount >= $limit) { throw new Exception(); }",
                "retrieval_stage": "semantic_vector",
                "semantic_vector": True,
                "semantic_vector_strong": True,
                "vector_compatible": True,
                "vector_score": 0.61,
                "embedding_provider": "openai_compatible",
                "embedding_model": "multilingual-semantic-v2",
                "matched_queries": [],
                "matched_concepts": [],
            }
        ],
        set(),
    )

    assert support["grounded"] is True
    assert support["level"] == "semantic_vector"
    assert support["semantic_vector_count"] == 1
    assert support["strong_semantic_vector_count"] == 1
    assert "不代表" not in support["reason"]


def test_retrieval_support_rejects_hash_and_weak_semantic_vectors() -> None:
    """验证离线 hash 或未标记强相似的向量不能绕过业务锚点门禁。"""
    hits = [
        {
            "file": "php/service/RegistrationService.php",
            "line": 20,
            "retrieval_stage": "semantic_vector",
            "semantic_vector": True,
            "semantic_vector_strong": True,
            "vector_compatible": True,
            "vector_score": 0.9,
            "embedding_provider": "openai_compatible",
            "embedding_model": "hash-v1",
            "matched_queries": [],
            "matched_concepts": [],
        },
        {
            "file": "php/service/OtherService.php",
            "line": 30,
            "retrieval_stage": "semantic_vector",
            "semantic_vector": True,
            "semantic_vector_strong": False,
            "vector_compatible": True,
            "vector_score": 0.35,
            "embedding_provider": "openai_compatible",
            "embedding_model": "multilingual-semantic-v2",
            "matched_queries": [],
            "matched_concepts": [],
        },
        {
            "file": "phpunit/RegistrationServiceTest.php",
            "line": 40,
            "retrieval_stage": "semantic_vector",
            "semantic_vector": True,
            "semantic_vector_strong": True,
            "vector_compatible": True,
            "vector_score": 0.8,
            "embedding_provider": "openai_compatible",
            "embedding_model": "multilingual-semantic-v2",
            "matched_queries": [],
            "matched_concepts": [],
        },
    ]

    support = assess_retrieval_support(hits, set())

    assert support["grounded"] is False
    assert support["level"] == "insufficient"
    assert support["strong_semantic_vector_count"] == 0


def test_select_semantic_hits_caps_vector_candidates_before_symbol_discovery() -> None:
    """验证真实向量候选最多保留十二只，避免占满符号发现和最终证据预算。"""
    hits = [
        {
            "file": f"php/service/Service{index}.php",
            "line": index + 1,
            "retrieval_stage": "semantic_vector",
            "semantic_vector": True,
            "semantic_vector_strong": True,
            "vector_compatible": True,
            "vector_score": 0.7 - index * 0.01,
            "embedding_provider": "openai_compatible",
            "embedding_model": "multilingual-semantic-v2",
            "matched_queries": [],
            "matched_concepts": [],
        }
        for index in range(20)
    ]

    selected = select_semantic_hits(hits, limit=36)

    assert len(selected) == 12
    assert all(item["retrieval_stage"] == "semantic_vector" for item in selected)
