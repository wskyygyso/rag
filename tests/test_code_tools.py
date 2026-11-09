"""代码检索工具的安全边界和基本行为测试。"""

from pathlib import Path

import pytest

from app.code_tools import (
    CodeToolError,
    extract_generic_anchors,
    extract_search_queries,
    read_file,
    search_code,
    search_code_terms,
)


def test_search_code_and_read_file(tmp_path: Path) -> None:
    """验证搜索结果包含准确的相对路径、行号和文件内容。

    参数:
        - `tmp_path`：pytest 临时目录，类型为 `Path`。
    """
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
    """验证相对路径不能逃逸项目仓库目录。

    参数:
        - `tmp_path`：pytest 临时目录，类型为 `Path`。
    """
    project = tmp_path / "waky3"
    project.mkdir()
    with pytest.raises(CodeToolError):
        read_file(str(tmp_path), "waky3", "../secret.php")


def test_extract_search_queries_deduplicates_terms() -> None:
    """验证问题关键词提取会保留顺序并去重。"""
    queries = extract_search_queries("waky3 公会邀请 BD_GUILD_JOIN_DAY_LIMIT 公会")
    assert queries[:3] == ["waky3", "公会邀请", "BD_GUILD_JOIN_DAY_LIMIT"]
    assert queries.count("公会邀请") == 1


def test_extract_search_queries_keeps_qualified_symbol() -> None:
    """验证 Class::method 形式的 PHP 符号不会被拆成两个关键词。"""
    queries = extract_search_queries("请检查 GuildController::inviteGuild 的调用链")
    assert "GuildController::inviteGuild" in queries


def test_extract_search_queries_keeps_dotted_config_key() -> None:
    """验证点号配置键不会被拆成两个无关关键词。"""
    queries = extract_search_queries("检查 anchor.joinBDGuildDayLimit 配置")
    assert "anchor.joinBDGuildDayLimit" in queries


def test_extract_search_queries_keeps_business_anchor_without_sentence_noise() -> None:
    """验证中文业务问句只保留业务锚点，不凭问题模板展开实现符号。"""
    queries = extract_search_queries("waky6项目 排查触发美元熔断时，业务有哪些具体限制？")
    assert "美元熔断" in queries
    assert "业务有哪些具体限制" not in queries
    assert "UsdFuseService" not in queries
    assert "ERR_USD_EXCHANGE_FUSE_TRIGGERED" not in queries


def test_extract_search_queries_does_not_embed_diamond_withdrawal_implementation() -> None:
    """验证嵌套问题只拆出业务短语，不保存某个项目的提现实现映射。"""
    queries = extract_search_queries(
        "waky6项目 钻石提现最小兑换金额是多少？有手续费嘛？"
    )
    assert "钻石提现最小兑换金额" in queries
    assert "钻石提现最小兑换金额是多少" not in queries
    assert "有手续费嘛" not in queries
    assert not {
        "WithdrawalOrderCreateService",
        "withdrawal_limits",
        "min_diamonds",
        "dollar_to_diamond",
        "buildOfficialSnapshot",
        "calcOfficialSettlement",
        "$fee = round",
        "pay_fee",
    } & set(queries)
    assert len(queries) <= 18


def test_extract_search_queries_does_not_expand_frontend_display_chain() -> None:
    """验证钱包展示问题不会在问题解析阶段写入项目专属调用链。"""
    queries = extract_search_queries("waky4 排查前端展示钻石是根据什么？")
    assert "前端展示钻石" in queries
    assert "根据" not in queries
    assert not {
        "PropControl",
        "getMyRebateCard",
        "is_anchor",
        "DiamondControl",
        "getDiamondGearList",
        "DiamondModel",
        "hsing_pay.t_user_diamonds",
        "RebateDiamondExchangeConfig",
    } & set(queries)
    assert len(queries) <= 18


def test_extract_generic_anchors_does_not_require_domain_mapping() -> None:
    """验证未知业务问题会自动生成可命中文档和注释的中文片段。"""
    anchors = extract_generic_anchors("用户风控冻结阈值是多少？")
    assert "风控冻结" in anchors
    assert "冻结阈值" in anchors
    assert all(value not in {"是多少", "用户风控冻结阈值是多少"} for value in anchors)
    assert len(anchors) <= 24


def test_extract_generic_anchors_keeps_spaceship_fragment_business_terms() -> None:
    """验证年度金主长问句不会把飞船碎片和分配动作截成不可检索长句。"""
    question = "waky6 25年年度金主活动，飞船商品碎片是如何分配的？"
    anchors = extract_generic_anchors(question)
    queries = extract_search_queries(question)
    combined = set(anchors) | set(queries)
    assert {"年度金主", "年度金主活动", "飞船商品碎片", "商品碎片", "飞船", "分配"} <= combined
    assert "2025" not in anchors
    assert "25年" not in combined
    assert "年年度金主活动" not in combined
    assert "飞船商品碎片是分配" not in combined
    assert len(anchors) <= 24


