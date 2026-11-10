"""任务队列、超时和 Worker 中断恢复测试。"""

import pytest
from billiard.exceptions import SoftTimeLimitExceeded

from app import worker
from app.settings import Settings
from app.task_store import recover_stale_diagnosis_tasks


class _Pool:
    """记录恢复 SQL 的连接池替身。"""

    def __init__(self, affected: int = 2):
        """初始化受影响行数和调用记录。

        参数:
            - `affected`：数据库受影响记录数，类型为 `int`。
        """
        self.affected = affected
        self.calls = []

    async def execute(self, query, *args):
        """返回 PostgreSQL 风格更新结果。

        参数:
            - `query`：检索或查询文本。
            - `*args`：可变位置参数集合。
        """
        self.calls.append((query, args))
        return f"UPDATE {self.affected}"


@pytest.mark.asyncio
async def test_recover_stale_diagnosis_tasks_marks_only_running_states() -> None:
    """验证仅恢复长时间处于运行阶段的任务。"""
    pool = _Pool()
    count = await recover_stale_diagnosis_tasks(pool, 10)
    assert count == 2
    query, args = pool.calls[0]
    assert "RUNNING" in query and "SEARCHING" in query and "REPORTING" in query
    assert args == (10,)


def test_worker_routes_and_time_limits_are_configured() -> None:
    """验证诊断和索引任务进入独立队列并配置软硬超时。"""
    assert worker.celery_app.conf.task_routes["app.worker.run_diagnosis_task"]["queue"] == "diagnosis"
    assert worker.celery_app.conf.task_routes["app.worker.run_document_index_task"]["queue"] == "index"
    assert worker.run_diagnosis_task.soft_time_limit == worker.settings.diagnosis_task_soft_time_limit_seconds
    assert worker.run_diagnosis_task.time_limit == worker.settings.diagnosis_task_time_limit_seconds
    assert worker.run_document_index_task.queue == "index"


def test_explicit_symbol_extraction_excludes_generic_technology_tokens() -> None:
    """验证首轮精确检索保留真实类名，不把 Redis 等通用技术词当成目标入口。"""
    symbols = worker._explicit_symbol_queries(
        ["FuseRecoveryService", "GuildController::inviteGuild", "Redis", "SQL"]
    )

    assert symbols == ["FuseRecoveryService", "GuildController::inviteGuild"]


def test_config_keys_are_extracted_from_hit_context_for_followup_search() -> None:
    """验证日志命中附近的真实配置读取会进入下一轮定义检索。"""
    hits = [
        {
            "content": "logInfo('device registered users exceeds the limit')",
            "context": "\n".join(
                [
                    "50: $regCount = getDeviceRegCount($deviceId);",
                    "51: $limit = Config::get('device.device_user_count_limit');",
                    "52: if ($regCount >= $limit) { return false; }",
                ]
            ),
        },
        {
            "content": "\\Conf\\Config::get('device.device_user_count_limit')",
        },
    ]

    keys = worker._config_keys_from_hits(hits)

    assert keys == ["device.device_user_count_limit"]


def test_initial_hit_tagging_distinguishes_anchor_and_explicit_symbol() -> None:
    """验证源码命中会保留中文业务锚点和显式符号的可审计来源。"""
    anchor_stage = worker._tag_initial_retrieval_stage(
        {"matched_queries": ["钻石提现"]},
        ["钻石提现"],
        ["AdvanceServices"],
    )
    symbol_stage = worker._tag_initial_retrieval_stage(
        {"matched_queries": ["AdvanceServices"]},
        ["钻石提现"],
        ["AdvanceServices"],
    )

    assert anchor_stage == "business_anchor"
    assert symbol_stage == "explicit_symbol"


