# waky4 前端钻石展示排查证据

## 诊断元数据

| 项目 | 内容 |
|---|---|
| 项目 | `waky4` |
| 问题 | `waky4 排查前端展示钻石是根据什么？` |
| 最终任务 ID | `diag_80bb27c08ce946f5bfa25d5285db32fb` |
| 任务状态 | `COMPLETED` |
| 检索模式 | `curated_domain` |
| AI | 主动关闭；结论来自源码确定性分析 |
| 最终检索词 | 17 个；动态发现噪声词 0 个 |
| 确定性事实 | 8 类，全部包含源码行号 |

## 结论

waky4 前端钻石展示不是由静态前端配置或单一余额字段决定，而是由钱包页两个后端接口共同构成显示契约：

1. `Action/Prop.getMyRebateCard` 返回 `is_anchor`，源码注释明确说明前端据此展示钻石档位和用户钻石。
2. 钱包钻石数据由 `Action/Diamond.getDiamondGearList` 提供；非主播只收到 `is_show = 0`，主播收到 `diamonds` 和 `diamond_gears`。
3. 钻石余额来自 `hsing_pay.t_user_diamonds.diamonds`，按当前登录 UID 从从库查询。
4. 兑换档位按主播所属公会区域，从 `data_analyse.t_anchor_settlement_config` 中读取 `type = 9` 的 JSON 配置。
5. 主播判定条件是 `data_analyse.t_anchor` 中存在同一 UID 且 `status = 0` 的记录。

## 一、前端显隐依据

钱包页先通过 `Prop.getMyRebateCard` 获取 `is_anchor`。

| 证据 | 源码位置 | 证明内容 |
|---|---|---|
| 钱包返利卡入口 | `waky4_miyouphp/php/action/PropControl.php:76` | 定义 `getMyRebateCard($request)` |
| 主播显隐字段 | `waky4_miyouphp/php/action/PropControl.php:88` | 写入 `$rebateCard['is_anchor'] = ...isAnchor($uid)`，注释明确为“前端展示钻石档位和用户钻石” |
| 钱包路由白名单 | `waky4_miyouphp/php/config/auth.php:80` | 登记 `Action/Prop.getMyRebateCard` |

关键源码：

```php
$rebateCard['is_anchor'] = (new \UserModel())->isAnchor($uid);
// 110，是否主播，前端展示钻石档位和用户钻石
```

因此，前端展示钻石区的首要业务判断是当前登录用户是否为主播。仓库未包含客户端渲染源码，客户端实际使用真值判断、非空判断还是字段存在判断仍需结合对应端版本确认。

## 二、钻石接口与主播门槛

| 证据 | 源码位置 | 证明内容 |
|---|---|---|
| 钱包跳转路由 | `waky4_miyouphp/php/config/auth.php:88` | `Action/Diamond.getDiamondGearList`，注释为“110钱包页跳转” |
| 接口方法 | `waky4_miyouphp/php/action/DiamondControl.php:21` | 定义 `getDiamondGearList($request)` |
| 登录校验 | `waky4_miyouphp/php/action/DiamondControl.php:24` | `Request::CheckLogin()` 取得当前 UID |
| 非主播分支 | `waky4_miyouphp/php/action/DiamondControl.php:28-30` | 写入 `is_show = 0` 后立即返回，不读取余额和档位 |
| 主播判定 | `waky4_miyouphp/php/model/usermodel.php:1757-1759` | 查询 `TAnchor`：`uid` 相同且 `status = 0` 的记录存在时才是主播 |

需要注意：主播分支只写入 `diamonds` 和 `diamond_gears`，源码没有显式写入 `is_show = 1`。前端应依据接口成功响应及这些数据字段展示，不能假设后端一定返回正向显隐标记。

## 三、钻石余额来源

| 证据 | 源码位置 | 证明内容 |
|---|---|---|
| 控制器读取余额 | `waky4_miyouphp/php/action/DiamondControl.php:35-36` | 调用 `getUserDiamonds($uid)`，取 `diamonds` 写入响应 |
| 钱包查询方法 | `waky4_miyouphp/php/model/DiamondModel.php:114-121` | 按 UID 查询钱包余额 |
| 数据库来源 | `waky4_miyouphp/php/model/DiamondModel.php:116` | `select uid, diamonds, consume from hsing_pay.t_user_diamonds where uid = ?` |
| 从库读取 | `waky4_miyouphp/php/model/DiamondModel.php:117` | 使用 `$this->slaveDb->query_one($sql, $uid)` |
| 无记录兜底 | `waky4_miyouphp/php/model/DiamondModel.php:118-120` | 没有钱包记录时 `diamonds = 0`、`consume = 0` |

结论：前端看到的钻石数量来自当前主播在 `hsing_pay.t_user_diamonds.diamonds` 中的余额；模型同时返回 `consume`，但钱包接口只把 `diamonds` 写入响应，不展示 `consume`。本次可见链路没有把 Redis 作为余额来源。

## 四、兑换档位的区域来源

