"""年度活动和奖励分配事实分析器。

分析器只读取 `EvidenceLine` 和问题画像，不负责检索或持久化。
"""

import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.domain.diagnosis.analyzers.common import EvidenceLine, _find_line, _lines_by_file, _evidence_lines


def _spaceship_level_configs(
    lines: Sequence[EvidenceLine],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """从 2025 年度金主配置证据中提取各级普通商品碎片规则。

    参数:
        - `lines`：当前诊断命中的源码证据行，类型为 `Sequence[EvidenceLine]`。
          只有路径属于 `ceremony2025GoldOwner/config.php` 的上下文会参与解析。

    Returns:
        `(levels, rate_refs)`。`levels` 每项包含等级、碎片总量、固定排名奖励、
        固定奖励合计和配置引用；`rate_refs` 是三档惊喜配置的证据引用。结果仅
        覆盖本次取证窗口实际出现的等级，缺失窗口不会被默认数值补齐。
    """
    levels: List[Dict[str, Any]] = []
    rate_refs: List[str] = []
    seen_levels = set()
    for file_name, file_lines in _lines_by_file(lines).items():
        lowered_file = file_name.casefold()
        if not lowered_file.endswith("ceremony2025goldowner/config.php"):
            continue
        for index, marker in enumerate(file_lines):
            if "'ordinary_prizes' =>" not in marker.content:
                continue
            level_id: Optional[int] = None
            for previous in reversed(file_lines[max(0, index - 30):index]):
                level_match = re.fullmatch(
                    r"\s*'id'\s*=>\s*(\d+)\s*,?\s*",
                    previous.content,
                )
                if level_match:
                    level_id = int(level_match.group(1))
                    break
            if level_id is None:
                continue
            section = file_lines[index:min(len(file_lines), index + 30)]
            fragment_line: Optional[EvidenceLine] = None
            rate_line: Optional[EvidenceLine] = None
            fixed_line: Optional[EvidenceLine] = None
            fixed_items: List[Tuple[int, int, int]] = []
            fixed_refs: List[str] = []
            for section_line in section:
                content = section_line.content
                if fragment_line is None:
                    fragment_match = re.search(r"'fragments'\s*=>\s*(\d+)", content)
                    if fragment_match:
                        fragment_line = section_line
                if rate_line is None:
                    rate_match = re.search(r"'minimum_guarantee_rate'\s*=>\s*(\d+)", content)
                    if rate_match:
                        rate_line = section_line
                threshold = re.search(
                    r"\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]",
                    content,
                )
                if threshold and (rate_line is None or section_line.line < rate_line.line):
                    fixed_items.append(
                        (int(threshold.group(1)), int(threshold.group(2)), int(threshold.group(3)))
                    )
                    fixed_refs.append(section_line.ref)
                    if fixed_line is None:
                        fixed_line = section_line
                surprise_match = re.search(
                    r"(\d+)\s*=>\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]",
                    content,
                )
                if surprise_match and section_line.ref not in rate_refs:
                    rate_refs.append(section_line.ref)
            if fragment_line is None:
                continue
            fragment_match = re.search(r"\d+", fragment_line.content)
            if fragment_match is None:
                continue
            fragments = int(fragment_match.group(0))
            dedupe_key = (level_id, marker.line)
            if dedupe_key in seen_levels:
                continue
            seen_levels.add(dedupe_key)
            fixed_total = sum((end - start + 1) * amount for start, end, amount in fixed_items)
            levels.append(
                {
                    "level": level_id,
                    "fragments": fragments,
                    "fixed": fixed_items,
                    "fixed_total": fixed_total,
                    "marker_ref": marker.ref,
                    "fragment_ref": fragment_line.ref,
                    "rate_ref": rate_line.ref if rate_line else "",
                    "fixed_refs": fixed_refs,
                }
            )
    levels.sort(key=lambda item: int(item["level"]))
    return levels, list(dict.fromkeys(rate_refs))

def _analyze_spaceship_fragments(lines: Sequence[EvidenceLine]) -> List[Dict[str, Any]]:
    """解释年度金主飞船普通商品碎片的配置和分配顺序。

    参数:
        - `lines`：已通过代码检索取得的源码行，类型为 `Sequence[EvidenceLine]`。
          解析同时依赖等级配置、`SpaceshipService` 的普通奖励公式、
          `SpaceshipOrdinaryPrize` 的领奖互斥规则和 `SurprisePrize` 的随机发奖。

    Returns:
        按业务执行顺序排列的确定性事实。每条事实只引用实际命中的源码；缺少
        等级配置或公式时不会猜测具体人数和最终每人碎片数。
    """
    facts: List[Dict[str, Any]] = []
    levels, rate_refs = _spaceship_level_configs(lines)
    if levels:
        level_descriptions = []
        level_refs = []
        for item in levels:
            fixed_text = "；".join(
                f"{start}-{end} 名每人 {amount}"
                for start, end, amount in item["fixed"]
            ) or "未配置固定排名奖励"
            level_descriptions.append(
                f"等级 {item['level']} 总量 {item['fragments']:,}，"
                f"固定奖励 {fixed_text}（合计 {item['fixed_total']:,}）"
            )
            level_refs.extend([item["fragment_ref"], *item["fixed_refs"]])
        facts.append(
            {
                "category": "碎片总量与固定榜",
                "statement": "2025 年度金主配置的普通商品碎片总量为："
                + "；".join(level_descriptions)
                + "。固定榜奖励先从该等级总碎片中扣除。",
                "evidence_refs": list(dict.fromkeys(level_refs)),
            }
        )

    fixed_formula = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$fragments = intval", "$item[1]", "$item[0]", "$item[2]"),
    )
    surplus_formula = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$surplus = max(0", "$fragments - $topFixed"),
    )
    if fixed_formula and surplus_formula:
        facts.append(
            {
                "category": "分配顺序",
                "statement": "每个固定排名档位的奖励合计为“人数 × 每人碎片”，"
                "全部固定榜奖励先发放；随后用该等级碎片总量减去固定奖励，"
                "得到保底池与惊喜池的共同基数。",
                "evidence_refs": [fixed_formula.ref, surplus_formula.ref],
            }
        )

    minimum_rate = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$minimum = max(0", "minimum_guarantee_rate", "$surplus"),
    )
    minimum_average = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$averageMinimum = max(0", "$minimum", "$averageUsers"),
    )
    minimum_fallback = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("if ($averageMinimum <= 0)",),
    )
    minimum_total = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$totalMinimum = $averageMinimum * $averageUsers",),
    )
    minimum_surplus = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$minimumSur = max(0", "$minimum - $totalMinimum"),
    )
    if minimum_rate and minimum_average:
        refs = [minimum_rate.ref, minimum_average.ref]
        statement = (
            "扣除固定榜后，剩余碎片的 60% 进入保底池，面向固定榜之后的剩余用户"
            "均分；每人先取“保底池 ÷ 剩余用户数”的向下整数。"
        )
        if minimum_fallback:
            statement += "若人均不足 1，则改为每人 1，发完保底池即止。"
            refs.append(minimum_fallback.ref)
        if minimum_total and minimum_surplus:
            statement += "保底池发完后的整数除法余数会并入惊喜池。"
            refs.extend([minimum_total.ref, minimum_surplus.ref])
        facts.append({"category": "保底池", "statement": statement, "evidence_refs": refs})

    surprise_total = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$surprise = intval", "surprise_rate", "$surplus"),
    )
    surprise_remainder = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$sur = max(0", "$surplus - $minimum - $surprise", "$minimumSur"),
    )
    surprise_user_rate = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$rowSurpriseUser = intval", "$userRate * $totalUsers / 100"),
    )
    surprise_per_user = _find_line(
        lines,
        file_suffix="SpaceshipService.php",
        contains=("$rowFragments = max(1", "$rowSurprise", "$rowSurpriseUser"),
    )
    if surprise_total and surprise_user_rate and surprise_per_user:
        refs = [surprise_total.ref, surprise_user_rate.ref, surprise_per_user.ref]
        if surprise_remainder:
            refs.append(surprise_remainder.ref)
        facts.append(
            {
                "category": "惊喜池",
                "statement": "扣除固定榜后的剩余碎片有 40% 进入惊喜池，并把前两档"
                "取整余数和保底余数追加进来。惊喜档按该等级总用户数的 10%、20%、"
                "30% 选择人数，分别分走惊喜池的 30%、50% 和最后剩余碎片；每档先按"
                "“本档碎片 ÷ 本档人数”向下取整，差额由档内最后一名和逐人补 1 收口。",
                "evidence_refs": refs,
            }
        )

    random_pool = _find_line(
        lines,
        file_suffix="SurprisePrize.php",
        contains=("$randomIndexes = $this->generateRandomIndexes", "$totalUsers", "$surpriseUsers"),
    )
    random_range = _find_line(
        lines,
        file_suffix="SurprisePrize.php",
        contains=("range(1, $totalUsers)",),
    )
    random_shuffle = _find_line(
        lines,
        file_suffix="SurprisePrize.php",
        contains=("shuffle($indexes)",),
    )
    random_slice = _find_line(
        lines,
        file_suffix="SurprisePrize.php",
        contains=("array_slice($indexes", "$count"),
    )
    random_assignment = _find_line(
        lines,
        file_suffix="SurprisePrize.php",
        contains=("'rank_id' => $randomIndexes[$currentIndex]", "'fragments' => $rowFragments"),
    )
    if random_pool and random_range and random_shuffle and random_assignment:
        facts.append(
            {
                "category": "惊喜随机发放",
                "statement": "惊喜档不是按排名顺序发放，而是从该等级全部用户名次中"
                "生成不重复随机序列，再按档位依次取出中奖名次；因此固定榜用户也有"
                "机会同时获得惊喜碎片。",
                "evidence_refs": [
                    random_pool.ref,
                    random_range.ref,
                    random_shuffle.ref,
                    *([random_slice.ref] if random_slice else []),
                    random_assignment.ref,
                ],
            }
        )

    prize_top = _find_line(
        lines,
        file_suffix="SpaceshipOrdinaryPrize.php",
        contains=("$returnData['top_fragments'] = $this->getTopRankFragments()",),
    )
    prize_minimum = _find_line(
        lines,
        file_suffix="SpaceshipOrdinaryPrize.php",
        contains=("if ($returnData['top_fragments'] <= 0)",),
    )
    prize_surprise = _find_line(
        lines,
        file_suffix="SpaceshipOrdinaryPrize.php",
        contains=("$returnData['surprise_fragments'] = $this->getSurprise()",),
    )
    if prize_top and prize_minimum and prize_surprise:
        facts.append(
            {
                "category": "领奖叠加规则",
                "statement": "固定榜奖励和保底奖励互斥：进入固定榜就不再领取保底；"
                "惊喜碎片单独计算并追加，所以固定榜或保底用户都可能再叠加惊喜奖励。",
                "evidence_refs": [prize_top.ref, prize_minimum.ref, prize_surprise.ref],
            }
        )

    inherited_template = _find_line(
        lines,
        file_suffix="SpaceshipConfig.php",
        contains=("if ($level > $maxLevel)",),
    )
    copied_config = _find_line(
        lines,
        file_suffix="SpaceshipConfig.php",
        contains=("$cfg = $levels[$maxLevel]",),
    )
    if inherited_template and copied_config:
        facts.append(
            {
                "category": "高等级模板",
                "statement": "配置显式列出的普通商品碎片等级为 1 至 4；超过最高等级时"
                "沿用第 4 级的整份普通奖励模板，只重新计算等级 ID 和升级所需燃料值。",
                "evidence_refs": [inherited_template.ref, copied_config.ref],
            }
        )

    if rate_refs and levels:
        rates = "60% 保底、40% 惊喜，惊喜三档用户/碎片比例依次为 10%/30%、20%/50%、30%/剩余"
        facts.append(
            {
                "category": "配置比例",
                "statement": f"本次证据窗口中的各等级统一配置为：{rates}。",
                "evidence_refs": rate_refs[:12],
            }
        )
    return facts
