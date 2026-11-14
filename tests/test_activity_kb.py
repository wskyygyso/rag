"""活动知识库构建、实体链接与相关实现文件选择的回归测试。

测试只使用临时目录中的精简配置和内存证据，不访问真实 waky3 仓库、数据库或模型。
所有断言验证“别名来自数据”“分差不足必须放弃”“全局配置语言包可建立 VIP 别名”
以及“判定入口只来自链接活动实现文件”等边界。
"""

from pathlib import Path

from app.domain.diagnosis.activity_kb import (
    ActivityRecord,
    build_activity_knowledge,
    link_activity,
    select_related_files,
)
from app.domain.diagnosis.analyzers.activity_kb_facts import (
    activity_level_table_facts,
    activity_structured_facts,
)
from app.domain.diagnosis.analyzers.common import EvidenceLine
from app.domain.diagnosis.analyzers.configuration import (
    activity_decision_source_facts,
)


def _write(root: Path, relative: str, text: str) -> None:
    """在临时仓库中写入一个配置或语言包文件。

    参数:
        - `root`：临时仓库根目录，类型为 `Path`。
        - `relative`：项目内相对路径（含项目目录），类型为 `str`。
        - `text`：文件完整文本，类型为 `str`。

    Returns:
        无返回值。父目录不存在时自动创建，保证构建函数能按真实目录扫描。
    """
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _lines(file_name: str, contents: list[str]) -> list[EvidenceLine]:
    """构造带连续行号的源码证据行。

    参数:
        - `file_name`：项目内相对路径，类型为 `str`。
        - `contents`：从第 1 行开始的源码文本，类型为 `list[str]`。

    Returns:
        行号从 1 递增的 `EvidenceLine` 列表，供判定入口分析器消费。
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


_STRUCTURED_CONFIG = """<?php
// 'vol' => 1,
return [
    'vol' => 2,
    'year' => 2026,
    'title' => 'demo party',
    'levels' => [
        [
            'id' => 1,
            'limit' => getByTestMode(100, 200),
            'name' => ['zh' => '初级'],
            'prizes' => [['id' => 1]],
        ],
        [
            'id' => 2,
            'limit' => 300,
            'name' => 'Advanced',
            'prizes' => [['id' => 2], ['id' => 3]],
        ],
    ],
    'prizes' => [
        1 => ['id' => 1, 'type' => 'goods', 'num' => 1, 'days' => 5, 'name' => 'Lucky box'],
        2 => ['id' => 2, 'type' => 'goods', 'num' => 1, 'days' => 7, 'name' => 'Frame'],
    ],
];
"""

_MINIMAL_CONFIG = """<?php
return [
    'title' => 'minimal activity',
];
"""

_LOVE_BALL_CONFIG = """<?php
$_dateTime = new DateTime('now');
return [
    'title' => 'cinta pesta dansa',
    'stime' => $_dateTime->modify('2026-05-26 10:30:00')->getTimestamp(),
    'etime' => $_dateTime->modify('2026-06-03 23:59:59')->getTimestamp(),
    'levels' => [
        ['id' => 1, 'limit' => 100000, 'name' => ['zh' => '业余'], 'prizes' => [['id' => 1]]],
    ],
    'pushmsg' => ['zh' => ['target' => '#心动的旋律#心动相遇！']],
];
"""

_OTHER_ACTIVITY_CONFIG = """<?php
return [
    'levels' => [
        ['id' => 1, 'limit' => 500000, 'name' => ['zh' => '其他称谓']],
    ],
];
"""


def _build(root: Path) -> tuple:
    """构建临时仓库的活动知识库。

    参数:
        - `root`：临时仓库根目录，类型为 `Path`。

    Returns:
        `build_activity_knowledge` 返回的活动记录元组。测试仓库根目录直接包含
        子项目目录，因此项目标识使用空字符串。
    """
    return build_activity_knowledge(str(root), "")


def test_aliases_come_from_repository_data_only(tmp_path: Path) -> None:
    """验证活动别名只来自真实配置文案。

    参数:
        - `tmp_path`：pytest 提供的临时目录，类型为 `Path`。

    Returns:
        无返回值。断言标题、等级名称和井号文案会成为别名，而仓库中不存在
        的“鉴定大师”不会被凭空生成。
    """
    _write(
        tmp_path,
        "waky3_miyouactivity/crontab/loveBall/config.php",
        _LOVE_BALL_CONFIG,
    )
    records = _build(tmp_path)
    record = next(item for item in records if item.activity_id == "loveBall")
    assert "心动的旋律" in record.aliases_zh
    assert "业余" in record.level_names
    assert all("鉴定大师" not in alias for item in records for alias in item.aliases_zh)


def test_link_by_chinese_bracket_alias(tmp_path: Path) -> None:
    """验证问题能通过数据中的中文活动名链接到唯一活动。

    参数:
        - `tmp_path`：pytest 提供的临时目录，类型为 `Path`。

    Returns:
        无返回值。断言“心动的旋律”只命中含该文案的 `loveBall`，不会落到其他
        同时有等级表的活动。
    """
    _write(
        tmp_path,
        "waky3_miyouactivity/crontab/loveBall/config.php",
        _LOVE_BALL_CONFIG,
    )
    _write(
        tmp_path,
        "waky3_miyouactivity/crontab/other/config.php",
        _OTHER_ACTIVITY_CONFIG,
    )
    link = link_activity("心动的旋律五个等级的门槛和奖励是什么？", _build(tmp_path))
    assert link is not None
    assert link.record.activity_id == "loveBall"
    assert link.ambiguous is False


def test_link_rejects_ambiguous_activity(tmp_path: Path) -> None:
    """验证分差不足时放弃链接，而不是猜测活动。

    参数:
        - `tmp_path`：pytest 提供的临时目录，类型为 `Path`。

    Returns:
        无返回值。两个活动都只有等级结构、问题中不存在的活动名没有别名时，
        链接结果必须为空或标记歧义。
    """
    _write(
        tmp_path,
        "waky3_miyouactivity/crontab/alpha/config.php",
        _OTHER_ACTIVITY_CONFIG,
    )
    _write(
        tmp_path,
        "waky3_miyouactivity/crontab/beta/config.php",
        _OTHER_ACTIVITY_CONFIG,
    )
    link = link_activity("鉴定大师活动四个等级的中文名称和门槛分别是什么？", _build(tmp_path))
    assert link is None or link.ambiguous is True


def test_global_config_lang_package_builds_vip_alias(tmp_path: Path) -> None:
    """验证全局配置可通过同名中文语言包建立 VIP 别名。

    参数:
        - `tmp_path`：pytest 提供的临时目录，类型为 `Path`。

    Returns:
        无返回值。`noble.php` 与 `lang/zh/noble.php` 一起构建后，“VIP 3”问题
        能链接到 `noble`，证明别名来自数据而非硬编码。
    """
    _write(
        tmp_path,
        "waky3_miyouphp/php/config/noble.php",
        """<?php
