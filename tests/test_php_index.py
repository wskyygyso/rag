"""PHP 符号解析和调用链分析测试。"""

from pathlib import Path

from app.php_index import index_repository, parse_php_source, trace_call_chain
from app.symbol_tools import (
    find_references,
    find_symbol,
    get_project_symbol_summary,
    select_call_chain_entries,
    summarize_symbol_index,
)


SOURCE = """<?php
class GuildController {
    public function inviteGuild() {
        $this->checkGuild();
        Config::get('anchor.joinBDGuildDayLimit');
    }
    private function checkGuild() {}
}
"""


def test_parse_php_source_extracts_symbols_and_references() -> None:
    """验证类、方法、配置读取和 this 调用均能被提取。"""
    result = parse_php_source(SOURCE, "php/action/Guild.php")
    names = {item["symbol_name"] for item in result["symbols"]}
    assert {"GuildController", "GuildController::inviteGuild", "GuildController::checkGuild"} <= names
    assert result["configs"][0]["key"] == "anchor.joinBDGuildDayLimit"
    targets = {item["target"] for item in result["references"]}
    assert "GuildController::checkGuild" in targets


def test_index_repository_and_trace_call_chain(tmp_path: Path) -> None:
    """验证项目索引和入口调用链节点可以关联到文件和行号。

    参数:
        - `tmp_path`：pytest 临时目录，类型为 `Path`。
    """
    project = tmp_path / "waky3" / "php"
    project.mkdir(parents=True)
    (project / "Guild.php").write_text(SOURCE, encoding="utf-8")
    indexed = index_repository(str(tmp_path), "waky3")
    assert len(indexed["symbols"]) == 3
    chain = trace_call_chain(str(tmp_path), "waky3", "GuildController::inviteGuild")
    assert chain["nodes"][0]["symbol_name"] == "GuildController::inviteGuild"
    assert "GuildController::checkGuild" in {edge["to"] for edge in chain["edges"]}


def test_symbol_tools_find_definitions_references_and_summary(tmp_path: Path) -> None:
    """验证符号定义、引用和项目摘要工具复用同一份索引。

    参数:
        - `tmp_path`：pytest 临时目录，类型为 `Path`。
    """
    project = tmp_path / "waky3" / "php"
    project.mkdir(parents=True)
    (project / "Guild.php").write_text(SOURCE, encoding="utf-8")
    symbol = find_symbol(str(tmp_path), "waky3", "GuildController::checkGuild")
    references = find_references(str(tmp_path), "waky3", "GuildController::checkGuild")
    summary = get_project_symbol_summary(str(tmp_path), "waky3")
    assert symbol["total"] == 1
    assert references["total"] == 1
    assert len(summary["symbols"]) == 3


def test_symbol_summary_drops_unrelated_project_content() -> None:
    """验证带问题词的符号摘要不会把全项目无关内容送入报告或 LLM。"""
    index = {
        "symbols": [
            {
                "symbol_type": "class",
                "symbol_name": "UsdFuseService",
                "file": "php/services/Monitor/Fuse/UsdFuseService.php",
                "line": 10,
                "signature": "class UsdFuseService",
            },
            {
                "symbol_type": "method",
                "symbol_name": "UsdFuseService::isOfficialPaymentTriggered",
                "file": "php/services/Monitor/Fuse/UsdFuseService.php",
                "line": 126,
                "signature": "function isOfficialPaymentTriggered",
            },
            {
                "symbol_type": "class",
                "symbol_name": "GuildController",
                "file": "php/action/Guild.php",
                "line": 4,
                "signature": "class GuildController",
            },
        ],
        "references": [
            {
                "source": "WithdrawalOrderCreateService::create",
                "target": "UsdFuseService::isOfficialPaymentTriggered",
                "file": "php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php",
                "line": 87,
            }
        ],
        "configs": [
            {"key": "anchor.guild", "file": "php/action/Guild.php", "line": 8}
        ],
        "sql": [
            {
                "operation": "SELECT",
                "content": "SELECT * FROM guild",
                "file": "php/action/Guild.php",
                "line": 9,
            }
        ],
    }
    evidence = [
        {
            "file": "php/services/Monitor/Fuse/UsdFuseService.php",
            "line": 126,
            "context": "126: return self::isTriggered($uid, self::OFFICIAL_PAYMENT);",
        }
    ]
    summary = summarize_symbol_index(
        index,
        "waky6",
        queries=["UsdFuseService", "isOfficialPaymentTriggered"],
        evidence=evidence,
    )
    names = {item["symbol_name"] for item in summary["symbols"]}
    assert names == {
        "UsdFuseService",
        "UsdFuseService::isOfficialPaymentTriggered",
    }
    assert not summary["configs"]
    assert not summary["sql"]
    assert summary["references"][0]["target"] == "UsdFuseService::isOfficialPaymentTriggered"

    entries = select_call_chain_entries(
        index,
        ["isOfficialPaymentTriggered"],
        evidence,
    )
    assert "UsdFuseService::isOfficialPaymentTriggered" in entries


def test_call_chain_entries_prioritize_exact_discovered_symbol() -> None:
    """验证完整入口符号优先于源码顺序更早的模糊方法命中。"""
    index = {
        "symbols": [
            {
                "symbol_type": "method",
                "symbol_name": "OtherService::withdrawPreview",
                "file": "php/OtherService.php",
                "line": 10,
            },
            {
                "symbol_type": "method",
                "symbol_name": "AdvanceServices::anchorWithdraw",
                "file": "php/services/Anchor/AdvanceServices.php",
                "line": 577,
            },
        ],
        "references": [],
        "configs": [],
        "sql": [],
    }

    entries = select_call_chain_entries(
        index,
        ["withdraw", "AdvanceServices::anchorWithdraw"],
        [],
        limit=1,
    )

    assert entries == ["AdvanceServices::anchorWithdraw"]
