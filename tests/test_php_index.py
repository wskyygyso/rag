"""PHP 符号解析和调用链分析测试。"""

from pathlib import Path

from app.php_index import index_repository, parse_php_source, trace_call_chain


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
    """验证项目索引和入口调用链节点可以关联到文件和行号。"""
    project = tmp_path / "waky3" / "php"
    project.mkdir(parents=True)
    (project / "Guild.php").write_text(SOURCE, encoding="utf-8")
    indexed = index_repository(str(tmp_path), "waky3")
    assert len(indexed["symbols"]) == 3
    chain = trace_call_chain(str(tmp_path), "waky3", "GuildController::inviteGuild")
    assert chain["nodes"][0]["symbol_name"] == "GuildController::inviteGuild"