def test_search_code_terms_searches_code_and_docs_once(tmp_path: Path) -> None:
    """验证通用片段批量检索同时覆盖源码和文档，并排除依赖目录。

    参数:
        - `tmp_path`：pytest 临时目录，类型为 `Path`。
    """
    project = tmp_path / "waky9"
    source = project / "php" / "RiskControl.php"
    document = project / "docs" / "risk.md"
    ignored = project / "vendor" / "RiskControl.php"
    for path in (source, document, ignored):
        path.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("<?php\n// 风控冻结阈值由配置控制\nclass RiskControl {}\n", encoding="utf-8")
    document.write_text("# 风控冻结规则\n", encoding="utf-8")
    ignored.write_text("风控冻结阈值", encoding="utf-8")

    result = search_code_terms(str(tmp_path), "waky9", ["风控冻结", "冻结阈值"])
    assert {item["file"] for item in result["matches"]} == {
        "php/RiskControl.php",
        "docs/risk.md",
    }
    assert all(item["matched_queries"] for item in result["matches"])


def test_search_code_terms_prioritizes_miyouactivity_business_source(tmp_path: Path) -> None:
    """验证生产活动代码优先于后台页面和旧报告，即使它们命中相同中文片段。

    参数:
        - `tmp_path`：pytest 临时目录，类型为 `Path`。
    """
    project = tmp_path / "waky6"
    business = project / "waky6_miyouactivity" / "crontab" / "ceremony2025GoldOwner" / "SpaceshipService.php"
    admin = project / "waky6_miyouadmin" / "activity" / "SpaceshipPage.php"
    report = project / "reports" / "spaceship-report.md"
    for path in (business, admin, report):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("商品碎片分配\n", encoding="utf-8")

    result = search_code_terms(
        str(tmp_path),
        "waky6",
        ["商品碎片分配"],
        max_results=3,
    )
    assert result["matches"][0]["file"].endswith("SpaceshipService.php")


def test_search_code_terms_can_match_mixed_case_code_identifiers(tmp_path: Path) -> None:
    """验证跨语言概念检索能忽略大小写命中 PHP 驼峰命名。

    参数:
        - `tmp_path`：pytest 临时目录，类型为 `Path`。
    """
    project = tmp_path / "waky4"
    source = project / "php" / "action" / "DiamondControl.php"
    source.parent.mkdir(parents=True)
    source.write_text("<?php\nclass DiamondControl { public function getDiamondGearList() {} }\n", encoding="utf-8")

    result = search_code_terms(
        str(tmp_path),
        "waky4",
        ["diamond", "gear"],
        case_insensitive=True,
    )

    assert result["matches"]
    assert result["matches"][0]["matched_queries"] == ["diamond", "gear"]


def test_search_code_terms_ranks_all_matches_before_truncation(tmp_path: Path) -> None:
    """验证通用检索先汇总全部命中再排序，不会按扫描顺序丢失业务源码。

    参数:
        - `tmp_path`：pytest 临时目录，类型为 `Path`。
    """
    project = tmp_path / "waky9"
    irrelevant = project / "docs" / "a-noise.md"
    source = project / "php" / "domain" / "PromotionService.php"
    irrelevant.parent.mkdir(parents=True, exist_ok=True)
    source.parent.mkdir(parents=True, exist_ok=True)
    irrelevant.write_text("活动邀请\n" * 80, encoding="utf-8")
    source.write_text("<?php\n// 活动邀请码每日上限和风控冻结阈值\n", encoding="utf-8")

    result = search_code_terms(
        str(tmp_path),
        "waky9",
        ["活动邀请", "活动邀请码", "风控冻结阈值"],
        max_results=1,
    )
    assert result["matches"][0]["file"] == "php/domain/PromotionService.php"


def test_search_code_terms_python_fallback_accepts_batch_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """验证容器未安装 ripgrep 时，120 条批量召回不会触发单次工具上限。

    参数:
        - `tmp_path`：pytest 临时目录，类型为 `Path`。
        - `monkeypatch`：pytest 运行期替换工具，用于模拟镜像内没有 ripgrep。
    """
    project = tmp_path / "waky9"
    source = project / "php" / "RiskControl.php"
    source.parent.mkdir(parents=True)
    source.write_text("<?php\n// 风控冻结阈值\n", encoding="utf-8")
    monkeypatch.setattr("app.code_tools.shutil.which", lambda command: None)

    result = search_code_terms(
        str(tmp_path),
        "waky9",
        ["风控冻结", "冻结阈值"],
        max_results=120,
    )
    assert result["matches"]
    assert result["matches"][0]["file"] == "php/RiskControl.php"


def test_search_code_excludes_generated_gitnexus_and_runtime_content(tmp_path: Path) -> None:
    """验证生成索引和运行态缓存不能抢占真实源码检索结果。

    参数:
        - `tmp_path`：pytest 临时目录，类型为 `Path`。
    """
    project = tmp_path / "waky6"
    source = project / "php" / "UsdFuseService.php"
    cache = project / ".gitnexus" / "cache.php"
    runtime = project / "runtime" / "cache.php"
    for path in (source, cache, runtime):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<?php\n// 美元熔断\n", encoding="utf-8")

    result = search_code(str(tmp_path), "waky6", "美元熔断")
    assert result["total"] == 1
    assert result["matches"][0]["file"] == "php/UsdFuseService.php"


def test_php_repository_fixture_returns_real_context() -> None:
    """验证固定 PHP 仓库样例可以返回方法、配置和错误码所在行。"""
    root = Path(__file__).parent / "fixtures" / "php_repo"
    result = search_code(str(root), "waky3", "BD_OP_FORBIDDEN")
    assert result["total"] == 1
    assert result["matches"][0]["file"] == "php/action/Guild.php"
    assert result["matches"][0]["line"] == 8