def test_query_evidence_uses_one_batch_search_and_keeps_config_definition(monkeypatch) -> None:
    """验证全部规划词只触发一次扫描，并保留配置定义与全部命中词。

    参数:
        - `monkeypatch`：pytest 运行期替换工具，用于记录底层检索调用。
    """
    calls = []

    def fake_search(root, project, queries, **kwargs):
        """返回包含显式符号和配置定义的模拟批量检索结果。

        参数:
            - `root`：代码仓库根目录。
            - `project`：当前项目标识。
            - `queries`：本次批量检索词。
            - `**kwargs`：批量检索的可选边界参数。
        """
        calls.append((root, project, list(queries), kwargs))
        return {
            "matches": [
                {
                    "file": "module/config/service.php",
                    "line": 18,
                    "content": "'daily_limit' => 10,",
                    "matched_queries": ["SettlementService", "daily_limit"],
                }
            ]
        }

    monkeypatch.setattr(worker, "search_code_terms", fake_search)

    evidence = worker._search_query_evidence(
        "repositories",
        "project",
        ["SettlementService", "daily_limit", "SettlementService"],
        ["daily_limit"],
    )

    assert len(calls) == 1
    assert calls[0][2] == ["SettlementService", "daily_limit"]
    assert evidence[0]["type"] == "config_definition"
    assert evidence[0]["query"] == "daily_limit"
    assert evidence[0]["matched_queries"] == ["SettlementService", "daily_limit"]


def test_diagnostic_evidence_matches_secondary_batch_query() -> None:
    """验证批量命中被某一规划词共享时，仍能满足该词的证据保底。"""
    item = {
        "type": "code",
        "query": "SettlementService",
        "matched_queries": ["SettlementService", "daily_limit"],
        "file": "module/service/SettlementService.php",
        "line": 18,
        "content": "$dailyLimit = 10;",
    }

    selected = worker._select_diagnostic_evidence([item], ["daily_limit"], limit=1)

    assert selected == [item]


def test_explicit_target_file_prefers_question_module_scope() -> None:
    """验证同名结算类会按问题中的业务模块目录收敛到真实实现。"""
    project_index = {
        "symbols": [
            {
                "symbol_name": "SettlementService",
                "symbol_type": "class",
                "file": "waky3_miyouphp/php/services/PatronCharge/SettlementService.php",
                "line": 10,
            },
            {
                "symbol_name": "SettlementService",
                "symbol_type": "class",
                "file": "waky3_miyouactivity/Logic/cyberStreetBattle/service/settlement/SettlementService.php",
                "line": 10,
            },
        ]
    }

    targets = worker._select_explicit_target_files(
        project_index,
        [],
        ["PatronCharge", "SettlementService"],
        "PatronCharge 的 SettlementService 如何处理结算？",
    )

    assert targets == ["waky3_miyouphp/php/services/PatronCharge/SettlementService.php"]


def test_explicit_target_file_uses_ranked_symbol_references_without_module_word() -> None:
    """验证问题未写目录名时，同名类按首轮高排名符号来源路径消歧。"""
    project_index = {
        "symbols": [
            {
                "symbol_name": "GiftWeekStarSettlementService",
                "symbol_type": "class",
                "file": "waky4_miyouactivity/Logic/giftWeekStarNew/service/GiftWeekStarSettlementService.php",
                "line": 10,
            },
            {
                "symbol_name": "GiftWeekStarSettlementService",
                "symbol_type": "class",
                "file": "waky4_miyouactivity/Logic/giftWeekStarNewRegion/service/GiftWeekStarSettlementService.php",
                "line": 10,
            },
        ]
    }
    evidence = [
        {
            "file": "waky4_miyouactivity/Logic/giftWeekStarNewRegion/boot/giftWeekStarNewRegionBoot.php",
            "retrieval_stage": "explicit_symbol",
            "generic_rank": 0,
            "matched_queries": ["GiftWeekStarSettlementService", "region"],
        },
        {
            "file": "waky4_miyouactivity/Logic/giftWeekStarNew/boot/giftWeekStarNewBoot.php",
            "retrieval_stage": "explicit_symbol",
            "generic_rank": 4,
            "matched_queries": ["GiftWeekStarSettlementService"],
        },
    ]

    targets = worker._select_explicit_target_files(
        project_index,
        evidence,
        ["GiftWeekStarSettlementService"],
        "GiftWeekStarSettlementService 按什么时间范围结算？",
    )

    assert targets == [
        "waky4_miyouactivity/Logic/giftWeekStarNewRegion/service/GiftWeekStarSettlementService.php"
    ]


