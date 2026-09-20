from pathlib import Path

import pytest

from app.code_tools import CodeToolError, extract_search_queries, read_file, search_code


def test_search_code_and_read_file(tmp_path: Path) -> None:
    project = tmp_path / "waky3"
    php_file = project / "php" / "action" / "Guild.php"
    php_file.parent.mkdir(parents=True)
    php_file.write_text("<?php\n$config = 'BD_GUILD_JOIN_DAY_LIMIT';\n", encoding="utf-8")

    result = search_code(str(tmp_path), "waky3", "BD_GUILD_JOIN_DAY_LIMIT")
    assert result["total"] == 1
    assert result["matches"][0]["file"] == "php/action/Guild.php"
    assert result["matches"][0]["line"] == 2

    content = read_file(str(tmp_path), "waky3", "php/action/Guild.php", 2, 2)
    assert "2: $config" in content["content"]


def test_code_tools_reject_path_escape(tmp_path: Path) -> None:
    project = tmp_path / "waky3"
    project.mkdir()
    with pytest.raises(CodeToolError):
        read_file(str(tmp_path), "waky3", "../secret.php")


def test_extract_search_queries_deduplicates_terms() -> None:
    queries = extract_search_queries("waky3 公会邀请 BD_GUILD_JOIN_DAY_LIMIT 公会")
    assert queries[:3] == ["waky3", "公会邀请", "BD_GUILD_JOIN_DAY_LIMIT"]
    assert queries.count("公会邀请") == 1