return [
    'level' => [
        \\Model\\UserNobleModel::LEVEL_RANGER => ['score' => getByTestMode(1000, 1000000)],
        \\Model\\UserNobleModel::LEVEL_KNIGHT => ['score' => getByTestMode(2000, 2000000)],
        \\Model\\UserNobleModel::LEVEL_VISCOUNT => ['score' => getByTestMode(3000, 5000000)],
    ],
];
""",
    )
    _write(
        tmp_path,
        "waky3_miyouphp/php/lang/zh/noble.php",
        """<?php
return [
    'ranger' => 'VIP 1',
    'knight' => 'VIP 2',
    'viscount' => 'VIP 3',
];
""",
    )
    link = link_activity("VIP 3 的升级门槛在测试环境和正式环境分别是多少？", _build(tmp_path))
    assert link is not None
    assert link.record.activity_id == "noble"
    assert link.record.level_key == "level"
    assert link.record.level_field == "score"


def test_time_window_is_extracted_from_config(tmp_path: Path) -> None:
    """验证起止时间从配置日期表达式提取。

    参数:
        - `tmp_path`：pytest 提供的临时目录，类型为 `Path`。

    Returns:
        无返回值。断言活动记录保存了 `stime`/`etime` 字面量，供时间类问题使用。
    """
    _write(
        tmp_path,
        "waky3_miyouactivity/crontab/loveBall/config.php",
        _LOVE_BALL_CONFIG,
    )
    record = next(item for item in _build(tmp_path) if item.activity_id == "loveBall")
    assert record.stime == "2026-05-26 10:30:00"
    assert record.etime == "2026-06-03 23:59:59"


def test_select_related_files_respects_activity_boundaries() -> None:
    """验证相关实现文件只来自链接活动本身。

    参数:
        无。

    Returns:
        无返回值。断言活动目录只选同目录入口/服务/控制器，全局配置按
        服务优先并排除配置文件、第三方库和测试目录。
    """
    activity = ActivityRecord(
        activity_id="loveBall",
        marker="crontab",
        config_file="waky3_miyouactivity/crontab/loveBall/config.php",
        level_field="limit",
    )
    known = [
        "waky3_miyouactivity/crontab/loveBall/config.php",
        "waky3_miyouactivity/crontab/loveBall/service/LoveBallTaskService.php",
        "waky3_miyouactivity/crontab/loveBall/loveBallBoot.php",
        "waky3_miyouactivity/action_activity/loveBallControl.php",
        "waky3_miyouactivity/crontab/other/otherBoot.php",
        "waky3_miyouactivity/vendor/loveBall/third.php",
    ]
    related = select_related_files(activity, known, limit=6)
    assert "waky3_miyouactivity/crontab/loveBall/loveBallBoot.php" in related
    assert "waky3_miyouactivity/action_activity/loveBallControl.php" in related
    assert "waky3_miyouactivity/crontab/loveBall/service/LoveBallTaskService.php" in related
    assert "waky3_miyouactivity/crontab/other/otherBoot.php" not in related
    assert "waky3_miyouactivity/vendor/loveBall/third.php" not in related

    noble = ActivityRecord(
        activity_id="noble",
        marker="config",
        config_file="waky3_miyouphp/php/config/noble.php",
        level_field="score",
    )
    known = [
        "waky3_miyouphp/php/services/noble/NobleService.php",
        "waky3_miyouphp/php/services/NewNoble/NewNobleScoreServices.php",
        "waky3_miyouphp/php/model/UserNobleModel.php",
        "waky3_miyouphp/php/config/prize.php",
        "waky3_miyouphp/vendor/noble/third.php",
    ]
    related = select_related_files(noble, known, limit=6)
    assert related[0] == "waky3_miyouphp/php/services/NewNoble/NewNobleScoreServices.php"
    assert "waky3_miyouphp/php/model/UserNobleModel.php" in related
    assert "waky3_miyouphp/php/config/prize.php" not in related
    assert "waky3_miyouphp/vendor/noble/third.php" not in related


def test_decision_source_facts_read_linked_config_only() -> None:
    """验证等级判定入口来自链接活动的实现文件。

    参数:
        无。

    Returns:
        无返回值。断言事实包含 `NewNobleScoreServices::getLevel` 和
        `noble.level`，而不是初始化方法 `initCfg` 或其他活动文件。
    """
    file_name = "waky3_miyouphp/php/services/NewNoble/NewNobleScoreServices.php"
    lines = _lines(
        file_name,
        [
            "<?php",
            "class NewNobleScoreServices",
            "{",
            "    private function initCfg()",
            "    {",
            "        $this->levelCfg = array_map(fn($v) => $v['score'], Config::get('noble.level'));",
            "    }",
            "    public function getLevel(int $score): int",
            "    {",
            "        return $this->levelCfg[0] ?? 0;",
            "    }",
            "}",
        ],
    )
    facts = activity_decision_source_facts(
        lines,
        "VIP 升级的等级判定逻辑在哪里、按什么字段计算？",
        {
            "activity_id": "noble",
            "level_key": "level",
            "level_field": "score",
            "related_files": [file_name],
        },
    )
    assert facts
    statement = facts[0]["statement"]
    assert "NewNobleScoreServices::getLevel" in statement
    assert "noble.level" in statement


def _linked_payload(record, **overrides) -> dict:
    """把活动记录转换成工作流写入的业务层快照。

    参数:
        - `record`：已构建的活动记录，类型为 `ActivityRecord`。
        - `overrides`：需要覆盖的快照字段，类型为关键字参数；用于构造未登记、
          空等级表等降级场景。

    Returns:
        与 `retrieval_plan.activity_link` 同构的字典，等级和奖励已转换为
        JSON 安全的列表，供事实函数直接消费。
    """
    payload = {
        "available": True,
        "activity_id": record.activity_id,
        "config_file": record.config_file,
        "ambiguous": False,
        "volume": record.volume,
        "year": record.year,
        "vol_ref": record.vol_ref,
        "year_ref": record.year_ref,
        "levels": [
            {
                "ordinal": level.ordinal,
                "threshold": level.threshold,
                "name": level.name,
                "prize_ids": list(level.prize_ids),
                "ref": level.source_ref,
            }
            for level in record.levels
        ],
        "prizes": [
            {"id": prize.prize_id, "name": prize.name, "ref": prize.source_ref}
            for prize in record.prizes
        ],
        "backend_registered": record.backend_registered,
        "backend_config_file": record.backend_config_file,
    }
    payload.update(overrides)
    return payload


def test_structured_fields_are_extracted_from_config(tmp_path: Path) -> None:
    """验证期次、年份、等级表和奖励定义从配置字面量解析。

    参数:
        - `tmp_path`：pytest 提供的临时目录，类型为 `Path`。

    Returns:
        无返回值。断言注释中的示例期次被跳过，真实 `vol`/`year`、等级门槛、
        中文/英文等级名称和奖励引用 id 均被保存。
    """
    _write(
        tmp_path,
        "waky3_miyouactivity/crontab/demo/config.php",
        _STRUCTURED_CONFIG,
    )
    record = next(item for item in _build(tmp_path) if item.activity_id == "demo")
    assert record.volume == "2"
    assert record.year == "2026"
    assert record.vol_ref.endswith(":4")
    assert record.year_ref.endswith(":5")
    assert len(record.levels) == 2
    first, second = record.levels
    assert first.ordinal == 1
    assert first.threshold == "测试 100 / 正式 200"
    assert first.name == "初级"
    assert first.prize_ids == ("1",)
    assert second.name == "Advanced"
    assert second.prize_ids == ("2", "3")
    assert [prize.name for prize in record.prizes] == ["Lucky box", "Frame"]


def test_period_and_backend_facts_use_linked_snapshot(tmp_path: Path) -> None:
    """验证期次与后台登记事实直接读取知识库快照。

    参数:
        - `tmp_path`：pytest 提供的临时目录，类型为 `Path`。

    Returns:
        无返回值。断言“第几期”输出配置期次，“后台登记”输出后台活动名；
        未登记活动明确说明不在登记路径。
    """
    _write(
        tmp_path,
        "waky3_miyouactivity/crontab/demo/config.php",
        _STRUCTURED_CONFIG,
    )
    record = next(item for item in _build(tmp_path) if item.activity_id == "demo")
    payload = _linked_payload(record)
    period = activity_structured_facts(payload, "这个活动是第几期？")
    assert [fact["category"] for fact in period] == ["活动期次"]
    assert "第 2 期" in period[0]["statement"]
    assert period[0]["evidence_refs"]

    backend = activity_structured_facts(payload, "这个活动后台登记了吗？")
    assert [fact["category"] for fact in backend] == ["后台活动登记"]
    assert "后台活动名为「demo」" in backend[0]["statement"]

    unregistered = _linked_payload(
        record,
        backend_registered=False,
        backend_config_file="",
    )
    missing = activity_structured_facts(unregistered, "后台活动在哪里配置？")
    assert "不在后台活动登记路径" in missing[0]["statement"]


def test_level_table_facts_render_names_and_prize_refs(tmp_path: Path) -> None:
    """验证等级表兜底事实包含名称、门槛和奖励名称。

    参数:
        - `tmp_path`：pytest 提供的临时目录，类型为 `Path`。

    Returns:
        无返回值。断言奖励 id 被解析为可读名称，证据引用指向活动配置；
        点名“第 2 档”时只输出该等级。
    """
    _write(
        tmp_path,
        "waky3_miyouactivity/crontab/demo/config.php",
        _STRUCTURED_CONFIG,
    )
    record = next(item for item in _build(tmp_path) if item.activity_id == "demo")
    facts = activity_level_table_facts(
        _linked_payload(record),
        "每个等级的门槛和奖励是什么？",
    )
    assert len(facts) == 2
    assert "初级" in facts[0]["statement"]
    assert "测试 100 / 正式 200" in facts[0]["statement"]
    assert "Lucky box" in facts[0]["statement"]
    assert "Frame" in facts[1]["statement"]
    assert all(
        reference.startswith(record.config_file)
        for fact in facts
        for reference in fact["evidence_refs"]
    )

    ordinal = activity_level_table_facts(
        _linked_payload(record),
        "第 2 档的门槛是多少？",
    )
    assert len(ordinal) == 1
    assert "Advanced" in ordinal[0]["statement"]


def test_backend_registration_prefers_crontab_config(tmp_path: Path) -> None:
    """验证同名活动按后台规则去重并标记后台登记。

    参数:
        - `tmp_path`：pytest 提供的临时目录，类型为 `Path`。

    Returns:
        无返回值。`Logic/boot/config.php` 先被扫描到时，随后出现的
        `crontab/config.php` 必须替换该记录，最终只保留后台真正读取的版本。
    """
    _write(
        tmp_path,
        "waky3_miyouactivity/crontab/demo/config.php",
        _STRUCTURED_CONFIG,
    )
    _write(
        tmp_path,
        "waky3_miyouactivity/Logic/demo/boot/config.php",
        _MINIMAL_CONFIG,
    )
    matches = [item for item in _build(tmp_path) if item.activity_id == "demo"]
    assert len(matches) == 1
    record = matches[0]
    assert record.marker == "crontab"
    assert record.backend_registered is True
    assert record.backend_config_file == record.config_file


def test_logic_boot_config_is_covered_and_registered(tmp_path: Path) -> None:
    """验证只有 Logic boot 配置的活动同样进入知识库并标记登记。

    参数:
        - `tmp_path`：pytest 提供的临时目录，类型为 `Path`。

    Returns:
        无返回值。断言配置来自 `Logic/<name>/boot/config.php`，后台登记为真，
        结构化字段仍可解析。
    """
    _write(
        tmp_path,
        "waky3_miyouactivity/Logic/demo/boot/config.php",
        _STRUCTURED_CONFIG,
    )
    record = next(item for item in _build(tmp_path) if item.activity_id == "demo")
    assert record.marker == "Logic"
    assert record.config_file.endswith("Logic/demo/boot/config.php")
    assert record.backend_registered is True
    assert record.volume == "2"
    assert len(record.levels) == 2


def test_global_config_is_not_backend_registered(tmp_path: Path) -> None:
    """验证全局配置不属于后台活动登记路径。

    参数:
        - `tmp_path`：pytest 提供的临时目录，类型为 `Path`。

    Returns:
        无返回值。断言 `php/config/noble.php` 构建出的记录
        `backend_registered=False`，后台事实显示未登记。
    """
    _write(tmp_path, "waky3_miyouphp/php/config/noble.php", _MINIMAL_CONFIG)
    record = next(item for item in _build(tmp_path) if item.activity_id == "noble")
    assert record.backend_registered is False
    assert record.backend_config_file == ""
    fact = activity_structured_facts(
        _linked_payload(record),
        "这个活动在后台登记了吗？",
    )
    assert "不在后台活动登记路径" in fact[0]["statement"]


def test_out_of_range_level_ordinal_returns_no_facts(tmp_path: Path) -> None:
    """验证点名不存在的等级时不回退到整张等级表。

    参数:
        - `tmp_path`：pytest 提供的临时目录，类型为 `Path`。

    Returns:
        无返回值。“等级 99”没有被 `第 N 档` 句式覆盖，也必须识别为序号，
        并返回空事实，让上层继续给出等级缺口而不是整张 1-2 级表。
    """
    _write(
        tmp_path,
        "waky3_miyouactivity/crontab/demo/config.php",
        _STRUCTURED_CONFIG,
    )
    record = next(item for item in _build(tmp_path) if item.activity_id == "demo")
    facts = activity_level_table_facts(
        _linked_payload(record),
        "这个活动等级 99 的门槛是多少？",
    )
    assert facts == []


def test_missing_structured_data_degrades_to_empty(tmp_path: Path) -> None:
    """验证缺少结构化字段的配置不会抛异常或生成事实。

    参数:
        - `tmp_path`：pytest 提供的临时目录，类型为 `Path`。

    Returns:
        无返回值。断言没有 `vol/year/levels/prizes` 时字段为空，期次与等级
        事实函数返回空列表，由上层继续按缺口或其他分析器处理。
    """
    _write(
        tmp_path,
        "waky3_miyouactivity/crontab/minimal/config.php",
        _MINIMAL_CONFIG,
    )
    record = next(item for item in _build(tmp_path) if item.activity_id == "minimal")
    assert record.volume == ""
    assert record.year == ""
    assert record.levels == ()
    assert record.prizes == ()
    payload = _linked_payload(record)
    assert activity_structured_facts(payload, "这个活动是第几期？") == []
    assert activity_level_table_facts(payload, "每个等级的门槛是多少？") == []
