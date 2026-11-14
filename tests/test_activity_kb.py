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