def test_explicit_target_scope_ignores_generic_redis_inside_class_filename() -> None:
    """验证通用 Redis 词不会让未指定模块的同名类重新进入候选。"""
    project_index = {
        "symbols": [
            {
                "symbol_name": "ClearOldRedisKeyService",
                "symbol_type": "class",
                "file": "waky4_miyouactivity/Logic/giftWeekStarNew/service/ClearOldRedisKeyService.php",
                "line": 8,
            },
            {
                "symbol_name": "ClearOldRedisKeyService",
                "symbol_type": "class",
                "file": "waky4_miyouactivity/Logic/giftWeekStarNewRegion/service/ClearOldRedisKeyService.php",
                "line": 10,
            },
        ]
    }

    targets = worker._select_explicit_target_files(
        project_index,
        [],
        ["ClearOldRedisKeyService"],
        "giftWeekStarNewRegion 的 ClearOldRedisKeyService 清理哪些 Redis key？",
    )

    assert targets == [
        "waky4_miyouactivity/Logic/giftWeekStarNewRegion/service/ClearOldRedisKeyService.php"
    ]


def test_explicit_target_focus_removes_other_same_name_module_file() -> None:
    """验证最终证据会删除其他模块同名类，但保留配置和普通调用方。"""
    target = "waky3_miyouphp/php/services/PatronCharge/SettlementService.php"
    evidence = [
        {
            "file": "waky3_miyouactivity/Logic/cyberStreetBattle/service/settlement/SettlementService.php",
            "line": 30,
        },
        {"file": target, "line": 36},
        {"file": "waky3_miyouphp/php/config/patron.php", "line": 7},
    ]

    focused = worker._focus_explicit_target_evidence(
        evidence,
        [target],
        ["PatronCharge", "SettlementService"],
    )

    assert focused[0]["file"] == target
    assert {item["file"] for item in focused} == {
        target,
        "waky3_miyouphp/php/config/patron.php",
    }


def test_inherited_class_evidence_follows_extends_declaration(monkeypatch) -> None:
    """验证显式类证据会沿源码中的 extends 声明补充唯一直接基类。

    参数:
        - `monkeypatch`：pytest 运行期替换工具，用于提供确定性的源码读取结果。
    """
    child_file = "module/service/CleanupService.php"
    parent_file = "module/ActivityBase.php"
    project_index = {
        "symbols": [
            {
                "symbol_name": "CleanupService",
                "symbol_type": "class",
                "file": child_file,
                "line": 10,
            },
            {
                "symbol_name": "ActivityBase",
                "symbol_type": "class",
                "file": parent_file,
                "line": 17,
            },
        ]
    }

    def fake_read_file(root, project, relative_path, start_line=1, end_line=200):
        """返回目标类或基类的模拟带行号源码。

        参数:
            - `root`：代码仓库根目录。
            - `project`：当前项目标识。
            - `relative_path`：当前读取的项目内文件路径。
            - `start_line`：读取起始行。
            - `end_line`：读取结束行。
        """
        content = {
            child_file: "10: class CleanupService extends ActivityBase",
            parent_file: "17: abstract class ActivityBase",
        }[relative_path]
        return {"content": content, "start_line": start_line, "end_line": end_line}

    monkeypatch.setattr(worker, "read_file", fake_read_file)

    evidence = worker._inherited_class_evidence(
        "repositories",
        "project",
        project_index,
        [child_file],
    )

    assert evidence == [
        {
            "type": "symbol",
            "query": "ActivityBase",
            "file": parent_file,
            "line": 17,
            "content": "ActivityBase 父类定义",
            "hierarchy_of": child_file,
            "hierarchy_depth": 1,
        }
    ]


