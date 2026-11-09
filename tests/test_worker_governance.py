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
