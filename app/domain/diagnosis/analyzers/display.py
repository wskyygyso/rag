"""前端余额和配置展示事实分析器。

分析器只读取 `EvidenceLine` 和问题画像，不负责检索或持久化。
"""

import math
import re
from typing import Any, Dict, List, Sequence

from app.domain.diagnosis.analyzers.common import EvidenceLine, _find_line, _evidence_lines


def _analyze_frontend_diamond_display(lines: Sequence[EvidenceLine]) -> List[Dict[str, Any]]:
    """分析 waky4 钱包页钻石展示条件、余额来源和区域档位配置。

    参数:
        - `lines`：围绕“前端展示钻石根据什么”检索到的钱包接口、登录校验、
          主播身份、余额模型、公会区域和档位配置源码，类型为
          `Sequence[EvidenceLine]`。分析器只使用实际命中的代码行，不会把
          客户端渲染行为或缓存策略补写成源码事实。

    Returns:
        按接口入口、展示门槛、身份判断、余额、区域、档位、响应字段和读取
        一致性排列的确定性事实。缺少任一关键数据源时不会伪造该层结论，调用方
        可据 `evidence_refs` 继续核查。
    """
    facts: List[Dict[str, Any]] = []

    page_route = _find_line(
        lines,
        file_suffix="php/config/auth.php",
        contains=("Action/Diamond.getDiamondGearList", "钱包页"),
    )
    controller_entry = _find_line(
        lines,
        file_suffix="DiamondControl.php",
        contains=("function getDiamondGearList",),
    )
    login_guard = _find_line(
        lines,
        file_suffix="DiamondControl.php",
        contains=("\\Request::CheckLogin()",),
    )
    if page_route and controller_entry and login_guard:
        facts.append(
            {
                "category": "页面入口",
                "statement": "waky4 钱包页钻石区对应 `Action/Diamond.getDiamondGearList`；"
                "接口先调用 `Request::CheckLogin()` 取得当前登录用户，未登录请求不会进入"
                "钱包数据读取。",
                "evidence_refs": [page_route.ref, controller_entry.ref, login_guard.ref],
            }
        )

    wallet_card_entry = _find_line(
        lines,
        file_suffix="PropControl.php",
        contains=("function getMyRebateCard",),
    )
    wallet_anchor_flag = _find_line(
        lines,
        file_suffix="PropControl.php",
        contains=("$rebateCard['is_anchor']", "isAnchor($uid)", "前端展示钻石档位和用户钻石"),
    )
    if wallet_card_entry and wallet_anchor_flag:
        facts.append(
            {
                "category": "钱包页显隐",
                "statement": "钱包页的 `Action/Prop.getMyRebateCard` 还会返回 `is_anchor`；"
                "源码注释明确说明前端依据该字段展示钻石档位和用户钻石。该字段与下面"
                "余额、档位接口共同构成钱包页钻石区的显示契约。",
                "evidence_refs": [wallet_card_entry.ref, wallet_anchor_flag.ref],
            }
        )

    anchor_guard = _find_line(
        lines,
        file_suffix="DiamondControl.php",
        contains=("if (!$anchor)",),
    )
    hidden_marker = _find_line(
        lines,
        file_suffix="DiamondControl.php",
        contains=("is_show'] = 0",),
    )
    anchor_method = _find_line(
        lines,
        file_suffix="usermodel.php",
        contains=("function isAnchor",),
    )
    anchor_query = _find_line(
        lines,
        file_suffix="usermodel.php",
        contains=("TAnchor", "where('uid'", "where('status',0)"),
    )
    if anchor_guard and hidden_marker and anchor_method and anchor_query:
        facts.append(
            {
                "category": "展示条件",
                "statement": "接口先判断登录用户是否为主播；非主播只返回 `is_show = 0` 并立即"
                "结束，不返回余额和兑换档位。主播判定条件是 `data_analyse.t_anchor` 中存在"
                "同一 UID 且 `status = 0` 的记录。",
                "evidence_refs": [
                    anchor_guard.ref,
                    hidden_marker.ref,
                    anchor_method.ref,
                    anchor_query.ref,
                ],
            }
        )

    balance_call = _find_line(
        lines,
        file_suffix="DiamondControl.php",
        contains=("getUserDiamonds($uid)",),
    )
    balance_field = _find_line(
        lines,
        file_suffix="DiamondControl.php",
        contains=("$data['diamonds']", "$userDiamonds['diamonds']"),
    )
    balance_method = _find_line(
        lines,
        file_suffix="DiamondModel.php",
        contains=("function getUserDiamonds",),
    )
    balance_sql = _find_line(
        lines,
        file_suffix="DiamondModel.php",
        contains=("hsing_pay.t_user_diamonds", "where uid = ?"),
    )
    balance_slave = _find_line(
        lines,
        file_suffix="DiamondModel.php",
        contains=("$this->slaveDb->query_one($sql",),
    )
    balance_zero = _find_line(
        lines,
        file_suffix="DiamondModel.php",
        contains=("return ['diamonds' => 0", "'consume' => 0"),
    )
    if (
        balance_call
        and balance_field
        and balance_method
        and balance_sql
        and balance_slave
        and balance_zero
    ):
        facts.append(
            {
                "category": "余额来源",
                "statement": "主播展示的钻石余额来自 `hsing_pay.t_user_diamonds.diamonds`，"
                "按当前 UID 通过 `DiamondModel::getUserDiamonds()` 从从库查询；用户没有钱包"
                "记录时返回 0。",
                "evidence_refs": [
                    balance_call.ref,
                    balance_field.ref,
                    balance_method.ref,
                    balance_sql.ref,
                    balance_slave.ref,
                    balance_zero.ref,
                ],
            }
        )

    region_call = _find_line(
        lines,
        file_suffix="DiamondControl.php",
        contains=("$anchorModel->getAnchorRegion($uid)",),
    )
    region_method = _find_line(
        lines,
        file_suffix="AnchorModel.php",
        contains=("function getAnchorRegion",),
    )
    region_sql = _find_line(
        lines,
        file_suffix="AnchorModel.php",
        contains=("select b.region from data_analyse.t_guild_anchor", "t_guild b on a.gid=b.gid"),
    )
    region_fallback = _find_line(
        lines,
        file_suffix="AnchorModel.php",
        contains=("return 'global'",),
    )
    if region_call and region_method and region_sql and region_fallback:
        facts.append(
            {
                "category": "区域来源",
                "statement": "兑换档位按主播所属公会区域筛选：`AnchorModel::getAnchorRegion()` "
                "关联 `t_guild_anchor` 与状态正常的 `t_guild` 读取 `region`；没有有效公会"
                "记录时回退为 `global`。",
                "evidence_refs": [
                    region_call.ref,
                    region_method.ref,
                    region_sql.ref,
                    region_fallback.ref,
                ],
            }
        )

    gear_call = _find_line(
        lines,
        file_suffix="DiamondModel.php",
        contains=("new RebateDiamondExchangeConfig($region)",),
    )
    type_assignment = _find_line(
        lines,
        file_suffix="RebateDiamondExchangeConfig.php",
        contains=("$this->type = self::TYPE_REBATE_DIAMOND_EXCHANGE_COIN",),
    )
    type_constant = _find_line(
        lines,
        file_suffix="BaseAnchorConfig.php",
        contains=("TYPE_REBATE_DIAMOND_EXCHANGE_COIN = 9",),
    )
    gear_config_sql = _find_line(
        lines,
        file_suffix="BaseAnchorConfig.php",
        contains=("select json_conf from data_analyse.t_anchor_settlement_config",),
    )
    gear_levels = _find_line(
        lines,
        file_suffix="DiamondModel.php",
        contains=("RebateDiamondExchangeConfig::LEVELS",),
    )
    diamond_num_field = _find_line(
        lines,
        file_suffix="DiamondModel.php",
        contains=("'diamond_num' =>", "DIAMOND_NUM"),
    )
    coin_num_field = _find_line(
        lines,
        file_suffix="DiamondModel.php",
        contains=("'coin_num' =>", "COIN_NUM"),
    )
    if (
        gear_call
        and type_assignment
        and type_constant
        and gear_config_sql
        and gear_levels
        and diamond_num_field
        and coin_num_field
    ):
        facts.append(
            {
                "category": "档位配置",
                "statement": "兑换档位来自 `data_analyse.t_anchor_settlement_config` 中"
                " `type = 9`（主播钻石兑金币政策）且匹配主播区域的 `json_conf`；"
                "`levels[].diamondNum/coinNum` 会映射为接口字段 "
                "`diamond_gears[].diamond_num/coin_num`。",
                "evidence_refs": [
                    gear_call.ref,
                    type_assignment.ref,
                    type_constant.ref,
                    gear_config_sql.ref,
                    gear_levels.ref,
                    diamond_num_field.ref,
                    coin_num_field.ref,
                ],
            }
        )

    gear_response = _find_line(
        lines,
        file_suffix="DiamondControl.php",
        contains=("$data['diamond_gears']",),
    )
    if balance_field and gear_response:
        facts.append(
            {
                "category": "响应字段",
                "statement": "主播分支的接口响应写入 `diamonds` 和 `diamond_gears` 两个字段；"
                "源码没有在该主播分支额外写入 `is_show = 1`，因此前端是否展示应依据接口"
                "成功响应及这些字段判断，而不能假设后端返回了显式的正显标记。",
                "evidence_refs": [balance_field.ref, gear_response.ref],
            }
        )

    config_slave = _find_line(
        lines,
        file_suffix="BaseAnchorConfig.php",
        contains=("ServiceFactory::getSlaveDB()",),
    )
    if balance_slave and config_slave:
        facts.append(
            {
                "category": "读取一致性",
                "statement": "钱包余额和档位配置都通过从库查询，而不是在前端写死；主从复制延迟时"
                "可能短时展示旧余额或旧档位，排障时应同时核对主库当前值与从库读到的值。",
                "evidence_refs": [balance_slave.ref, config_slave.ref],
            }
        )

    return facts