| 证据 | 源码位置 | 证明内容 |
|---|---|---|
| 获取区域 | `waky4_miyouphp/php/action/DiamondControl.php:39` | `getAnchorRegion($uid)` |
| 区域查询 | `waky4_miyouphp/php/model/AnchorModel.php:998-1000` | 关联 `data_analyse.t_guild_anchor` 和状态正常的 `data_analyse.t_guild` 读取 `region` |
| 区域兜底 | `waky4_miyouphp/php/model/AnchorModel.php:1002-1005` | 没有有效公会区域时回退为 `global` |

也就是说，同一钻石余额在不同区域可能对应不同的兑换档位；区域不是客户端参数，而是服务端根据主播当前有效公会关系查询得到。

## 五、兑换档位配置来源

| 证据 | 源码位置 | 证明内容 |
|---|---|---|
| 档位方法 | `waky4_miyouphp/php/model/DiamondModel.php:131-145` | 使用主播区域实例化档位配置并映射响应字段 |
| 配置类 | `waky4_miyouphp/php/services/AnchorSettlement/RebateDiamondExchangeConfig.php:8-20` | 钻石兑金币档位配置类 |
| 配置类型 | `waky4_miyouphp/php/services/AnchorSettlement/BaseAnchorConfig.php:22` | `TYPE_REBATE_DIAMOND_EXCHANGE_COIN = 9` |
| 类型绑定 | `waky4_miyouphp/php/services/AnchorSettlement/RebateDiamondExchangeConfig.php:19` | 将配置类型设为 9 |
| 数据库配置 | `waky4_miyouphp/php/services/AnchorSettlement/BaseAnchorConfig.php:72-83` | 按 `type` 和 `region` 查询 `data_analyse.t_anchor_settlement_config.json_conf` |
| 档位字段 | `waky4_miyouphp/php/model/DiamondModel.php:136-141` | `levels` 中的 `diamondNum`、`coinNum` 映射成 `diamond_num`、`coin_num` |

最终接口结构如下：

```text
{
  diamonds: <当前主播钱包余额>,
  diamond_gears: [
    { id: <档位 ID>, diamond_num: <钻石数量>, coin_num: <金币数量> }
  ]
}
```

`config/anchor.php` 中的兑换政策比例不是该钱包页档位列表的直接数据源；钱包页档位列表实际来自 `t_anchor_settlement_config`。

## 六、读取一致性与排障边界

- 余额和档位配置都通过从库查询。
- 主从复制延迟时，页面可能短时展示旧余额或旧档位；排查用户反馈时应同时核对主库当前值和从库读取值。
- 本次证据覆盖 waky4 后端接口和配置来源，不包含客户端渲染源码；客户端具体显隐实现仍需结合对应端版本确认。
- 本次没有连接 waky4 业务数据库执行运行态查询，因此证据能证明数据来源和读取路径，不能证明某位用户此刻的实际余额或档位值。

## 七、旧任务为何“无法排查”

修复前任务：`diag_551d6fbb867c411bb3913d1ebb58f16b`。

旧任务虽然状态为 `COMPLETED`，但存在以下问题：

- 检索词被拆成 `前端展示钻石` 和孤立噪声 `根据`；
- 动态发现错误扩展到 `cpBond`、礼物、矿场、房间等无关代码；
- 没有稳定命中 `PropControl`、`DiamondControl`、`DiamondModel`、主播判定和档位配置完整链路；
- 最终只有 7 条泛化的“代码控制点”，不能回答展示依据；
- AI 调用失败后，没有领域兜底把源码事实组织成业务答案。

根因是 RAgent 缺少“钱包页钻石展示”的领域映射和确定性分析，不是 waky4 没有实现该功能。

## 八、修复内容

- 增加“前端展示钻石”领域检索映射，覆盖钱包显隐、接口、登录、主播判定、余额、区域和档位；
- 使用精确检索词 `where('status',0)->exists` 获取 `UserModel::isAnchor()`，避免被其他同名主播判断挤出结果；
- 已知高置信领域使用受控检索计划，未知领域仍保留通用检索和动态符号发现；
- 增加钱包钻石证据优先级，避免礼物和活动代码挤占窗口；
- 增加确定性分析，输出页面入口、钱包显隐、展示条件、余额、区域、档位、响应字段和读取一致性；
- 有确定性事实时，“已确认事实”只展示事实自身引用的源码，不再把全部候选证据重复列为确认事实；
- 增加领域分词、协议映射、证据排序和完整业务链路回归测试。

## 九、最终验收

最终真实任务：

```text
diag_80bb27c08ce946f5bfa25d5285db32fb
```

验收结果：

- 状态 `COMPLETED`；
- 检索模式 `curated_domain`；
- 17 个受控检索词，动态发现噪声词 0 个；
- 8 类确定性事实：页面入口、钱包页显隐、展示条件、余额来源、区域来源、档位配置、响应字段、读取一致性；
- `PropControl.php`、`DiamondControl.php`、`DiamondModel.php`、`usermodel.php`、`AnchorModel.php`、`BaseAnchorConfig.php`、`RebateDiamondExchangeConfig.php` 均进入证据；
- 报告已确认事实只保留 8 条确定性事实，不再混入无关代码命中；
- 全量测试 `206 passed`；
- 注释覆盖、Python 编译、前端 JavaScript 语法、Compose 配置和 `git diff --check` 均通过。

当前改动尚未提交，等待用户审阅。