def test_document_index_task_uses_application_runtime_module(monkeypatch) -> None:
    """验证索引 Worker 不依赖生产镜像未安装的 scripts 包。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    calls = []

    async def fake_run_document_index(project):
        """记录 Worker 传入的项目并返回模拟分块数。

        参数:
            - `project`：需要建立索引的项目目录名。
        """
        calls.append(project)
        return 17

    monkeypatch.setattr(worker, "run_document_index_chunks", fake_run_document_index)
    result = worker.run_document_index_task.run("waky3")
    assert calls == ["waky3"]
    assert result == {"project": "waky3", "status": "completed", "chunk_count": 17}


def test_worker_rejects_invalid_timeout_pair() -> None:
    """验证软超时不小于硬超时时拒绝 Worker 配置。"""
    invalid = Settings(
        diagnosis_task_soft_time_limit_seconds=100,
        diagnosis_task_time_limit_seconds=100,
    )
    with pytest.raises(ValueError):
        worker.validate_worker_timeouts(invalid)


def test_symbol_evidence_includes_exact_class_definition() -> None:
    """验证动态发现的类名会从索引补齐真实声明，而不是只保留跨文件引用。"""
    project_index = {
        "symbols": [
            {
                "symbol_name": "SurprisePrize",
                "symbol_type": "class",
                "file": "waky6_miyouactivity/crontab/ceremony2025GoldOwner/SurprisePrize.php",
                "line": 12,
            },
            {
                "symbol_name": "SurprisePrize::initSurprise",
                "symbol_type": "method",
                "file": "waky6_miyouactivity/crontab/ceremony2025GoldOwner/SurprisePrize.php",
                "line": 77,
            },
        ]
    }

    evidence = worker._symbol_evidence(
        project_index,
        ["SurprisePrize", "SurprisePrize::initSurprise"],
    )

    definitions = {(item["query"], item["line"], item["content"]) for item in evidence}
    assert (
        "SurprisePrize",
        12,
        "SurprisePrize 类定义",
    ) in definitions
    assert (
        "SurprisePrize::initSurprise",
        77,
        "SurprisePrize::initSurprise 方法定义",
    ) in definitions


def test_config_definition_match_requires_config_assignment() -> None:
    """验证只有配置文件里的同名数组字段会成为配置定义证据。"""
    assert worker._is_config_definition_match(
        "diamond_to_usd",
        {
            "file": "waky6_miyouphp/php/config/anchor.php",
            "content": "'diamond_to_usd' => getByTestMode(40000,40000),",
        },
    )
    assert not worker._is_config_definition_match(
        "diamond_to_usd",
        {
            "file": "waky6_miyouphp/php/services/Anchor/AdvanceServices.php",
            "content": "$price = Config::get('anchor.diamond_to_usd');",
        },
    )


def test_diagnostic_evidence_reserves_config_definition_values() -> None:
    """验证配置定义不会在通用源码预算耗尽后被调用点挤出证据窗口。"""
    generic_hits = [
        {
            "type": "generic_code",
            "file": f"php/Module{i}.php",
            "line": i,
            "content": "业务配置",
            "generic_rank": i,
            "retrieval_stage": "business_anchor",
            "matched_queries": ["业务配置"],
        }
        for i in range(40)
    ]
    config_definition = {
        "type": "config_definition",
        "file": "php/config/anchor.php",
        "line": 79,
        "query": "diamond_to_usd",
        "content": "'diamond_to_usd' => 40000,",
    }

    selected = worker._select_diagnostic_evidence(
        [*generic_hits, config_definition],
        ["diamond_to_usd"],
        limit=32,
    )

    assert config_definition in selected


def test_diagnostic_evidence_keeps_nearest_callees_for_top_chains() -> None:
    """验证高相关调用链会保留入口和两级最近子调用，不独占全部证据预算。"""
    entries = []
    callees = []
    debits = []
    for chain_index in range(8):
        entries.append(
            {
                "type": "symbol",
                "file": f"php/Entry{chain_index}.php",
                "line": 10,
                "query": f"Entry{chain_index}::run",
                "call_chain_entry": f"Entry{chain_index}::run",
                "call_chain_depth": 0,
                "call_chain_order": chain_index,
            }
        )
        callees.append(
            {
                "type": "symbol",
                "file": f"php/Entry{chain_index}.php",
                "line": 20,
                "query": f"Entry{chain_index}::guard",
                "call_chain_entry": f"Entry{chain_index}::run",
                "call_chain_depth": 1,
                "call_chain_order": chain_index,
            }
        )
        debits.append(
            {
                "type": "symbol",
                "file": f"php/Entry{chain_index}.php",
                "line": 30,
                "query": f"Entry{chain_index}::debit",
                "call_chain_entry": f"Entry{chain_index}::run",
                "call_chain_depth": 2,
                "call_chain_order": chain_index,
            }
        )

    selected = worker._select_diagnostic_evidence(
        [*entries, *callees, *debits],
        [],
        limit=12,
    )

    selected_queries = {item["query"] for item in selected}
    assert {f"Entry{i}::guard" for i in range(4)} <= selected_queries
    assert {f"Entry{i}::debit" for i in range(4)} <= selected_queries


def test_merge_semantic_hits_combines_round_evidence_for_same_line() -> None:
    """验证中文锚点和英文概念命中同一行时会合并业务词与概念覆盖。"""
    chinese_hits = [
        {
            "file": "waky4_miyouphp/php/action/DiamondControl.php",
            "line": 21,
            "content": "// 前端展示钻石",
            "matched_queries": ["前端展示钻石"],
            "matched_concepts": ["display", "diamond"],
        }
    ]
    concept_hits = [
        {
            "file": "waky4_miyouphp/php/action/DiamondControl.php",
            "line": 21,
            "content": "// 前端展示钻石",
            "matched_queries": ["show", "diamond"],
            "matched_concepts": ["anchor"],
        }
    ]

    merged = worker._merge_semantic_hits(chinese_hits, concept_hits)

    assert len(merged) == 1
    assert merged[0]["matched_queries"] == ["前端展示钻石", "show", "diamond"]
    assert merged[0]["matched_concepts"] == ["display", "diamond", "anchor"]


def test_evidence_deduplication_keeps_concept_coverage_when_symbol_wins() -> None:
    """验证同行的符号定义替换普通命中时，跨语言概念覆盖不会丢失。"""
    generic_hit = {
        "type": "generic_code",
        "file": "php/DiamondControl.php",
        "line": 20,
        "content": "// 展示钻石档位",
        "matched_queries": ["diamond", "show"],
        "matched_concepts": ["display", "diamond", "gear"],
    }
    symbol_hit = {
        "type": "symbol",
        "query": "DiamondControl",
        "file": "php/DiamondControl.php",
        "line": 20,
        "content": "DiamondControl 类定义",
        "matched_queries": ["DiamondControl"],
        "matched_concepts": [],
    }

    evidence = worker._deduplicate_evidence([generic_hit, symbol_hit])

    assert evidence[0]["type"] == "symbol"
    assert evidence[0]["matched_concepts"] == ["display", "diamond", "gear"]
    assert evidence[0]["matched_queries"] == ["diamond", "show", "DiamondControl"]


def test_call_chain_evidence_adds_same_repository_business_callees() -> None:
    """验证调用链补全会带回同业务子仓的校验方法，并排除框架目录节点。"""
    chains = [
        {
            "entry": "AdvanceServices::anchorWithdraw",
            "nodes": [
                {
                    "symbol_name": "AdvanceServices::anchorWithdraw",
                    "file": "waky6_miyouphp/php/services/Anchor/AdvanceServices.php",
                    "line": 577,
                },
                {
                    "symbol_name": "AdvanceServices::checkAnchorCanWithdraw",
                    "file": "waky6_miyouphp/php/services/Anchor/AdvanceServices.php",
                    "line": 272,
                },
                {
                    "symbol_name": "Application::stop",
                    "file": "waky6_miyouphp/php/lib/application.php",
                    "line": 17,
                },
                {
                    "symbol_name": "Lang::get",
                    "file": "waky6_pay-api/lang/Lang.php",
                    "line": 92,
                },
            ],
        }
    ]

    evidence = worker._call_chain_evidence(chains)

    assert {item["query"] for item in evidence} == {
        "AdvanceServices::anchorWithdraw",
        "AdvanceServices::checkAnchorCanWithdraw",
    }
    assert all(item["type"] == "symbol" for item in evidence)
    assert [item["call_chain_depth"] for item in evidence] == [0, 1]


def test_evidence_relevance_prefers_shallow_business_call_chain_nodes() -> None:
    """验证入口和直接校验方法优先于深层框架调用。"""
    direct = {
        "type": "symbol",
        "file": "waky6_miyouphp/php/services/Anchor/AdvanceServices.php",
        "line": 272,
        "content": "checkAnchorCanWithdraw",
        "call_chain_entry": "AdvanceServices::anchorWithdraw",
        "call_chain_depth": 1,
    }
    deep = {
        "type": "symbol",
        "file": "waky6_miyouphp/php/services/Anchor/AdvanceServices.php",
        "line": 703,
        "content": "addProxyUSD",
        "call_chain_entry": "AdvanceServices::anchorWithdraw",
        "call_chain_depth": 12,
    }

    assert sorted([deep, direct], key=worker._evidence_relevance)[0] == direct


def test_evidence_deduplication_keeps_symbol_declaration_semantics() -> None:
    """验证同一行的文本命中和符号命中合并后仍保留声明类型。"""
    code_hit = {
        "type": "code",
        "query": "SurprisePrize",
        "file": "SurprisePrize.php",
        "line": 3,
        "content": "class SurprisePrize",
    }
    symbol_hit = {
        "type": "symbol",
        "query": "SurprisePrize",
        "file": "SurprisePrize.php",
        "line": 3,
        "content": "SurprisePrize 类定义",
    }

    evidence = worker._deduplicate_evidence([code_hit, symbol_hit])

    assert evidence == [symbol_hit]


def test_diagnostic_evidence_prefers_symbol_definition_over_reference() -> None:
    """验证同名类被多个文件引用时，最终证据仍优先保留声明所在文件。"""
    evidence = [
        {
            "type": "code",
            "query": "SurprisePrize",
            "file": "waky6_miyouactivity/crontab/ceremony2025GoldOwner/require_file.php",
            "line": 8,
            "content": "require_once __DIR__ . '/SurprisePrize.php';",
        },
        {
            "type": "symbol",
            "query": "SurprisePrize",
            "file": "waky6_miyouactivity/crontab/ceremony2025GoldOwner/SurprisePrize.php",
            "line": 12,
            "content": "SurprisePrize 类定义",
        },
    ]

    selected = worker._select_diagnostic_evidence(evidence, ["SurprisePrize"], limit=1)

    assert selected == [evidence[1]]


def test_diagnostic_evidence_caps_generic_candidates_before_symbol_expansion() -> None:
    """验证通用命中有固定预算，不会在符号发现前占满整个证据窗口。"""
    evidence = [
        {
            "type": "generic_code",
            "file": f"php/Config{i}.php",
            "line": i + 1,
            "content": "商品碎片配置",
        }
        for i in range(40)
    ]

    selected = worker._select_diagnostic_evidence(evidence, [], limit=48)

    assert len(selected) == 28


def test_evidence_range_expands_complete_method_body() -> None:
    """验证方法符号按下一个符号边界扩展，避免只返回函数声明附近几行。"""
    project_index = {
        "symbols": [
            {"symbol_name": "SurprisePrize", "symbol_type": "class", "file": "SurprisePrize.php", "line": 1},
            {
                "symbol_name": "SurprisePrize::generateRandomIndexes",
                "symbol_type": "method",
                "file": "SurprisePrize.php",
                "line": 160,
            },
            {"symbol_name": "SurprisePrize::getSurprise", "symbol_type": "method", "file": "SurprisePrize.php", "line": 172},
        ]
    }

    start_line, end_line = worker._method_evidence_range(
        project_index,
        {
            "type": "symbol",
            "query": "SurprisePrize::generateRandomIndexes",
            "file": "SurprisePrize.php",
            "line": 160,
        },
    )

    assert (start_line, end_line) == (157, 171)


def test_evidence_relevance_uses_concept_coverage_instead_of_project_symbols() -> None:
    """验证证据排序依靠通用概念覆盖和结构信号，而不是项目专属方法名。"""
    relevant = {
        "file": "waky4_miyouphp/php/model/DiamondModel.php",
        "line": 116,
        "query": "getDiamondBalance",
        "context": "function getDiamondBalance() { return $diamondBalance; }",
        "matched_concepts": ["diamond", "balance"],
    }
    noise = {
        "file": "waky4_miyouphp/php/action/GiftControl.php",
        "line": 20,
        "query": "gift",
        "context": "gift display reference",
        "matched_concepts": ["display"],
    }

    ordered = sorted([noise, relevant], key=worker._evidence_relevance)

    assert ordered[0] == relevant


def test_evidence_relevance_preserves_rare_business_anchor_rank() -> None:
    """验证稀有通用锚点的预排序不会被同名高频标题覆盖。"""
    rare_config = {
        "type": "generic_code",
        "file": "php/ceremony/config.php",
        "line": 249,
        "content": "//商品碎片数",
        "generic_rank": 3,
        "retrieval_stage": "business_anchor",
        "matched_queries": ["商品碎片"],
        "matched_concepts": ["fragment"],
    }
    frequent_title = {
        "type": "generic_code",
        "file": "php/ceremony/boot.php",
        "line": 20,
        "content": "//年度金主发奖",
        "generic_rank": 30,
        "retrieval_stage": "business_anchor",
        "matched_queries": ["年度金主"],
        "matched_concepts": ["annual", "gold_owner"],
    }

    assert sorted([frequent_title, rare_config], key=worker._evidence_relevance)[0] == rare_config


@pytest.mark.parametrize(
    ("llm_enabled", "use_ai", "evidence", "expected"),
    [
        (True, False, 80, "已按任务设置跳过 AI 诊断叙写"),
        (True, None, 0, "代码证据不足，已跳过 AI 诊断叙写"),
        (False, None, 80, None),
    ],
)
def test_ai_skip_warning_distinguishes_user_choice_from_missing_evidence(
    llm_enabled,
    use_ai,
    evidence,
    expected,
) -> None:
    """验证主动关闭 AI 不会被报告成代码证据不足。

    参数:
        - `llm_enabled`：测试配置中的全局 LLM 开关，类型为 `bool`。
        - `use_ai`：任务级 AI 开关，类型为 `bool | None`。
        - `evidence`：可引用证据数量，类型为 `int`。
        - `expected`：期望的跳过提示片段或空值，类型为 `str | None`。
    """
    settings = Settings(
        llm_enabled=llm_enabled,
        llm_min_evidence_items=3,
    )
    warning = worker.ai_skip_warning(settings, {"use_ai": use_ai}, evidence)
    if expected is None:
        assert warning is None
    else:
        assert expected in warning


class _ClosingPool:
    """提供异步关闭方法的数据库池替身。"""

    async def close(self):
        """模拟释放连接池。"""
        return None


@pytest.mark.asyncio
async def test_soft_timeout_marks_diagnosis_failed(monkeypatch) -> None:
    """验证软超时被转换为失败状态并保留超时原因。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    updates = []
    pool = _ClosingPool()

    async def fake_create_pool(settings):
        """返回测试连接池。

        参数:
            - `settings`：应用运行配置。
        """
        return pool

    async def fake_update_task(pool, task_id, **kwargs):
        """首次状态推进抛软超时，失败回写正常完成。

        参数:
            - `pool`：数据库连接池或测试替身。
            - `task_id`：诊断任务 ID。
            - `**kwargs`：按关键字传入的附加参数集合。
        """
        updates.append((task_id, kwargs))
        if len(updates) == 1:
            raise SoftTimeLimitExceeded("soft timeout")
        return {}

    monkeypatch.setattr(worker, "create_pool", fake_create_pool)
    monkeypatch.setattr(worker, "update_task", fake_update_task)
    with pytest.raises(SoftTimeLimitExceeded):
        await worker._execute_diagnosis("diag_timeout")
    assert updates[-1][1]["status"] == "FAILED"
    assert updates[-1][1]["current_step"] == "任务执行超时"
