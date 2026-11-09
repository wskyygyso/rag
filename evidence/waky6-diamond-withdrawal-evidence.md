# 诊断证据：diag_1458e0a7d641480b93bbe902a0dca249

> ⚠️ 历史证据，已失效：当前挂载的 waky6 仓库中不存在本文引用的 `WithdrawalOrderCreateService`。本文保留用于审计旧任务，不应作为当前源码事实。当前有效结论请查看 `evidence/retrieval-refactor-validation.md` 和任务 `diag_a32c29ef2157414488b22b4da9e7422b`。

- 项目：waky6
- 问题：钻石提现最小兑换金额是多少？有手续费嘛？
- 环境：development
- 任务状态：COMPLETED
- 创建时间：2026-11-06T06:58:40.591190+00:00
- 结果更新时间：2026-11-06T06:59:16.151567+00:00

> 证据口径：业务限制来自源码确定性分析；数据库、配置和日志验证及 AI 叙写分别标注，未执行或失败的项目不能视为已确认。

## 诊断结论

已从源码确认 waky6 用户钻石提现规则：所有提现金额必须是 10,000 钻石的整数倍；币商最低 10,000 钻石，官方正式环境最低 800,000 钻石（测试环境 80,000）；币商订单手续费字段为 0，官方订单会从到账额扣除渠道手续费；手续费=结算金额×比例费率+固定费；当前 USDT-TRC20 为 1%+2.5 USDT，Epay 为 1%+0.5 USD。

## 置信度

中（0.72）：已由源码证据生成确定性业务事实；运行环境状态仍需按需验证。

## 业务限制与源码依据

### 1. 业务入口

问题对应新版用户钻石提现链路：`Action/Diamond.withdrawOrder` 调用 `WithdrawalOrderCreateService::create()`，不能与旧版主播工资提现混用。

源码引用：`waky6_miyouphp/php/action/DiamondControl.php:59`、`waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:29`

- `waky6_miyouphp/php/action/DiamondControl.php:59`：return (new \Domain\Withdrawal\Service\WithdrawalOrderCreateService())->create($uid, $input, $request);
- `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:29`：class WithdrawalOrderCreateService

### 2. 提现粒度

钻石提现金额必须为 10,000 钻石的整数倍。

源码引用：`waky6_miyouphp/php/config/diamonds.php:81`、`waky6_miyouphp/php/config/diamonds.php:82`

- `waky6_miyouphp/php/config/diamonds.php:81`：'withdrawal_limits' => [
- `waky6_miyouphp/php/config/diamonds.php:82`：'multiple' => 10000,

### 3. 提现下限

币商渠道最低 10,000 钻石；官方渠道测试环境最低 80,000 钻石、正式环境最低 800,000 钻石。`getByTestMode` 的第一个参数用于 `TEST_MODE=1`，第二个参数用于正式环境。

源码引用：`waky6_miyouphp/php/config/diamonds.php:84`、`waky6_miyouphp/php/config/diamonds.php:87`、`waky6_miyouphp/php/conf/SysEnv.php:122`、`waky6_miyouphp/php/conf/SysEnv.php:124`

- `waky6_miyouphp/php/config/diamonds.php:84`：'min_diamonds' => 10000,
- `waky6_miyouphp/php/config/diamonds.php:87`：'min_diamonds' => getByTestMode(80000,800000),
- `waky6_miyouphp/php/conf/SysEnv.php:122`：function getByTestMode($testModeVal, $noTestModeVal)
- `waky6_miyouphp/php/conf/SysEnv.php:124`：return env('TEST_MODE', 0) ? $testModeVal : $noTestModeVal;

### 4. 官方金额与频次

官方渠道单笔最高 4,000,000 钻石，每个用户自然日最多 5 笔；币商渠道没有配置单笔上限。

源码引用：`waky6_miyouphp/php/config/diamonds.php:88`、`waky6_miyouphp/php/config/diamonds.php:89`

- `waky6_miyouphp/php/config/diamonds.php:88`：'max_diamonds' => getByTestMode(4000000,4000000),
- `waky6_miyouphp/php/config/diamonds.php:89`：'daily_limit' => getByTestMode(5,5),

### 5. 美元换算

钻石按 1 美元 = 40,000 钻石换算：币商最低 10,000 钻石约 $0.25；官方正式环境最低 800,000 钻石约 $20.00，测试环境最低 80,000 钻石约 $2.00。

源码引用：`waky6_miyouphp/php/config/diamonds.php:32`、`waky6_miyouphp/php/config/diamonds.php:84`、`waky6_miyouphp/php/config/diamonds.php:87`

- `waky6_miyouphp/php/config/diamonds.php:32`：'dollar_to_diamond' => 40000,
- `waky6_miyouphp/php/config/diamonds.php:84`：'min_diamonds' => 10000,
- `waky6_miyouphp/php/config/diamonds.php:87`：'min_diamonds' => getByTestMode(80000,800000),

### 6. 手续费承担

只有官方订单构建手续费和到账净额快照；币商订单的手续费字段默认为 0.00，订单应付金额仍等于钻石折算的美元收入。该结论不等于保证币商线下不会另行收费。

源码引用：`waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:117`、`waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:119`、`waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:120`

- `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:117`：? self::buildOfficialSnapshot($channel['config'], $income)
- `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:119`：$fee = $official['fee'] ?? '0.00';
- `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:120`：$payoutMoney = $official['net'] ?? $income;

### 7. 手续费公式

官方渠道手续费按“结算币种金额 × 比例费率 + 固定手续费”计算，再从结算金额中扣除后得到实际到账净额。若扣除后的净额不大于 0，该渠道会拒绝下单。

源码引用：`waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:423`、`waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:427`、`waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:430`

- `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:423`：$fee = round($grossCurrency * $feeRate + $feeFixed, 2);
- `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:427`：$net = round($grossCurrency - $fee, 2);
- `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:430`：if ($net <= 0) {

### 8. 渠道费率

当前源码配置的官方渠道费率示例：USDT-TRC20 为 1% + 固定 2.5；Epay 为 1% + 固定 0.5。具体币种和最终到账金额以用户所选渠道及订单快照为准。

源码引用：`waky6_miyouphp/php/config/anchorPayment.php:1402`、`waky6_miyouphp/php/config/anchorPayment.php:1419`、`waky6_miyouphp/php/config/anchorPayment.php:1420`、`waky6_miyouphp/php/config/anchorPayment.php:1430`、`waky6_miyouphp/php/config/anchorPayment.php:1448`、`waky6_miyouphp/php/config/anchorPayment.php:1449`

- `waky6_miyouphp/php/config/anchorPayment.php:1402`：'name' => 'USDT-TRC20',
- `waky6_miyouphp/php/config/anchorPayment.php:1419`：'pay_fee' => 0.01,
- `waky6_miyouphp/php/config/anchorPayment.php:1420`：'additional_pay_fee' => 2.5,
- `waky6_miyouphp/php/config/anchorPayment.php:1430`：'name' => 'Epay', //渠道名
- `waky6_miyouphp/php/config/anchorPayment.php:1448`：'pay_fee' => 0.01,          // 1%
- `waky6_miyouphp/php/config/anchorPayment.php:1449`：'additional_pay_fee' => 0.5,// +1

## 源码证据上下文

### `waky6_miyouphp/php/config/diamonds.php:13`

- 命中检索词：min_diamonds
- 证据行：'min_diamonds' => 10000,

```text
1: <?php
2:
3: /**
4:  * 钻石兑金币（向币商）配置
5:  * 需求：2026-09-21-waky6-withdrawal-redesign（骨架 §5.7.0/§7.1）
6:  * 说明：换算比例按既定口径写死于代码配置文件，不落 DB、不走 t_pay_proxy_config 的 DB 覆盖链路
7:  */
8: return [
9:     'diamond_exchange' => [
10:         // 钻石兑金币比例：2 钻 = 1 金币（R-DG-03；服务端只读）
11:         'diamond_to_coin' => getByTestMode(2, 2),
12:         // 兑换输入下限（R-DG-06）
13:         'min_diamonds' => 10000,
14:         // 兑换输入整数倍（R-DG-06）
15:         'multiple' => 10000,
16:         // 单次兑换上限：0=不限（Q-003 待产品确认，预留配置）
17:         'max_diamonds' => 0,
18:         // 快速档位（展示用；对应金币数 = 档位 / 2）
19:         'gears' => [10000, 50000, 100000, 200000, 500000],
20:     ],
21:
22:     /**
23:      * 运营后台「主播管理-返利配置」页只读展示的三个比例（原型 R-OP-10）
24:      * 均为服务端写死、后台不可修改；后两项语义属提现侧，与提现改造共用同一份定义，勿在别处重复声明
25:      */
```

### `waky6_miyouphp/php/config/diamonds.php:31`

- 命中检索词：钻石提现
- 证据行：// 钻石提现：1$ = 40000 钻石

```text
19:         'gears' => [10000, 50000, 100000, 200000, 500000],
20:     ],
21:
22:     /**
23:      * 运营后台「主播管理-返利配置」页只读展示的三个比例（原型 R-OP-10）
24:      * 均为服务端写死、后台不可修改；后两项语义属提现侧，与提现改造共用同一份定义，勿在别处重复声明
25:      */
26:     'readonly_ratios' => [
27:         // 钻石兑金币：2 钻石 = 1 金币（与上面 diamond_to_coin 同源，展示用）
28:         'diamond_to_coin' => 2,
29:         // 美元兑代理币：1$ = 20000 代理币
30:         'dollar_to_proxy_coin' => 20000,
31:         // 钻石提现：1$ = 40000 钻石
32:         'dollar_to_diamond' => 40000,
33:     ],
34:
35:     /**
36:      * 新版提现的区内灰度名单（骨架 §5.7.0 第二层开关；2026-11-02）
37:      *
38:      * 判定 = 区 rebate_mode==2 **或** uid 命中本名单：区一旦翻成 2 就全区放行、不再查名单，
39:      * 所以正式放量的收尾动作是翻区开关，不是清名单（翻完这两组键自动失效）。
40:      * 读取方只有 Domain\Withdrawal\Service\WithdrawalModeResolver，别处不要再自己 in_array 这两个键。
41:      *
42:      * ⚠️ 生产生效的是 getByTestMode 的**第二段**（正式环境 TEST_MODE=0）：灰度 uid 填在第二段，
43:      *    填在第一段只在本地/测试环境生效，生产会静默不生效。
```

### `waky6_miyouphp/php/config/diamonds.php:32`

- 命中检索词：dollar_to_diamond
- 证据行：'dollar_to_diamond' => 40000,

```text
20:     ],
21:
22:     /**
23:      * 运营后台「主播管理-返利配置」页只读展示的三个比例（原型 R-OP-10）
24:      * 均为服务端写死、后台不可修改；后两项语义属提现侧，与提现改造共用同一份定义，勿在别处重复声明
25:      */
26:     'readonly_ratios' => [
27:         // 钻石兑金币：2 钻石 = 1 金币（与上面 diamond_to_coin 同源，展示用）
28:         'diamond_to_coin' => 2,
29:         // 美元兑代理币：1$ = 20000 代理币
30:         'dollar_to_proxy_coin' => 20000,
31:         // 钻石提现：1$ = 40000 钻石
32:         'dollar_to_diamond' => 40000,
33:     ],
34:
35:     /**
36:      * 新版提现的区内灰度名单（骨架 §5.7.0 第二层开关；2026-11-02）
37:      *
38:      * 判定 = 区 rebate_mode==2 **或** uid 命中本名单：区一旦翻成 2 就全区放行、不再查名单，
39:      * 所以正式放量的收尾动作是翻区开关，不是清名单（翻完这两组键自动失效）。
40:      * 读取方只有 Domain\Withdrawal\Service\WithdrawalModeResolver，别处不要再自己 in_array 这两个键。
41:      *
42:      * ⚠️ 生产生效的是 getByTestMode 的**第二段**（正式环境 TEST_MODE=0）：灰度 uid 填在第二段，
43:      *    填在第一段只在本地/测试环境生效，生产会静默不生效。
44:      *
```

### `waky6_miyouphp/php/config/diamonds.php:75`

- 命中检索词：钻石提现
- 证据行：* 用户钻石提现限制。

```text
63:         // 只放用户不放币商 = 无效灰度：单子派不出去（候选池为空），一路超时自动退款。
64:         'agent_uids' => getByTestMode(
65:             [
66:                 // TEST_MODE=1：本地 / 测试环境生效
67:             ],
68:             [
69:                 // 生产生效（TEST_MODE=0）：正式灰度 uid 填这里
70:             ]
71:         ),
72:     ],
73:
74:     /**
75:      * 用户钻石提现限制。
76:      *
77:      * 所有提现单统一为 10000 的整数倍（2026-10-30 产品口径）；
78:      * 非官方单（币商渠道）最低 10000；官方单额外限制单笔金额和单个用户自然日下单次数。
79:      * max_diamonds=0、daily_limit=0 均表示不限。自然日按用户手机时区计算。
80:      */
81:     'withdrawal_limits' => [
82:         'multiple' => 10000,
83:         'dealer' => [
84:             'min_diamonds' => 10000,
85:         ],
86:         'official' => [
87:             'min_diamonds' => getByTestMode(80000,800000),
```

### `waky6_miyouphp/php/config/diamonds.php:81`

- 命中检索词：withdrawal_limits
- 证据行：'withdrawal_limits' => [

```text
69:                 // 生产生效（TEST_MODE=0）：正式灰度 uid 填这里
70:             ]
71:         ),
72:     ],
73:
74:     /**
75:      * 用户钻石提现限制。
76:      *
77:      * 所有提现单统一为 10000 的整数倍（2026-10-30 产品口径）；
78:      * 非官方单（币商渠道）最低 10000；官方单额外限制单笔金额和单个用户自然日下单次数。
79:      * max_diamonds=0、daily_limit=0 均表示不限。自然日按用户手机时区计算。
80:      */
81:     'withdrawal_limits' => [
82:         'multiple' => 10000,
83:         'dealer' => [
84:             'min_diamonds' => 10000,
85:         ],
86:         'official' => [
87:             'min_diamonds' => getByTestMode(80000,800000),
88:             'max_diamonds' => getByTestMode(4000000,4000000),
89:             'daily_limit' => getByTestMode(5,5),
90:         ],
91:     ],
92:
93:     /**
```

### `waky6_miyouphp/php/config/diamonds.php:84`

- 命中检索词：min_diamonds
- 证据行：'min_diamonds' => 10000,

```text
72:     ],
73:
74:     /**
75:      * 用户钻石提现限制。
76:      *
77:      * 所有提现单统一为 10000 的整数倍（2026-10-30 产品口径）；
78:      * 非官方单（币商渠道）最低 10000；官方单额外限制单笔金额和单个用户自然日下单次数。
79:      * max_diamonds=0、daily_limit=0 均表示不限。自然日按用户手机时区计算。
80:      */
81:     'withdrawal_limits' => [
82:         'multiple' => 10000,
83:         'dealer' => [
84:             'min_diamonds' => 10000,
85:         ],
86:         'official' => [
87:             'min_diamonds' => getByTestMode(80000,800000),
88:             'max_diamonds' => getByTestMode(4000000,4000000),
89:             'daily_limit' => getByTestMode(5,5),
90:         ],
91:     ],
92:
93:     /**
94:      * 提现订单通知的端上跳转地址（骨架 §13.3.3 步骤2；键名与写法沿用 waky7 diamonds.php:96,99）
95:      * ⚠️ 两个 H5 页面（withdrawalOrderNotification / coinsOrderNotification）在 w6 侧是否存在未核实，
96:      *    需前端确认；页面未上线前消息可送达但点击无内容
```

### `waky6_miyouphp/php/config/diamonds.php:87`

- 命中检索词：min_diamonds
- 证据行：'min_diamonds' => getByTestMode(80000,800000),

```text
75:      * 用户钻石提现限制。
76:      *
77:      * 所有提现单统一为 10000 的整数倍（2026-10-30 产品口径）；
78:      * 非官方单（币商渠道）最低 10000；官方单额外限制单笔金额和单个用户自然日下单次数。
79:      * max_diamonds=0、daily_limit=0 均表示不限。自然日按用户手机时区计算。
80:      */
81:     'withdrawal_limits' => [
82:         'multiple' => 10000,
83:         'dealer' => [
84:             'min_diamonds' => 10000,
85:         ],
86:         'official' => [
87:             'min_diamonds' => getByTestMode(80000,800000),
88:             'max_diamonds' => getByTestMode(4000000,4000000),
89:             'daily_limit' => getByTestMode(5,5),
90:         ],
91:     ],
92:
93:     /**
94:      * 提现订单通知的端上跳转地址（骨架 §13.3.3 步骤2；键名与写法沿用 waky7 diamonds.php:96,99）
95:      * ⚠️ 两个 H5 页面（withdrawalOrderNotification / coinsOrderNotification）在 w6 侧是否存在未核实，
96:      *    需前端确认；页面未上线前消息可送达但点击无内容
97:      */
98:     // im跳转的url（发给币商，3040 离线提醒用）
99:     'im_jump_url_coins_merchant' => \Conf\Config::get('urlConf.jump_url_coins_merchant'),
```

### `waky6_miyouphp/php/conf/SysEnv.php:122`

- 命中检索词：function getByTestMode
- 证据行：function getByTestMode($testModeVal, $noTestModeVal)

```text
110:         return (bool)env('TEST_MODE', 0);
111:     }
112: }
113:
114: if (!function_exists('env')) {
115:     function env(string $key, $default = '')
116:     {
117:         return SysEnv::get($key, $default);
118:     }
119: }
120:
121: if (!function_exists('getByTestMode')) {
122:     function getByTestMode($testModeVal, $noTestModeVal)
123:     {
124:         return env('TEST_MODE', 0) ? $testModeVal : $noTestModeVal;
125:     }
126: }
127:
128: if (!function_exists('checkEnv')) {
129:     function checkEnv(string $key, $checkVal)
130:     {
131:         return $checkVal == SysEnv::get($key, '');
132:     }
133: }
134:
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:22`

- 命中检索词：DiamondControl
- 证据行：* 官方渠道对齐 waky7 `DiamondControl::diamondWithdraw()` → `AdvanceServices::addAnchorUSD()`：

```text
10: use Model\DiamondsWithdrawalOrderModel;
11: use Model\Orm\HsingPayOrm\TUserDiamonds;
12: use Model\WithdrawalAssetModel;
13: use ServiceFactory;
14: use Services\Monitor\Fuse\DiamondFuseService;
15: use Services\Monitor\Fuse\UsdFuseService;
16: use Timezone\TimeUser;
17:
18: /**
19:  * 钻石提现下单（官方 / 币商双渠道）——批 A + 官方 API 写侧
20:  * 需求：2026-09-21-waky6-withdrawal-redesign（骨架 §9.2 入出参、§10.2 流程、§5.1 状态口径）
21:  *
22:  * 官方渠道对齐 waky7 `DiamondControl::diamondWithdraw()` → `AdvanceServices::addAnchorUSD()`：
23:  * 冻结钻石、建 status=10 且 `is_official=1` 的订单、同步创建 `t_api_payments` 待打款记录；
24:  * 币商渠道建 status=0 待派单订单，由派单调度继续推进。
25:  *
26:  * R-WD-02 的资金安全顺序固定为「先冻结 → 再建单」：两步与官方打款记录必须在同一主库事务内，
27:  * 任一步失败整体回滚，绝不出现已建单但未冻结钻石的中间态。
28:  */
29: class WithdrawalOrderCreateService
30: {
31:     const ORDER_TYPE_OFFICIAL = 'official';
32:     const ORDER_TYPE_DEALER = 'dealer';
33:
34:     const WITHDRAW_MODE_DIRECT = 'direct';
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:29`

- 命中检索词：WithdrawalOrderCreateService
- 证据行：class WithdrawalOrderCreateService

```text
17:
18: /**
19:  * 钻石提现下单（官方 / 币商双渠道）——批 A + 官方 API 写侧
20:  * 需求：2026-09-21-waky6-withdrawal-redesign（骨架 §9.2 入出参、§10.2 流程、§5.1 状态口径）
21:  *
22:  * 官方渠道对齐 waky7 `DiamondControl::diamondWithdraw()` → `AdvanceServices::addAnchorUSD()`：
23:  * 冻结钻石、建 status=10 且 `is_official=1` 的订单、同步创建 `t_api_payments` 待打款记录；
24:  * 币商渠道建 status=0 待派单订单，由派单调度继续推进。
25:  *
26:  * R-WD-02 的资金安全顺序固定为「先冻结 → 再建单」：两步与官方打款记录必须在同一主库事务内，
27:  * 任一步失败整体回滚，绝不出现已建单但未冻结钻石的中间态。
28:  */
29: class WithdrawalOrderCreateService
30: {
31:     const ORDER_TYPE_OFFICIAL = 'official';
32:     const ORDER_TYPE_DEALER = 'dealer';
33:
34:     const WITHDRAW_MODE_DIRECT = 'direct';
35:     const WITHDRAW_MODE_APPOINT = 'appoint';
36:
37:     /** 下单冷却 key 前缀（同一用户 5 秒一次，契约 91020008） */
38:     const COOLDOWN_KEY_PREFIX = 'withdrawal_order_create:';
39:
40:     /** 冷却窗口（秒），与契约「同一用户 5s 内第二次提交」一致 */
41:     const COOLDOWN_SECONDS = 5;
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:117`

- 命中检索词：buildOfficialSnapshot
- 证据行：? self::buildOfficialSnapshot($channel['config'], $income)

```text
105:             $payeeFields
106:         );
107:
108:         // 5. 可提取余额：主库读 diamonds − pending_diamonds。这只是给用户的友好预检，
109:         //    并发下的真闸门是第 9 步冻结语句的 affected_rows（见 freezeAndCreate）
110:         $this->assertDiamondBalance($uid, $diamonds);
111:
112:         // 6. 美元换算：1$ = 40000 钻石，两位数口径
113:         $income = self::calcIncome($diamonds);
114:
115:         // 7. 官方渠道在下单时冻结费率和三方渠道快照，后续配置调整不影响历史单
116:         $official = $orderType === self::ORDER_TYPE_OFFICIAL
117:             ? self::buildOfficialSnapshot($channel['config'], $income)
118:             : null;
119:         $fee = $official['fee'] ?? '0.00';
120:         $payoutMoney = $official['net'] ?? $income;
121:         // 币种取渠道目录的 currency（Telebirr→ETB）；currency_type 已是国家/分组键，不能当币种用
122:         $payoutCurrency = $official['currency'] ?? strval($channel['config']['currency'] ?? '');
123:
124:         // 8. 官方单自然日次数限制先于频率锁；达到上限时不占用 5 秒频控窗口。
125:         $this->assertOfficialDailyLimit($uid, $orderType);
126:         // 同一用户 5s 频率闸门（契约 91020008）：窗口内重复提交（含并发）直接拒，防连点出两张单
127:         $this->assertNotFrequent($uid);
128:
129:         $payload = [
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:269`

- 命中检索词：withdrawal_limits
- 证据行：$multiple = max(0, intval(Config::get('diamonds.withdrawal_limits.multiple', 0)));

```text
257:      * 钻石数基础合法性（R-WD-14「未填钻石数」分支）
258:      *
259:      * @param int $diamonds
260:      * @throws WithdrawalException
261:      */
262:     public static function assertDiamonds(int $diamonds, string $orderType = ''): void
263:     {
264:         if ($diamonds <= 0) {
265:             throw new WithdrawalException(WithdrawalException::INVALID_PARAMS);
266:         }
267:
268:         // 所有提现单统一 10000 的整数倍（2026-10-30 产品口径）
269:         $multiple = max(0, intval(Config::get('diamonds.withdrawal_limits.multiple', 0)));
270:         if ($multiple > 0 && $diamonds % $multiple !== 0) {
271:             throw new WithdrawalException(WithdrawalException::AMOUNT_NOT_ALLOWED, [], [
272:                 'diamonds' => $diamonds,
273:                 'multiple' => $multiple,
274:                 'detail' => 'withdrawal diamonds must be a configured multiple',
275:             ]);
276:         }
277:
278:         if ($orderType !== self::ORDER_TYPE_OFFICIAL) {
279:             // 非官方单（币商渠道）：最低 10000，无单笔上限（2026-10-30 产品口径）
280:             $min = max(0, intval(Config::get('diamonds.withdrawal_limits.dealer.min_diamonds', 0)));
281:             if ($min > 0 && $diamonds < $min) {
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:280`

- 命中检索词：withdrawal_limits
- 证据行：$min = max(0, intval(Config::get('diamonds.withdrawal_limits.dealer.min_diamonds', 0)));

```text
268:         // 所有提现单统一 10000 的整数倍（2026-10-30 产品口径）
269:         $multiple = max(0, intval(Config::get('diamonds.withdrawal_limits.multiple', 0)));
270:         if ($multiple > 0 && $diamonds % $multiple !== 0) {
271:             throw new WithdrawalException(WithdrawalException::AMOUNT_NOT_ALLOWED, [], [
272:                 'diamonds' => $diamonds,
273:                 'multiple' => $multiple,
274:                 'detail' => 'withdrawal diamonds must be a configured multiple',
275:             ]);
276:         }
277:
278:         if ($orderType !== self::ORDER_TYPE_OFFICIAL) {
279:             // 非官方单（币商渠道）：最低 10000，无单笔上限（2026-10-30 产品口径）
280:             $min = max(0, intval(Config::get('diamonds.withdrawal_limits.dealer.min_diamonds', 0)));
281:             if ($min > 0 && $diamonds < $min) {
282:                 throw new WithdrawalException(WithdrawalException::AMOUNT_NOT_ALLOWED, [], [
283:                     'diamonds' => $diamonds,
284:                     'min_diamonds' => $min,
285:                     'detail' => 'dealer withdrawal diamonds below configured minimum',
286:                 ]);
287:             }
288:
289:             return;
290:         }
291:
292:         $min = max(0, intval(Config::get('diamonds.withdrawal_limits.official.min_diamonds', 0)));
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:284`

- 命中检索词：min_diamonds
- 证据行：'min_diamonds' => $min,

```text
272:                 'diamonds' => $diamonds,
273:                 'multiple' => $multiple,
274:                 'detail' => 'withdrawal diamonds must be a configured multiple',
275:             ]);
276:         }
277:
278:         if ($orderType !== self::ORDER_TYPE_OFFICIAL) {
279:             // 非官方单（币商渠道）：最低 10000，无单笔上限（2026-10-30 产品口径）
280:             $min = max(0, intval(Config::get('diamonds.withdrawal_limits.dealer.min_diamonds', 0)));
281:             if ($min > 0 && $diamonds < $min) {
282:                 throw new WithdrawalException(WithdrawalException::AMOUNT_NOT_ALLOWED, [], [
283:                     'diamonds' => $diamonds,
284:                     'min_diamonds' => $min,
285:                     'detail' => 'dealer withdrawal diamonds below configured minimum',
286:                 ]);
287:             }
288:
289:             return;
290:         }
291:
292:         $min = max(0, intval(Config::get('diamonds.withdrawal_limits.official.min_diamonds', 0)));
293:         $max = max(0, intval(Config::get('diamonds.withdrawal_limits.official.max_diamonds', 0)));
294:         if (($min > 0 && $diamonds < $min) || ($max > 0 && $diamonds > $max)) {
295:             throw new WithdrawalException(WithdrawalException::AMOUNT_NOT_ALLOWED, [], [
296:                 'diamonds' => $diamonds,
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:292`

- 命中检索词：withdrawal_limits
- 证据行：$min = max(0, intval(Config::get('diamonds.withdrawal_limits.official.min_diamonds', 0)));

```text
280:             $min = max(0, intval(Config::get('diamonds.withdrawal_limits.dealer.min_diamonds', 0)));
281:             if ($min > 0 && $diamonds < $min) {
282:                 throw new WithdrawalException(WithdrawalException::AMOUNT_NOT_ALLOWED, [], [
283:                     'diamonds' => $diamonds,
284:                     'min_diamonds' => $min,
285:                     'detail' => 'dealer withdrawal diamonds below configured minimum',
286:                 ]);
287:             }
288:
289:             return;
290:         }
291:
292:         $min = max(0, intval(Config::get('diamonds.withdrawal_limits.official.min_diamonds', 0)));
293:         $max = max(0, intval(Config::get('diamonds.withdrawal_limits.official.max_diamonds', 0)));
294:         if (($min > 0 && $diamonds < $min) || ($max > 0 && $diamonds > $max)) {
295:             throw new WithdrawalException(WithdrawalException::AMOUNT_NOT_ALLOWED, [], [
296:                 'diamonds' => $diamonds,
297:                 'min_diamonds' => $min,
298:                 'max_diamonds' => $max,
299:                 'detail' => 'official withdrawal diamonds outside configured range',
300:             ]);
301:         }
302:     }
303:
304:     /**
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:293`

- 命中检索词：withdrawal_limits
- 证据行：$max = max(0, intval(Config::get('diamonds.withdrawal_limits.official.max_diamonds', 0)));

```text
281:             if ($min > 0 && $diamonds < $min) {
282:                 throw new WithdrawalException(WithdrawalException::AMOUNT_NOT_ALLOWED, [], [
283:                     'diamonds' => $diamonds,
284:                     'min_diamonds' => $min,
285:                     'detail' => 'dealer withdrawal diamonds below configured minimum',
286:                 ]);
287:             }
288:
289:             return;
290:         }
291:
292:         $min = max(0, intval(Config::get('diamonds.withdrawal_limits.official.min_diamonds', 0)));
293:         $max = max(0, intval(Config::get('diamonds.withdrawal_limits.official.max_diamonds', 0)));
294:         if (($min > 0 && $diamonds < $min) || ($max > 0 && $diamonds > $max)) {
295:             throw new WithdrawalException(WithdrawalException::AMOUNT_NOT_ALLOWED, [], [
296:                 'diamonds' => $diamonds,
297:                 'min_diamonds' => $min,
298:                 'max_diamonds' => $max,
299:                 'detail' => 'official withdrawal diamonds outside configured range',
300:             ]);
301:         }
302:     }
303:
304:     /**
305:      * 校验自然日提现次数上限；limit<=0 表示不限。
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:297`

- 命中检索词：min_diamonds
- 证据行：'min_diamonds' => $min,

```text
285:                     'detail' => 'dealer withdrawal diamonds below configured minimum',
286:                 ]);
287:             }
288:
289:             return;
290:         }
291:
292:         $min = max(0, intval(Config::get('diamonds.withdrawal_limits.official.min_diamonds', 0)));
293:         $max = max(0, intval(Config::get('diamonds.withdrawal_limits.official.max_diamonds', 0)));
294:         if (($min > 0 && $diamonds < $min) || ($max > 0 && $diamonds > $max)) {
295:             throw new WithdrawalException(WithdrawalException::AMOUNT_NOT_ALLOWED, [], [
296:                 'diamonds' => $diamonds,
297:                 'min_diamonds' => $min,
298:                 'max_diamonds' => $max,
299:                 'detail' => 'official withdrawal diamonds outside configured range',
300:             ]);
301:         }
302:     }
303:
304:     /**
305:      * 校验自然日提现次数上限；limit<=0 表示不限。
306:      *
307:      * @throws WithdrawalException
308:      */
309:     public static function assertDailyLimitNotReached(int $existingCount, int $limit): void
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:336`

- 命中检索词：withdrawal_limits
- 证据行：$limit = max(0, intval(Config::get('diamonds.withdrawal_limits.official.daily_limit', 0)));

```text
324:      * 主库计数用于规避从库延迟；并发提交再由 5 秒频率锁兜底。
325:      *
326:      * @param int $uid
327:      * @param string $orderType
328:      * @throws WithdrawalException
329:      */
330:     private function assertOfficialDailyLimit(int $uid, string $orderType): void
331:     {
332:         if ($orderType !== self::ORDER_TYPE_OFFICIAL) {
333:             return;
334:         }
335:
336:         $limit = max(0, intval(Config::get('diamonds.withdrawal_limits.official.daily_limit', 0)));
337:         if ($limit <= 0) {
338:             return;
339:         }
340:
341:         $now = TimeUser::makeFromCache($uid)->now();
342:         $stime = $now->toTodayStart()->getTimestamp();
343:         $etime = $now->toTodayEnd()->getTimestamp();
344:         $existingCount = (new DiamondsWithdrawalOrderModel())->countUserWithdrawalOrders(
345:             $uid,
346:             1,
347:             $stime,
348:             $etime
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:375`

- 命中检索词：dollar_to_diamond
- 证据行：$ratio = intval(Config::get('diamonds.readonly_ratios.dollar_to_diamond'));

```text
363:             throw new WithdrawalException(WithdrawalException::INVALID_PARAMS);
364:         }
365:     }
366:
367:     /**
368:      * 钻石换算美元（R-WD-05：1$ = 40000 钻石）
369:      *
370:      * @param int $diamonds
371:      * @return string 两位小数，与美元账户落库精度同一口径
372:      */
373:     public static function calcIncome(int $diamonds): string
374:     {
375:         $ratio = intval(Config::get('diamonds.readonly_ratios.dollar_to_diamond'));
376:         if ($ratio <= 0) {
377:             throw new \RuntimeException('diamonds.readonly_ratios.dollar_to_diamond not configured');
378:         }
379:
380:         return sprintf('%.2f', $diamonds / $ratio);
381:     }
382:
383:     /**
384:      * 官方提现到账金额与手续费
385:      *
386:      * 费率、固定手续费、币种、平台全部取自渠道目录 anchorPayment 的 OFFICIAL 组，
387:      * 与 waky7 addAnchorUSD() 同口径（w7 还会用汇率表 exchange3 覆盖 pay_rate，
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:377`

- 命中检索词：dollar_to_diamond
- 证据行：throw new \RuntimeException('diamonds.readonly_ratios.dollar_to_diamond not configured');

```text
365:     }
366:
367:     /**
368:      * 钻石换算美元（R-WD-05：1$ = 40000 钻石）
369:      *
370:      * @param int $diamonds
371:      * @return string 两位小数，与美元账户落库精度同一口径
372:      */
373:     public static function calcIncome(int $diamonds): string
374:     {
375:         $ratio = intval(Config::get('diamonds.readonly_ratios.dollar_to_diamond'));
376:         if ($ratio <= 0) {
377:             throw new \RuntimeException('diamonds.readonly_ratios.dollar_to_diamond not configured');
378:         }
379:
380:         return sprintf('%.2f', $diamonds / $ratio);
381:     }
382:
383:     /**
384:      * 官方提现到账金额与手续费
385:      *
386:      * 费率、固定手续费、币种、平台全部取自渠道目录 anchorPayment 的 OFFICIAL 组，
387:      * 与 waky7 addAnchorUSD() 同口径（w7 还会用汇率表 exchange3 覆盖 pay_rate，
388:      * w6 的 t_pay_currency_config 没有这一列，所以不覆盖，见 §17）。
389:      *
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:384`

- 命中检索词：手续费
- 证据行：* 官方提现到账金额与手续费

```text
372:      */
373:     public static function calcIncome(int $diamonds): string
374:     {
375:         $ratio = intval(Config::get('diamonds.readonly_ratios.dollar_to_diamond'));
376:         if ($ratio <= 0) {
377:             throw new \RuntimeException('diamonds.readonly_ratios.dollar_to_diamond not configured');
378:         }
379:
380:         return sprintf('%.2f', $diamonds / $ratio);
381:     }
382:
383:     /**
384:      * 官方提现到账金额与手续费
385:      *
386:      * 费率、固定手续费、币种、平台全部取自渠道目录 anchorPayment 的 OFFICIAL 组，
387:      * 与 waky7 addAnchorUSD() 同口径（w7 还会用汇率表 exchange3 覆盖 pay_rate，
388:      * w6 的 t_pay_currency_config 没有这一列，所以不覆盖，见 §17）。
389:      *
390:      * @param array $channel 已定位到的官方渠道配置
391:      *                       pay_rate：美元换算成三方结算币种的倍率
392:      *                       pay_fee：按结算币种金额收取的比例手续费
393:      *                       additional_pay_fee：结算币种下的固定手续费
394:      *                       currency：三方结算币种，如 USDT
395:      *                       name：渠道目录中的渠道名，如 USDT-TRC20
396:      *                       platform：三方平台名，如 Obolum
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:386`

- 命中检索词：手续费
- 证据行：* 费率、固定手续费、币种、平台全部取自渠道目录 anchorPayment 的 OFFICIAL 组，

```text
374:     {
375:         $ratio = intval(Config::get('diamonds.readonly_ratios.dollar_to_diamond'));
376:         if ($ratio <= 0) {
377:             throw new \RuntimeException('diamonds.readonly_ratios.dollar_to_diamond not configured');
378:         }
379:
380:         return sprintf('%.2f', $diamonds / $ratio);
381:     }
382:
383:     /**
384:      * 官方提现到账金额与手续费
385:      *
386:      * 费率、固定手续费、币种、平台全部取自渠道目录 anchorPayment 的 OFFICIAL 组，
387:      * 与 waky7 addAnchorUSD() 同口径（w7 还会用汇率表 exchange3 覆盖 pay_rate，
388:      * w6 的 t_pay_currency_config 没有这一列，所以不覆盖，见 §17）。
389:      *
390:      * @param array $channel 已定位到的官方渠道配置
391:      *                       pay_rate：美元换算成三方结算币种的倍率
392:      *                       pay_fee：按结算币种金额收取的比例手续费
393:      *                       additional_pay_fee：结算币种下的固定手续费
394:      *                       currency：三方结算币种，如 USDT
395:      *                       name：渠道目录中的渠道名，如 USDT-TRC20
396:      *                       platform：三方平台名，如 Obolum
397:      * @param string $grossUsd 钻石折算的美元金额
398:      * @return array{fee:string,net:string,currency:string,platform:string,channel:string,method:string}
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:392`

- 命中检索词：手续费
- 证据行：*                       pay_fee：按结算币种金额收取的比例手续费

```text
380:         return sprintf('%.2f', $diamonds / $ratio);
381:     }
382:
383:     /**
384:      * 官方提现到账金额与手续费
385:      *
386:      * 费率、固定手续费、币种、平台全部取自渠道目录 anchorPayment 的 OFFICIAL 组，
387:      * 与 waky7 addAnchorUSD() 同口径（w7 还会用汇率表 exchange3 覆盖 pay_rate，
388:      * w6 的 t_pay_currency_config 没有这一列，所以不覆盖，见 §17）。
389:      *
390:      * @param array $channel 已定位到的官方渠道配置
391:      *                       pay_rate：美元换算成三方结算币种的倍率
392:      *                       pay_fee：按结算币种金额收取的比例手续费
393:      *                       additional_pay_fee：结算币种下的固定手续费
394:      *                       currency：三方结算币种，如 USDT
395:      *                       name：渠道目录中的渠道名，如 USDT-TRC20
396:      *                       platform：三方平台名，如 Obolum
397:      * @param string $grossUsd 钻石折算的美元金额
398:      * @return array{fee:string,net:string,currency:string,platform:string,channel:string,method:string}
399:      * @throws WithdrawalException
400:      */
401:     public static function calcOfficialSettlement(array $channel, string $grossUsd): array
402:     {
403:         // 1. 读取渠道计费参数：汇率、比例手续费、固定手续费、结算币种和三方渠道名。
404:         $rate = (float)($channel['pay_rate'] ?? 0);
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:393`

- 命中检索词：手续费
- 证据行：*                       additional_pay_fee：结算币种下的固定手续费

```text
381:     }
382:
383:     /**
384:      * 官方提现到账金额与手续费
385:      *
386:      * 费率、固定手续费、币种、平台全部取自渠道目录 anchorPayment 的 OFFICIAL 组，
387:      * 与 waky7 addAnchorUSD() 同口径（w7 还会用汇率表 exchange3 覆盖 pay_rate，
388:      * w6 的 t_pay_currency_config 没有这一列，所以不覆盖，见 §17）。
389:      *
390:      * @param array $channel 已定位到的官方渠道配置
391:      *                       pay_rate：美元换算成三方结算币种的倍率
392:      *                       pay_fee：按结算币种金额收取的比例手续费
393:      *                       additional_pay_fee：结算币种下的固定手续费
394:      *                       currency：三方结算币种，如 USDT
395:      *                       name：渠道目录中的渠道名，如 USDT-TRC20
396:      *                       platform：三方平台名，如 Obolum
397:      * @param string $grossUsd 钻石折算的美元金额
398:      * @return array{fee:string,net:string,currency:string,platform:string,channel:string,method:string}
399:      * @throws WithdrawalException
400:      */
401:     public static function calcOfficialSettlement(array $channel, string $grossUsd): array
402:     {
403:         // 1. 读取渠道计费参数：汇率、比例手续费、固定手续费、结算币种和三方渠道名。
404:         $rate = (float)($channel['pay_rate'] ?? 0);
405:         $feeRate = (float)($channel['pay_fee'] ?? 0);
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:401`

- 命中检索词：calcOfficialSettlement
- 证据行：public static function calcOfficialSettlement(array $channel, string $grossUsd): array

```text
389:      *
390:      * @param array $channel 已定位到的官方渠道配置
391:      *                       pay_rate：美元换算成三方结算币种的倍率
392:      *                       pay_fee：按结算币种金额收取的比例手续费
393:      *                       additional_pay_fee：结算币种下的固定手续费
394:      *                       currency：三方结算币种，如 USDT
395:      *                       name：渠道目录中的渠道名，如 USDT-TRC20
396:      *                       platform：三方平台名，如 Obolum
397:      * @param string $grossUsd 钻石折算的美元金额
398:      * @return array{fee:string,net:string,currency:string,platform:string,channel:string,method:string}
399:      * @throws WithdrawalException
400:      */
401:     public static function calcOfficialSettlement(array $channel, string $grossUsd): array
402:     {
403:         // 1. 读取渠道计费参数：汇率、比例手续费、固定手续费、结算币种和三方渠道名。
404:         $rate = (float)($channel['pay_rate'] ?? 0);
405:         $feeRate = (float)($channel['pay_fee'] ?? 0);
406:         $feeFixed = (float)($channel['additional_pay_fee'] ?? 0);
407:         $currency = strtoupper(trim(strval($channel['currency'] ?? '')));
408:         $method = trim(strval($channel['name'] ?? ''));
409:
410:         // 2. 汇率、币种和渠道名是计算与打款的最小必需项；任一缺失都说明目录配置不完整。
411:         if ($rate <= 0 || $currency === '' || $method === '') {
412:             throw new \RuntimeException('anchorPayment OFFICIAL channel incomplete: ' . json_encode([
413:                 'currency' => $currency, 'method' => $method, 'pay_rate' => $rate,
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:403`

- 命中检索词：手续费
- 证据行：// 1. 读取渠道计费参数：汇率、比例手续费、固定手续费、结算币种和三方渠道名。

```text
391:      *                       pay_rate：美元换算成三方结算币种的倍率
392:      *                       pay_fee：按结算币种金额收取的比例手续费
393:      *                       additional_pay_fee：结算币种下的固定手续费
394:      *                       currency：三方结算币种，如 USDT
395:      *                       name：渠道目录中的渠道名，如 USDT-TRC20
396:      *                       platform：三方平台名，如 Obolum
397:      * @param string $grossUsd 钻石折算的美元金额
398:      * @return array{fee:string,net:string,currency:string,platform:string,channel:string,method:string}
399:      * @throws WithdrawalException
400:      */
401:     public static function calcOfficialSettlement(array $channel, string $grossUsd): array
402:     {
403:         // 1. 读取渠道计费参数：汇率、比例手续费、固定手续费、结算币种和三方渠道名。
404:         $rate = (float)($channel['pay_rate'] ?? 0);
405:         $feeRate = (float)($channel['pay_fee'] ?? 0);
406:         $feeFixed = (float)($channel['additional_pay_fee'] ?? 0);
407:         $currency = strtoupper(trim(strval($channel['currency'] ?? '')));
408:         $method = trim(strval($channel['name'] ?? ''));
409:
410:         // 2. 汇率、币种和渠道名是计算与打款的最小必需项；任一缺失都说明目录配置不完整。
411:         if ($rate <= 0 || $currency === '' || $method === '') {
412:             throw new \RuntimeException('anchorPayment OFFICIAL channel incomplete: ' . json_encode([
413:                 'currency' => $currency, 'method' => $method, 'pay_rate' => $rate,
414:             ]));
415:         }
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:421`

- 命中检索词：手续费
- 证据行：// 4. 手续费 = 结算金额 × 比例费率 + 固定手续费，同样保留两位小数。

```text
409:
410:         // 2. 汇率、币种和渠道名是计算与打款的最小必需项；任一缺失都说明目录配置不完整。
411:         if ($rate <= 0 || $currency === '' || $method === '') {
412:             throw new \RuntimeException('anchorPayment OFFICIAL channel incomplete: ' . json_encode([
413:                 'currency' => $currency, 'method' => $method, 'pay_rate' => $rate,
414:             ]));
415:         }
416:
417:         // 3. 先把用户提现的美元金额换算成三方结算币种金额，并统一保留两位小数。
418:         //    例如 $5 × USDT 的 pay_rate(1) = 5.00 USDT。
419:         $grossCurrency = round((float)$grossUsd * $rate, 2);
420:
421:         // 4. 手续费 = 结算金额 × 比例费率 + 固定手续费，同样保留两位小数。
422:         //    例如 5.00 × 1% + 2.50 = 2.55 USDT。
423:         $fee = round($grossCurrency * $feeRate + $feeFixed, 2);
424:
425:         // 5. 三方实际打给收款人的净额 = 结算金额 - 手续费。
426:         //    例如 5.00 - 2.55 = 2.45 USDT。
427:         $net = round($grossCurrency - $fee, 2);
428:
429:         // 6. 净额必须大于 0；否则说明固定费或比例费已吞掉全部金额，该渠道不可下单。
430:         if ($net <= 0) {
431:             throw new WithdrawalException(WithdrawalException::OFFICIAL_SETTLEMENT_AMOUNT_INVALID, [], [
432:                 'gross_usd' => $grossUsd,
433:                 'gross_currency' => $grossCurrency,
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:423`

- 命中检索词：$fee = round
- 证据行：$fee = round($grossCurrency * $feeRate + $feeFixed, 2);

```text
411:         if ($rate <= 0 || $currency === '' || $method === '') {
412:             throw new \RuntimeException('anchorPayment OFFICIAL channel incomplete: ' . json_encode([
413:                 'currency' => $currency, 'method' => $method, 'pay_rate' => $rate,
414:             ]));
415:         }
416:
417:         // 3. 先把用户提现的美元金额换算成三方结算币种金额，并统一保留两位小数。
418:         //    例如 $5 × USDT 的 pay_rate(1) = 5.00 USDT。
419:         $grossCurrency = round((float)$grossUsd * $rate, 2);
420:
421:         // 4. 手续费 = 结算金额 × 比例费率 + 固定手续费，同样保留两位小数。
422:         //    例如 5.00 × 1% + 2.50 = 2.55 USDT。
423:         $fee = round($grossCurrency * $feeRate + $feeFixed, 2);
424:
425:         // 5. 三方实际打给收款人的净额 = 结算金额 - 手续费。
426:         //    例如 5.00 - 2.55 = 2.45 USDT。
427:         $net = round($grossCurrency - $fee, 2);
428:
429:         // 6. 净额必须大于 0；否则说明固定费或比例费已吞掉全部金额，该渠道不可下单。
430:         if ($net <= 0) {
431:             throw new WithdrawalException(WithdrawalException::OFFICIAL_SETTLEMENT_AMOUNT_INVALID, [], [
432:                 'gross_usd' => $grossUsd,
433:                 'gross_currency' => $grossCurrency,
434:                 'fee' => $fee,
435:                 'net' => $net,
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:425`

- 命中检索词：手续费
- 证据行：// 5. 三方实际打给收款人的净额 = 结算金额 - 手续费。

```text
413:                 'currency' => $currency, 'method' => $method, 'pay_rate' => $rate,
414:             ]));
415:         }
416:
417:         // 3. 先把用户提现的美元金额换算成三方结算币种金额，并统一保留两位小数。
418:         //    例如 $5 × USDT 的 pay_rate(1) = 5.00 USDT。
419:         $grossCurrency = round((float)$grossUsd * $rate, 2);
420:
421:         // 4. 手续费 = 结算金额 × 比例费率 + 固定手续费，同样保留两位小数。
422:         //    例如 5.00 × 1% + 2.50 = 2.55 USDT。
423:         $fee = round($grossCurrency * $feeRate + $feeFixed, 2);
424:
425:         // 5. 三方实际打给收款人的净额 = 结算金额 - 手续费。
426:         //    例如 5.00 - 2.55 = 2.45 USDT。
427:         $net = round($grossCurrency - $fee, 2);
428:
429:         // 6. 净额必须大于 0；否则说明固定费或比例费已吞掉全部金额，该渠道不可下单。
430:         if ($net <= 0) {
431:             throw new WithdrawalException(WithdrawalException::OFFICIAL_SETTLEMENT_AMOUNT_INVALID, [], [
432:                 'gross_usd' => $grossUsd,
433:                 'gross_currency' => $grossCurrency,
434:                 'fee' => $fee,
435:                 'net' => $net,
436:                 'currency' => $currency,
437:                 'detail' => 'official settlement net amount must be greater than zero',
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:541`

- 命中检索词：手续费
- 证据行：* 官方渠道快照：校验 V2 仅开放的 OBO USDT 渠道，并返回手续费与打款金额。

```text
529:      * @param object $redis 需支持 set($key, 1, ['NX', 'EX' => n])：成功 true、已有 key false
530:      * @throws WithdrawalException
531:      */
532:     public static function assertNotFrequentByRedis($redis, int $uid): void
533:     {
534:         if (true !== $redis->set(self::COOLDOWN_KEY_PREFIX . $uid, 1, ['NX', 'EX' => self::COOLDOWN_SECONDS])) {
535:             throw new WithdrawalException(WithdrawalException::FREQUENT_OPERATION);
536:         }
537:     }
538:
539:     /**
540:      * 冻结钻石 + 建单 + 首条日志（同一主库事务）
541:      * 官方渠道快照：校验 V2 仅开放的 OBO USDT 渠道，并返回手续费与打款金额。
542:      *
543:      * @return array
544:      * @throws WithdrawalException
545:      */
546:     /**
547:      * 官方渠道快照：费率取目录，网络码取映射表
548:      *
549:      * 入参的渠道合法性已在上一步 verify 里查过（不在目录里根本到不了这里），
550:      * 这里不再拿入参和某个固定渠道做全等比较——官方渠道不再只有 USDT-TRC20 一个。
551:      *
552:      * @param array $channel verify 命中的渠道配置
553:      * @param string $income 钻石折算美元
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:557`

- 命中检索词：buildOfficialSnapshot
- 证据行：private static function buildOfficialSnapshot(array $channel, string $income): array

```text
545:      */
546:     /**
547:      * 官方渠道快照：费率取目录，网络码取映射表
548:      *
549:      * 入参的渠道合法性已在上一步 verify 里查过（不在目录里根本到不了这里），
550:      * 这里不再拿入参和某个固定渠道做全等比较——官方渠道不再只有 USDT-TRC20 一个。
551:      *
552:      * @param array $channel verify 命中的渠道配置
553:      * @param string $income 钻石折算美元
554:      * @return array
555:      * @throws WithdrawalException
556:      */
557:     private static function buildOfficialSnapshot(array $channel, string $income): array
558:     {
559:         $settlement = self::calcOfficialSettlement($channel, $income);
560:
561:         if ($settlement['channel'] === '') {
562:             throw new WithdrawalException(WithdrawalException::CHANNEL_UNAVAILABLE, [], [
563:                 'method' => $settlement['method'],
564:                 'reason' => 'official api channel not configured',
565:             ]);
566:         }
567:
568:         return $settlement;
569:     }
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:559`

- 命中检索词：calcOfficialSettlement
- 证据行：$settlement = self::calcOfficialSettlement($channel, $income);

```text
547:      * 官方渠道快照：费率取目录，网络码取映射表
548:      *
549:      * 入参的渠道合法性已在上一步 verify 里查过（不在目录里根本到不了这里），
550:      * 这里不再拿入参和某个固定渠道做全等比较——官方渠道不再只有 USDT-TRC20 一个。
551:      *
552:      * @param array $channel verify 命中的渠道配置
553:      * @param string $income 钻石折算美元
554:      * @return array
555:      * @throws WithdrawalException
556:      */
557:     private static function buildOfficialSnapshot(array $channel, string $income): array
558:     {
559:         $settlement = self::calcOfficialSettlement($channel, $income);
560:
561:         if ($settlement['channel'] === '') {
562:             throw new WithdrawalException(WithdrawalException::CHANNEL_UNAVAILABLE, [], [
563:                 'method' => $settlement['method'],
564:                 'reason' => 'official api channel not configured',
565:             ]);
566:         }
567:
568:         return $settlement;
569:     }
570:
571:     /**
```

### `waky6_miyouphp/php/action/DiamondControl.php:17`

- 命中检索词：DiamondControl
- 证据行：class DiamondControl

```text
5: use Lang\Lang;
6: use Model\AnchorModel;
7: use Model\DiamondModel;
8: use Model\DiamondsWithdrawalOrderModel;
9: use Domain\DiamondExchange\Service\DiamondExchangeService;
10: use Services\Diamond\DiamondServices;
11: use Services\Diamond\WithdrawalChannelConfigService;
12: use Services\Diamond\WithdrawalOrderDetailMock;
13: use Services\Diamond\WithdrawalOrderDetailService;
14: use Services\Anchor\AdvanceServices;
15: use Timezone\Auth;
16:
17: class DiamondControl
18: {
19:
20:     /**
21:      * 发起钻石提现（用户侧下单，骨架 §9.2 定版）
22:      * action=Action/Diamond.withdrawOrder
23:      *
24:      * 双轨口径不变：**旧 diamondWithdraw() 一行未改**，本方法是新版链路入口，
25:      * 开放范围由 WithdrawalModeResolver 判定（区 rebate_mode=2 全区放行，区仍为 1 时只放行灰度名单内用户），
26:      * 未开放一律返回「该区未开放」，其余用户继续走老方法。
27:      * 官方渠道与币商渠道共用这个入口，分叉在 WithdrawalOrderCreateService 里。
28:      *
29:      * 收款账户按 waky7 口径平铺传（payeeAccountNo / payeeName / payeePhone / payeeBankName / payeeIfscCode
```

### `waky6_miyouphp/php/action/DiamondControl.php:21`

- 命中检索词：钻石提现
- 证据行：* 发起钻石提现（用户侧下单，骨架 §9.2 定版）

```text
9: use Domain\DiamondExchange\Service\DiamondExchangeService;
10: use Services\Diamond\DiamondServices;
11: use Services\Diamond\WithdrawalChannelConfigService;
12: use Services\Diamond\WithdrawalOrderDetailMock;
13: use Services\Diamond\WithdrawalOrderDetailService;
14: use Services\Anchor\AdvanceServices;
15: use Timezone\Auth;
16:
17: class DiamondControl
18: {
19:
20:     /**
21:      * 发起钻石提现（用户侧下单，骨架 §9.2 定版）
22:      * action=Action/Diamond.withdrawOrder
23:      *
24:      * 双轨口径不变：**旧 diamondWithdraw() 一行未改**，本方法是新版链路入口，
25:      * 开放范围由 WithdrawalModeResolver 判定（区 rebate_mode=2 全区放行，区仍为 1 时只放行灰度名单内用户），
26:      * 未开放一律返回「该区未开放」，其余用户继续走老方法。
27:      * 官方渠道与币商渠道共用这个入口，分叉在 WithdrawalOrderCreateService 里。
28:      *
29:      * 收款账户按 waky7 口径平铺传（payeeAccountNo / payeeName / payeePhone / payeeBankName / payeeIfscCode
30:      * 中取该渠道在 anchorPayment 目录里声明的那几个），不再有 account_info 这个 JSON 块；
31:      * currency_type 传目录一维键（AR / ET / USDT / Epay-USD…）、pay_channel 传渠道名（Telebirr / Bank /
32:      * USDT-TRC20…），两者都**不是币种**，且要和目录逐字符一致（大小写不同会被判渠道不可用）。
33:      * currency_type 也接受 getWithdrawalConfig 里 channels 的「国家_币种」拼接键（VN_VND）——服务端拆回
```

### `waky6_miyouphp/php/action/DiamondControl.php:22`

- 命中检索词：withdrawOrder
- 证据行：* action=Action/Diamond.withdrawOrder

```text
10: use Services\Diamond\DiamondServices;
11: use Services\Diamond\WithdrawalChannelConfigService;
12: use Services\Diamond\WithdrawalOrderDetailMock;
13: use Services\Diamond\WithdrawalOrderDetailService;
14: use Services\Anchor\AdvanceServices;
15: use Timezone\Auth;
16:
17: class DiamondControl
18: {
19:
20:     /**
21:      * 发起钻石提现（用户侧下单，骨架 §9.2 定版）
22:      * action=Action/Diamond.withdrawOrder
23:      *
24:      * 双轨口径不变：**旧 diamondWithdraw() 一行未改**，本方法是新版链路入口，
25:      * 开放范围由 WithdrawalModeResolver 判定（区 rebate_mode=2 全区放行，区仍为 1 时只放行灰度名单内用户），
26:      * 未开放一律返回「该区未开放」，其余用户继续走老方法。
27:      * 官方渠道与币商渠道共用这个入口，分叉在 WithdrawalOrderCreateService 里。
28:      *
29:      * 收款账户按 waky7 口径平铺传（payeeAccountNo / payeeName / payeePhone / payeeBankName / payeeIfscCode
30:      * 中取该渠道在 anchorPayment 目录里声明的那几个），不再有 account_info 这个 JSON 块；
31:      * currency_type 传目录一维键（AR / ET / USDT / Epay-USD…）、pay_channel 传渠道名（Telebirr / Bank /
32:      * USDT-TRC20…），两者都**不是币种**，且要和目录逐字符一致（大小写不同会被判渠道不可用）。
33:      * currency_type 也接受 getWithdrawalConfig 里 channels 的「国家_币种」拼接键（VN_VND）——服务端拆回
34:      * 国家段定位，币种段只用来和目录币种核对，落库的 payment.country 与 payment_method 恒为拆出来的目录键。
```

### `waky6_miyouphp/php/action/DiamondControl.php:27`

- 命中检索词：WithdrawalOrderCreateService
- 证据行：* 官方渠道与币商渠道共用这个入口，分叉在 WithdrawalOrderCreateService 里。

```text
15: use Timezone\Auth;
16:
17: class DiamondControl
18: {
19:
20:     /**
21:      * 发起钻石提现（用户侧下单，骨架 §9.2 定版）
22:      * action=Action/Diamond.withdrawOrder
23:      *
24:      * 双轨口径不变：**旧 diamondWithdraw() 一行未改**，本方法是新版链路入口，
25:      * 开放范围由 WithdrawalModeResolver 判定（区 rebate_mode=2 全区放行，区仍为 1 时只放行灰度名单内用户），
26:      * 未开放一律返回「该区未开放」，其余用户继续走老方法。
27:      * 官方渠道与币商渠道共用这个入口，分叉在 WithdrawalOrderCreateService 里。
28:      *
29:      * 收款账户按 waky7 口径平铺传（payeeAccountNo / payeeName / payeePhone / payeeBankName / payeeIfscCode
30:      * 中取该渠道在 anchorPayment 目录里声明的那几个），不再有 account_info 这个 JSON 块；
31:      * currency_type 传目录一维键（AR / ET / USDT / Epay-USD…）、pay_channel 传渠道名（Telebirr / Bank /
32:      * USDT-TRC20…），两者都**不是币种**，且要和目录逐字符一致（大小写不同会被判渠道不可用）。
33:      * currency_type 也接受 getWithdrawalConfig 里 channels 的「国家_币种」拼接键（VN_VND）——服务端拆回
34:      * 国家段定位，币种段只用来和目录币种核对，落库的 payment.country 与 payment_method 恒为拆出来的目录键。
35:      *
36:      * order_type=dealer 且 withdraw_mode=appoint 时，dealer_uid 必须是在用的 1 级币商、与提现人同区、
37:      * 不是提现人自己，且（灰度期）在 withdrawal_gray.agent_uids 名单内；四种都报 91020006。
38:      * 渠道/接单开关/是否可被指定仍由派单再筛。
39:      *
```

### `waky6_miyouphp/php/action/DiamondControl.php:44`

- 命中检索词：withdrawOrder
- 证据行：public function withdrawOrder($request)

```text
32:      * USDT-TRC20…），两者都**不是币种**，且要和目录逐字符一致（大小写不同会被判渠道不可用）。
33:      * currency_type 也接受 getWithdrawalConfig 里 channels 的「国家_币种」拼接键（VN_VND）——服务端拆回
34:      * 国家段定位，币种段只用来和目录币种核对，落库的 payment.country 与 payment_method 恒为拆出来的目录键。
35:      *
36:      * order_type=dealer 且 withdraw_mode=appoint 时，dealer_uid 必须是在用的 1 级币商、与提现人同区、
37:      * 不是提现人自己，且（灰度期）在 withdrawal_gray.agent_uids 名单内；四种都报 91020006。
38:      * 渠道/接单开关/是否可被指定仍由派单再筛。
39:      *
40:      * @param array $request
41:      * @return array
42:      * @throws \Exception
43:      */
44:     public function withdrawOrder($request)
45:     {
46:         $uid = intval(\Request::CheckLogin());
47:
48:         $input = [
49:             'order_type' => $request['order_type'] ?? '',
50:             'withdraw_mode' => $request['withdraw_mode'] ?? '',
51:             'dealer_uid' => $request['dealer_uid'] ?? 0,
52:             'diamonds' => $request['diamonds'] ?? 0,
53:             'currency_type' => $request['currency_type'] ?? '',
54:             'pay_channel' => $request['pay_channel'] ?? '',
55:             'remark' => $request['remark'] ?? '',
56:         ];
```

### `waky6_miyouphp/php/action/DiamondControl.php:59`

- 命中检索词：WithdrawalOrderCreateService
- 证据行：return (new \Domain\Withdrawal\Service\WithdrawalOrderCreateService())->create($uid, $input, $request);

```text
47:
48:         $input = [
49:             'order_type' => $request['order_type'] ?? '',
50:             'withdraw_mode' => $request['withdraw_mode'] ?? '',
51:             'dealer_uid' => $request['dealer_uid'] ?? 0,
52:             'diamonds' => $request['diamonds'] ?? 0,
53:             'currency_type' => $request['currency_type'] ?? '',
54:             'pay_channel' => $request['pay_channel'] ?? '',
55:             'remark' => $request['remark'] ?? '',
56:         ];
57:
58:         try {
59:             return (new \Domain\Withdrawal\Service\WithdrawalOrderCreateService())->create($uid, $input, $request);
60:         } catch (\Domain\Withdrawal\Exception\WithdrawalException $e) {
61:             // 仓库没有把领域异常转成接口报文的全局处理器，在入口显式转成 code + msg
62:             // 客户端只看固定文案，渠道/字段的排查原文落日志（由 WithdrawalChannelService 挂在 extData.detail）
63:             \ServiceFactory::getLogger()->logInfo(
64:                 "withdrawOrder uid:$uid failed code:{$e->fullCode} ext:"
65:                 . json_encode($e->extData, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)
66:             );
67:
68:             \Application::stop($e->getMessage(), $e->fullCode);
69:         }
70:     }
71:
```

### `waky6_miyouphp/php/action/DiamondControl.php:64`

- 命中检索词：withdrawOrder
- 证据行："withdrawOrder uid:$uid failed code:{$e->fullCode} ext:"

```text
52:             'diamonds' => $request['diamonds'] ?? 0,
53:             'currency_type' => $request['currency_type'] ?? '',
54:             'pay_channel' => $request['pay_channel'] ?? '',
55:             'remark' => $request['remark'] ?? '',
56:         ];
57:
58:         try {
59:             return (new \Domain\Withdrawal\Service\WithdrawalOrderCreateService())->create($uid, $input, $request);
60:         } catch (\Domain\Withdrawal\Exception\WithdrawalException $e) {
61:             // 仓库没有把领域异常转成接口报文的全局处理器，在入口显式转成 code + msg
62:             // 客户端只看固定文案，渠道/字段的排查原文落日志（由 WithdrawalChannelService 挂在 extData.detail）
63:             \ServiceFactory::getLogger()->logInfo(
64:                 "withdrawOrder uid:$uid failed code:{$e->fullCode} ext:"
65:                 . json_encode($e->extData, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)
66:             );
67:
68:             \Application::stop($e->getMessage(), $e->fullCode);
69:         }
70:     }
71:
72:     /**
73:      * 确认提现订单到账（用户侧，骨架 §9.1 定版）
74:      * action=Action/Diamond.confirmWithdrawalOrder（方法名与 waky7 AdvanceServices 同名，客户端契约不可改）
75:      *
76:      * 只改态并发事件，入账由消费侧 confirmed() 做（CAS 幂等锚保护重复执行）。
```

### `waky6_miyouphp/php/action/DiamondControl.php:77`

- 命中检索词：withdrawOrder
- 证据行：* 与 withdrawOrder 一起留在本控制器：用户侧三个提现入口（下单/详情/确认）统一是 Action/Diamond.* 前缀，

```text
65:                 . json_encode($e->extData, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)
66:             );
67:
68:             \Application::stop($e->getMessage(), $e->fullCode);
69:         }
70:     }
71:
72:     /**
73:      * 确认提现订单到账（用户侧，骨架 §9.1 定版）
74:      * action=Action/Diamond.confirmWithdrawalOrder（方法名与 waky7 AdvanceServices 同名，客户端契约不可改）
75:      *
76:      * 只改态并发事件，入账由消费侧 confirmed() 做（CAS 幂等锚保护重复执行）。
77:      * 与 withdrawOrder 一起留在本控制器：用户侧三个提现入口（下单/详情/确认）统一是 Action/Diamond.* 前缀，
78:      * 不为了「新链路」再拆一个只有一个方法的控制器。
79:      *
80:      * @return bool
81:      * @throws \Exception
82:      */
83:     public function confirmWithdrawalOrder($request)
84:     {
85:         $uid = intval(\Request::CheckLogin());
86:         $orderId = intval(\Request::CheckInt('order_id'));
87:
88:         try {
89:             (new \Domain\Withdrawal\Service\WithdrawalOrderStateService())->confirmByUser($uid, $orderId);
```

### `waky6_miyouphp/php/action/DiamondControl.php:458`

- 命中检索词：DiamondControl
- 证据行：* action=Action/Diamond.getWithdrawalOrder（方法名即客户端契约，与 waky7 DiamondControl:253 同名，不可改）

```text
446:      */
447:     public function getAgentDiamondIMList($request)
448:     {
449:         $uid = \Request::CheckLogin();
450:         $page = max(intval(\Request::GetInt('page', 1)), 1);
451:         $pageSize = intval(\Request::GetInt('pageSize', 20));
452:
453:         return (new DiamondServices())->getAgentDiamondIMList($uid, $page, $pageSize);
454:     }
455:
456:     /**
457:      * 用户侧「提现订单详情」
458:      * action=Action/Diamond.getWithdrawalOrder（方法名即客户端契约，与 waky7 DiamondControl:253 同名，不可改）
459:      * 骨架 §13.3.7 出参定版；鉴权只看下单人本人
460:      *
461:      * 带 is_mock=1 返回写死的假单，供端上在没有真实订单时先把详情页联调通（闸门见 WithdrawalOrderDetailMock::shouldServe）
462:      *
463:      * @return array
464:      * @throws \Exception
465:      */
466:     public function getWithdrawalOrder($request)
467:     {
468:         $uid = \Request::CheckLogin();
469:
470:         // 1. mock 分支要在 order_id 校验之前：假单不需要真实订单号
```

### `waky6_miyouphp/php/lang/zh/exception.php:28`

- 命中检索词：手续费
- 证据行：'WITHDRAWAL_OFFICIAL_SETTLEMENT_AMOUNT_INVALID' => '提现金额过低，无法覆盖打款手续费',

```text
16:     'WITHDRAWAL_INSUFFICIENT_DIAMONDS' => '钻石余额不足',
17:     'WITHDRAWAL_ACCOUNT_INFO_INVALID' => '收款信息不正确：#name#',
18:     'WITHDRAWAL_CHANNEL_UNAVAILABLE' => '收款渠道暂不可用',
19:     'WITHDRAWAL_MERCHANT_UNAVAILABLE' => '该币商无法接此单',
20:     'WITHDRAWAL_REGION_NOT_OPEN' => '当前地区暂未开放新版提现',
21:     'WITHDRAWAL_FREQUENT_OPERATION' => '操作过于频繁',
22:     'WITHDRAWAL_WRITE_NOT_IMPLEMENTED' => '提现暂不可用，请稍后再试',
23:     'WITHDRAWAL_ORDER_NOT_FOUND' => '提现订单不存在',
24:     'WITHDRAWAL_NOT_MY_ORDER' => '该提现订单不属于当前账号',
25:     'WITHDRAWAL_ORDER_STATUS_CHANGED' => '订单状态已变更，请刷新后重试',
26:     'WITHDRAWAL_CONFIRM_WINDOW_CLOSED' => '已超出确认到账时间',
27:     'WITHDRAWAL_ORDER_NOT_ACTIONABLE' => '该订单当前无法处理',
28:     'WITHDRAWAL_OFFICIAL_SETTLEMENT_AMOUNT_INVALID' => '提现金额过低，无法覆盖打款手续费',
29:     'WITHDRAWAL_OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED' => '官方提现暂不可用，请选择币商渠道或稍后重试',
30:     'WITHDRAWAL_DEALER_WITHDRAWAL_FUSE_TRIGGERED' => '币商提现暂不可用，请稍后重试',
31:
32: ];
```

### `waky6_miyouphp/php/lang/zh/withdrawal.php:4`

- 命中检索词：钻石提现
- 证据行：* 钻石提现订单通知文案（骨架 §13.3.2 通知矩阵）

```text
1: <?php
2:
3: /**
4:  * 钻石提现订单通知文案（骨架 §13.3.2 通知矩阵）
5:  * 文案与 waky7 php/lang/zh/withdrawal.php 对齐；%s 由调用方替换
6:  * ⚠️ auto_confirm_desc / auto_reject_desc 里的「2小时」对应 config/coins_agent_dispatch.php
7:  *    的付款与确认时效，改阈值时必须同步改文案
8:  */
9: return [
10:     'withdrawalOrder_wait_confirm_title' => "提现订单到账待确认！",
11:     'withdrawalOrder_wait_confirm_desc' => "提现钻石: %s",
12:
13:     'withdrawalOrder_wait_dispatch_title' => "有新的提现订单待处理！",
14:     'withdrawalOrder_wait_dispatch_desc' => "预计获得钻石: %s",
15:
16:     'withdrawalOrder_completed_title' => "提现订单已完成！",
```

### `waky6_miyouphp/php/config/anchorPayment.php:43`

- 命中检索词：pay_fee
- 证据行：'pay_fee' => 0.03,

```text
31:                         'title' => 'payeeName',
32:                         'kumai-showTitle' => 'AE-Emirates-NBD-payeeName',
33:                         'rules' => [
34:                             'required' => true,
35:                             'type' => [2,3,4],
36:                             'mode' => 'input',
37:                             'regex' => '^[A-Za-z ]{2,60}$',
38:                             'limit' => 60,
39:                         ],
40:                         'kumai-desc' => 'AE-Emirates-NBD-payeeName-desc',
41:                     ],
42:                 ],
43:                 'pay_fee' => 0.03,
44:                 'additional_pay_fee' => 0,
45:                 'transfer_reward' => 0.03,
46:                 'pay_rate' => 3.666679649,
47:                 'currency' => 'AED',
48:             ],
49:             [
50:                 'supported_regions' => ['global'],  //global为全部开放
51:                 'name' => 'FAB',
52:                 'img_url' => 'uc/img/FAB.png',
53:                 'kumai-desc' => 'AE-FAB-desc',
54:                 'params' => [
55:                     [
```

### `waky6_miyouphp/php/config/anchorPayment.php:44`

- 命中检索词：pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
32:                         'kumai-showTitle' => 'AE-Emirates-NBD-payeeName',
33:                         'rules' => [
34:                             'required' => true,
35:                             'type' => [2,3,4],
36:                             'mode' => 'input',
37:                             'regex' => '^[A-Za-z ]{2,60}$',
38:                             'limit' => 60,
39:                         ],
40:                         'kumai-desc' => 'AE-Emirates-NBD-payeeName-desc',
41:                     ],
42:                 ],
43:                 'pay_fee' => 0.03,
44:                 'additional_pay_fee' => 0,
45:                 'transfer_reward' => 0.03,
46:                 'pay_rate' => 3.666679649,
47:                 'currency' => 'AED',
48:             ],
49:             [
50:                 'supported_regions' => ['global'],  //global为全部开放
51:                 'name' => 'FAB',
52:                 'img_url' => 'uc/img/FAB.png',
53:                 'kumai-desc' => 'AE-FAB-desc',
54:                 'params' => [
55:                     [
56:                         'title' => 'payeeAccountNo',
```

### `waky6_miyouphp/php/config/anchorPayment.php:80`

- 命中检索词：pay_fee
- 证据行：'pay_fee' => 0.03,

```text
68:                         'title' => 'payeeName',
69:                         'kumai-showTitle' => 'AE-FAB-payeeName',
70:                         'rules' => [
71:                             'required' => true,
72:                             'type' => [2,3,4],
73:                             'mode' => 'input',
74:                             'regex' => '^[A-Za-z ]{2,60}$',
75:                             'limit' => 60,
76:                         ],
77:                         'kumai-desc' => 'AE-FAB-payeeName-desc',
78:                     ],
79:                 ],
80:                 'pay_fee' => 0.03,
81:                 'additional_pay_fee' => 0,
82:                 'transfer_reward' => 0.03,
83:                 'pay_rate' => 3.666679649,
84:                 'currency' => 'AED',
85:             ],
86:             [
87:                 'supported_regions' => ['global'],  //global为全部开放
88:                 'name' => 'ADCB',
89:                 'img_url' => 'uc/img/ADCB.png',
90:                 'kumai-desc' => 'AE-ADCB-desc',
91:                 'params' => [
92:                     [
```

### `waky6_miyouphp/php/config/anchorPayment.php:81`

- 命中检索词：pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
69:                         'kumai-showTitle' => 'AE-FAB-payeeName',
70:                         'rules' => [
71:                             'required' => true,
72:                             'type' => [2,3,4],
73:                             'mode' => 'input',
74:                             'regex' => '^[A-Za-z ]{2,60}$',
75:                             'limit' => 60,
76:                         ],
77:                         'kumai-desc' => 'AE-FAB-payeeName-desc',
78:                     ],
79:                 ],
80:                 'pay_fee' => 0.03,
81:                 'additional_pay_fee' => 0,
82:                 'transfer_reward' => 0.03,
83:                 'pay_rate' => 3.666679649,
84:                 'currency' => 'AED',
85:             ],
86:             [
87:                 'supported_regions' => ['global'],  //global为全部开放
88:                 'name' => 'ADCB',
89:                 'img_url' => 'uc/img/ADCB.png',
90:                 'kumai-desc' => 'AE-ADCB-desc',
91:                 'params' => [
92:                     [
93:                         'title' => 'payeeAccountNo',
```

### `waky6_miyouphp/php/config/anchorPayment.php:117`

- 命中检索词：pay_fee
- 证据行：'pay_fee' => 0.03,

```text
105:                         'title' => 'payeeName',
106:                         'kumai-showTitle' => 'AE-ADCB-payeeName',
107:                         'rules' => [
108:                             'required' => true,
109:                             'type' => [2,3,4],
110:                             'mode' => 'input',
111:                             'regex' => '^[A-Za-z ]{2,60}$',
112:                             'limit' => 60,
113:                         ],
114:                         'kumai-desc' => 'AE-ADCB-payeeName-desc',
115:                     ],
116:                 ],
117:                 'pay_fee' => 0.03,
118:                 'additional_pay_fee' => 0,
119:                 'transfer_reward' => 0.03,
120:                 'pay_rate' => 3.666679649,
121:                 'currency' => 'AED',
122:             ],
123:             [
124:                 'supported_regions' => ['global'],  //global为全部开放
125:                 'name' => 'Mashreqbank',
126:                 'img_url' => 'uc/img/Mashreqbank.png',
127:                 'kumai-desc' => 'AE-Mashreqbank-desc',
128:                 'params' => [
129:                     [
```

### `waky6_miyouphp/php/config/anchorPayment.php:118`

- 命中检索词：pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
106:                         'kumai-showTitle' => 'AE-ADCB-payeeName',
107:                         'rules' => [
108:                             'required' => true,
109:                             'type' => [2,3,4],
110:                             'mode' => 'input',
111:                             'regex' => '^[A-Za-z ]{2,60}$',
112:                             'limit' => 60,
113:                         ],
114:                         'kumai-desc' => 'AE-ADCB-payeeName-desc',
115:                     ],
116:                 ],
117:                 'pay_fee' => 0.03,
118:                 'additional_pay_fee' => 0,
119:                 'transfer_reward' => 0.03,
120:                 'pay_rate' => 3.666679649,
121:                 'currency' => 'AED',
122:             ],
123:             [
124:                 'supported_regions' => ['global'],  //global为全部开放
125:                 'name' => 'Mashreqbank',
126:                 'img_url' => 'uc/img/Mashreqbank.png',
127:                 'kumai-desc' => 'AE-Mashreqbank-desc',
128:                 'params' => [
129:                     [
130:                         'title' => 'payeeAccountNo',
```

### `waky6_miyouphp/php/config/anchorPayment.php:154`

- 命中检索词：pay_fee
- 证据行：'pay_fee' => 0.03,

```text
142:                         'title' => 'payeeName',
143:                         'kumai-showTitle' => 'AE-Mashreqbank-payeeName',
144:                         'rules' => [
145:                             'required' => true,
146:                             'type' => [2,3,4],
147:                             'mode' => 'input',
148:                             'regex' => '^[A-Za-z ]{2,60}$',
149:                             'limit' => 60,
150:                         ],
151:                         'kumai-desc' => 'AE-Mashreqbank-payeeName-desc',
152:                     ],
153:                 ],
154:                 'pay_fee' => 0.03,
155:                 'additional_pay_fee' => 0,
156:                 'transfer_reward' => 0.03,
157:                 'pay_rate' => 3.666679649,
158:                 'currency' => 'AED',
159:             ],
160:             [
161:                 'supported_regions' => ['global'],  //global为全部开放
162:                 'name' => 'Dubai Islamic Bank',
163:                 'img_url' => 'uc/img/Dubai Islamic Bank.png',
164:                 'kumai-desc' => 'AE-Dubai-Islamic-Bank-desc',
165:                 'params' => [
166:                     [
```

### `waky6_miyouphp/php/config/anchorPayment.php:155`

- 命中检索词：pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
143:                         'kumai-showTitle' => 'AE-Mashreqbank-payeeName',
144:                         'rules' => [
145:                             'required' => true,
146:                             'type' => [2,3,4],
147:                             'mode' => 'input',
148:                             'regex' => '^[A-Za-z ]{2,60}$',
149:                             'limit' => 60,
150:                         ],
151:                         'kumai-desc' => 'AE-Mashreqbank-payeeName-desc',
152:                     ],
153:                 ],
154:                 'pay_fee' => 0.03,
155:                 'additional_pay_fee' => 0,
156:                 'transfer_reward' => 0.03,
157:                 'pay_rate' => 3.666679649,
158:                 'currency' => 'AED',
159:             ],
160:             [
161:                 'supported_regions' => ['global'],  //global为全部开放
162:                 'name' => 'Dubai Islamic Bank',
163:                 'img_url' => 'uc/img/Dubai Islamic Bank.png',
164:                 'kumai-desc' => 'AE-Dubai-Islamic-Bank-desc',
165:                 'params' => [
166:                     [
167:                         'title' => 'payeeAccountNo',
```

### `waky6_miyouphp/php/config/anchorPayment.php:191`

- 命中检索词：pay_fee
- 证据行：'pay_fee' => 0.03,

```text
179:                         'title' => 'payeeName',
180:                         'kumai-showTitle' => 'AE-Dubai-Islamic-Bank-payeeName',
181:                         'rules' => [
182:                             'required' => true,
183:                             'type' => [2,3,4],
184:                             'mode' => 'input',
185:                             'regex' => '^[A-Za-z ]{2,60}$',
186:                             'limit' => 60,
187:                         ],
188:                         'kumai-desc' => 'AE-Dubai-Islamic-Bank-payeeName-desc',
189:                     ],
190:                 ],
191:                 'pay_fee' => 0.03,
192:                 'additional_pay_fee' => 0,
193:                 'transfer_reward' => 0.03,
194:                 'pay_rate' => 3.666679649,
195:                 'currency' => 'AED',
196:             ],
197:         ],
198:         'DZ' => [ // 阿尔及利亚（Algeria）
199:             [
200:                 'supported_regions' => ['global'],  //global为全部开放
201:                 'name' => 'BNA',
202:                 'img_url' => 'uc/img/BNA.png',
203:                 'kumai-desc' => 'DZ-BNA-desc',
```

### `waky6_miyouphp/php/config/anchorPayment.php:192`

- 命中检索词：pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
180:                         'kumai-showTitle' => 'AE-Dubai-Islamic-Bank-payeeName',
181:                         'rules' => [
182:                             'required' => true,
183:                             'type' => [2,3,4],
184:                             'mode' => 'input',
185:                             'regex' => '^[A-Za-z ]{2,60}$',
186:                             'limit' => 60,
187:                         ],
188:                         'kumai-desc' => 'AE-Dubai-Islamic-Bank-payeeName-desc',
189:                     ],
190:                 ],
191:                 'pay_fee' => 0.03,
192:                 'additional_pay_fee' => 0,
193:                 'transfer_reward' => 0.03,
194:                 'pay_rate' => 3.666679649,
195:                 'currency' => 'AED',
196:             ],
197:         ],
198:         'DZ' => [ // 阿尔及利亚（Algeria）
199:             [
200:                 'supported_regions' => ['global'],  //global为全部开放
201:                 'name' => 'BNA',
202:                 'img_url' => 'uc/img/BNA.png',
203:                 'kumai-desc' => 'DZ-BNA-desc',
204:                 'params' => [
```

### `waky6_miyouphp/php/config/anchorPayment.php:230`

- 命中检索词：pay_fee
- 证据行：'pay_fee' => 0.03,

```text
218:                         'title' => 'payeeName',
219:                         'kumai-showTitle' => 'DZ-BNA-payeeName',
220:                         'rules' => [
221:                             'required' => true,
222:                             'type' => [2,3,4],
223:                             'mode' => 'input',
224:                             'regex' => '^[A-Za-z ]{2,60}$',
225:                             'limit' => 60,
226:                         ],
227:                         'kumai-desc' => 'DZ-BNA-payeeName-desc',
228:                     ],
229:                 ],
230:                 'pay_fee' => 0.03,
231:                 'additional_pay_fee' => 0,
232:                 'transfer_reward' => 0.03,
233:                 'pay_rate' => 249.8477118,
234:                 'currency' => 'DZD',
235:             ],
236:             [
237:                 'supported_regions' => ['global'],  //global为全部开放
238:                 'name' => 'CPA',
239:                 'img_url' => 'uc/img/CPA.png',
240:                 'kumai-desc' => 'DZ-CPA-desc',
241:                 'params' => [
242:                     [
```

### `waky6_miyouphp/php/config/anchorPayment.php:231`

- 命中检索词：pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
219:                         'kumai-showTitle' => 'DZ-BNA-payeeName',
220:                         'rules' => [
221:                             'required' => true,
222:                             'type' => [2,3,4],
223:                             'mode' => 'input',
224:                             'regex' => '^[A-Za-z ]{2,60}$',
225:                             'limit' => 60,
226:                         ],
227:                         'kumai-desc' => 'DZ-BNA-payeeName-desc',
228:                     ],
229:                 ],
230:                 'pay_fee' => 0.03,
231:                 'additional_pay_fee' => 0,
232:                 'transfer_reward' => 0.03,
233:                 'pay_rate' => 249.8477118,
234:                 'currency' => 'DZD',
235:             ],
236:             [
237:                 'supported_regions' => ['global'],  //global为全部开放
238:                 'name' => 'CPA',
239:                 'img_url' => 'uc/img/CPA.png',
240:                 'kumai-desc' => 'DZ-CPA-desc',
241:                 'params' => [
242:                     [
243:                         'title' => 'payeeAccountNo',
```

### `waky6_miyouphp/php/config/anchorPayment.php:267`

- 命中检索词：pay_fee
- 证据行：'pay_fee' => 0.03,

```text
255:                         'title' => 'payeeName',
256:                         'kumai-showTitle' => 'DZ-CPA-payeeName',
257:                         'rules' => [
258:                             'required' => true,
259:                             'type' => [2,3,4],
260:                             'mode' => 'input',
261:                             'regex' => '^[A-Za-z ]{2,60}$',
262:                             'limit' => 60,
263:                         ],
264:                         'kumai-desc' => 'DZ-CPA-payeeName-desc',
265:                     ],
266:                 ],
267:                 'pay_fee' => 0.03,
268:                 'additional_pay_fee' => 0,
269:                 'transfer_reward' => 0.03,
270:                 'pay_rate' => 249.8477118,
271:                 'currency' => 'DZD',
272:             ],
273:             [
274:                 'supported_regions' => ['global'],  //global为全部开放
275:                 'name' => 'BADR',
276:                 'img_url' => 'uc/img/BADR.png',
277:                 'kumai-desc' => 'DZ-BADR-desc',
278:                 'params' => [
279:                     [
```

### `waky6_miyouphp/php/config/anchorPayment.php:268`

- 命中检索词：pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
256:                         'kumai-showTitle' => 'DZ-CPA-payeeName',
257:                         'rules' => [
258:                             'required' => true,
259:                             'type' => [2,3,4],
260:                             'mode' => 'input',
261:                             'regex' => '^[A-Za-z ]{2,60}$',
262:                             'limit' => 60,
263:                         ],
264:                         'kumai-desc' => 'DZ-CPA-payeeName-desc',
265:                     ],
266:                 ],
267:                 'pay_fee' => 0.03,
268:                 'additional_pay_fee' => 0,
269:                 'transfer_reward' => 0.03,
270:                 'pay_rate' => 249.8477118,
271:                 'currency' => 'DZD',
272:             ],
273:             [
274:                 'supported_regions' => ['global'],  //global为全部开放
275:                 'name' => 'BADR',
276:                 'img_url' => 'uc/img/BADR.png',
277:                 'kumai-desc' => 'DZ-BADR-desc',
278:                 'params' => [
279:                     [
280:                         'title' => 'payeeAccountNo',
```

### `waky6_miyouphp/php/config/anchorPayment.php:304`

- 命中检索词：pay_fee
- 证据行：'pay_fee' => 0.03,

```text
292:                         'title' => 'payeeName',
293:                         'kumai-showTitle' => 'DZ-BADR-payeeName',
294:                         'rules' => [
295:                             'required' => true,
296:                             'type' => [2,3,4],
297:                             'mode' => 'input',
298:                             'regex' => '^[A-Za-z ]{2,60}$',
299:                             'limit' => 60,
300:                         ],
301:                         'kumai-desc' => 'DZ-BADR-payeeName-desc',
302:                     ],
303:                 ],
304:                 'pay_fee' => 0.03,
305:                 'additional_pay_fee' => 0,
306:                 'transfer_reward' => 0.03,
307:                 'pay_rate' => 249.8477118,
308:                 'currency' => 'DZD',
309:             ],
310:             [
311:                 'supported_regions' => ['global'],  //global为全部开放
312:                 'name' => 'BEA',
313:                 'img_url' => 'uc/img/BEA.png',
314:                 'kumai-desc' => 'DZ-BEA-desc',
315:                 'params' => [
316:                     [
```

### `waky6_miyouphp/php/config/anchorPayment.php:305`

- 命中检索词：pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
293:                         'kumai-showTitle' => 'DZ-BADR-payeeName',
294:                         'rules' => [
295:                             'required' => true,
296:                             'type' => [2,3,4],
297:                             'mode' => 'input',
298:                             'regex' => '^[A-Za-z ]{2,60}$',
299:                             'limit' => 60,
300:                         ],
301:                         'kumai-desc' => 'DZ-BADR-payeeName-desc',
302:                     ],
303:                 ],
304:                 'pay_fee' => 0.03,
305:                 'additional_pay_fee' => 0,
306:                 'transfer_reward' => 0.03,
307:                 'pay_rate' => 249.8477118,
308:                 'currency' => 'DZD',
309:             ],
310:             [
311:                 'supported_regions' => ['global'],  //global为全部开放
312:                 'name' => 'BEA',
313:                 'img_url' => 'uc/img/BEA.png',
314:                 'kumai-desc' => 'DZ-BEA-desc',
315:                 'params' => [
316:                     [
317:                         'title' => 'payeeAccountNo',
```

### `waky6_miyouphp/php/config/anchorPayment.php:341`

- 命中检索词：pay_fee
- 证据行：'pay_fee' => 0.03,

```text
329:                         'title' => 'payeeName',
330:                         'kumai-showTitle' => 'DZ-BEA-payeeName',
331:                         'rules' => [
332:                             'required' => true,
333:                             'type' => [2,3,4],
334:                             'mode' => 'input',
335:                             'regex' => '^[A-Za-z ]{2,60}$',
336:                             'limit' => 60,
337:                         ],
338:                         'kumai-desc' => 'DZ-BEA-payeeName-desc',
339:                     ],
340:                 ],
341:                 'pay_fee' => 0.03,
342:                 'additional_pay_fee' => 0,
343:                 'transfer_reward' => 0.03,
344:                 'pay_rate' => 249.8477118,
345:                 'currency' => 'DZD',
346:             ],
347:         ],
348:         'EG' => [ // 埃及（Egypt）
349:             [
350:                 'supported_regions' => ['global'],  //global为全部开放
351:                 'name' => 'NBE',
352:                 'img_url' => 'uc/img/NBE.png',
353:                 'kumai-desc' => 'EG-NBE-desc',
```

### `waky6_miyouphp/php/config/anchorPayment.php:342`

- 命中检索词：pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
330:                         'kumai-showTitle' => 'DZ-BEA-payeeName',
331:                         'rules' => [
332:                             'required' => true,
333:                             'type' => [2,3,4],
334:                             'mode' => 'input',
335:                             'regex' => '^[A-Za-z ]{2,60}$',
336:                             'limit' => 60,
337:                         ],
338:                         'kumai-desc' => 'DZ-BEA-payeeName-desc',
339:                     ],
340:                 ],
341:                 'pay_fee' => 0.03,
342:                 'additional_pay_fee' => 0,
343:                 'transfer_reward' => 0.03,
344:                 'pay_rate' => 249.8477118,
345:                 'currency' => 'DZD',
346:             ],
347:         ],
348:         'EG' => [ // 埃及（Egypt）
349:             [
350:                 'supported_regions' => ['global'],  //global为全部开放
351:                 'name' => 'NBE',
352:                 'img_url' => 'uc/img/NBE.png',
353:                 'kumai-desc' => 'EG-NBE-desc',
354:                 'params' => [
```

### `waky6_miyouphp/php/config/anchorPayment.php:380`

- 命中检索词：pay_fee
- 证据行：'pay_fee' => 0.03,

```text
368:                         'title' => 'payeeName',
369:                         'kumai-showTitle' => 'EG-NBE-payeeName',
370:                         'rules' => [
371:                             'required' => true,
372:                             'type' => [2,3,4],
373:                             'mode' => 'input',
374:                             'regex' => '^[A-Za-z ]{2,60}$',
375:                             'limit' => 60,
376:                         ],
377:                         'kumai-desc' => 'EG-NBE-payeeName-desc',
378:                     ],
379:                 ],
380:                 'pay_fee' => 0.03,
381:                 'additional_pay_fee' => 0,
382:                 'transfer_reward' => 0.03,
383:                 'pay_rate' => 52.90408958,
384:                 'currency' => 'EGP',
385:             ],
386:             [
387:                 'supported_regions' => ['global'],  //global为全部开放
388:                 'name' => 'Banque Misr',
389:                 'img_url' => 'uc/img/Banque Misr.png',
390:                 'kumai-desc' => 'EG-Banque-Misr-desc',
391:                 'params' => [
392:                     [
```

### `waky6_miyouphp/php/config/anchorPayment.php:381`

- 命中检索词：pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
369:                         'kumai-showTitle' => 'EG-NBE-payeeName',
370:                         'rules' => [
371:                             'required' => true,
372:                             'type' => [2,3,4],
373:                             'mode' => 'input',
374:                             'regex' => '^[A-Za-z ]{2,60}$',
375:                             'limit' => 60,
376:                         ],
377:                         'kumai-desc' => 'EG-NBE-payeeName-desc',
378:                     ],
379:                 ],
380:                 'pay_fee' => 0.03,
381:                 'additional_pay_fee' => 0,
382:                 'transfer_reward' => 0.03,
383:                 'pay_rate' => 52.90408958,
384:                 'currency' => 'EGP',
385:             ],
386:             [
387:                 'supported_regions' => ['global'],  //global为全部开放
388:                 'name' => 'Banque Misr',
389:                 'img_url' => 'uc/img/Banque Misr.png',
390:                 'kumai-desc' => 'EG-Banque-Misr-desc',
391:                 'params' => [
392:                     [
393:                         'title' => 'payeeAccountNo',
```

### `waky6_miyouphp/php/config/anchorPayment.php:418`

- 命中检索词：additional_pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
406:                         'kumai-showTitle' => 'EG-Banque-Misr-payeeName',
407:                         'rules' => [
408:                             'required' => true,
409:                             'type' => [2,3,4],
410:                             'mode' => 'input',
411:                             'regex' => '^[A-Za-z ]{2,60}$',
412:                             'limit' => 60,
413:                         ],
414:                         'kumai-desc' => 'EG-Banque-Misr-payeeName-desc',
415:                     ],
416:                 ],
417:                 'pay_fee' => 0.03,
418:                 'additional_pay_fee' => 0,
419:                 'transfer_reward' => 0.03,
420:                 'pay_rate' => 52.90408958,
421:                 'currency' => 'EGP',
422:             ],
423:             [
424:                 'supported_regions' => ['global'],  //global为全部开放
425:                 'name' => 'CIB',
426:                 'img_url' => 'uc/img/CIB.png',
427:                 'kumai-desc' => 'EG-CIB-desc',
428:                 'params' => [
429:                     [
430:                         'title' => 'payeeAccountNo',
```

### `waky6_miyouphp/php/config/anchorPayment.php:455`

- 命中检索词：additional_pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
443:                         'kumai-showTitle' => 'EG-CIB-payeeName',
444:                         'rules' => [
445:                             'required' => true,
446:                             'type' => [2,3,4],
447:                             'mode' => 'input',
448:                             'regex' => '^[A-Za-z ]{2,60}$',
449:                             'limit' => 60,
450:                         ],
451:                         'kumai-desc' => 'EG-CIB-payeeName-desc',
452:                     ],
453:                 ],
454:                 'pay_fee' => 0.03,
455:                 'additional_pay_fee' => 0,
456:                 'transfer_reward' => 0.03,
457:                 'pay_rate' => 52.90408958,
458:                 'currency' => 'EGP',
459:             ],
460:             [
461:                 'supported_regions' => ['global'],  //global为全部开放
462:                 'name' => 'AlexBank',
463:                 'img_url' => 'uc/img/AlexBank.png',
464:                 'kumai-desc' => 'EG-AlexBank-desc',
465:                 'params' => [
466:                     [
467:                         'title' => 'payeeAccountNo',
```

### `waky6_miyouphp/php/config/anchorPayment.php:492`

- 命中检索词：additional_pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
480:                         'kumai-showTitle' => 'EG-AlexBank-payeeName',
481:                         'rules' => [
482:                             'required' => true,
483:                             'type' => [2,3,4],
484:                             'mode' => 'input',
485:                             'regex' => '^[A-Za-z ]{2,60}$',
486:                             'limit' => 60,
487:                         ],
488:                         'kumai-desc' => 'EG-AlexBank-payeeName-desc',
489:                     ],
490:                 ],
491:                 'pay_fee' => 0.03,
492:                 'additional_pay_fee' => 0,
493:                 'transfer_reward' => 0.03,
494:                 'pay_rate' => 52.90408958,
495:                 'currency' => 'EGP',
496:             ],
497:             [
498:                 'supported_regions' => ['global'],  //global为全部开放
499:                 'name' => 'QNB Alahli',
500:                 'img_url' => 'uc/img/QNB Alahli.png',
501:                 'kumai-desc' => 'EG-QNB-Alahli-desc',
502:                 'params' => [
503:                     [
504:                         'title' => 'payeeAccountNo',
```

### `waky6_miyouphp/php/config/anchorPayment.php:529`

- 命中检索词：additional_pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
517:                         'kumai-showTitle' => 'EG-QNB-Alahli-payeeName',
518:                         'rules' => [
519:                             'required' => true,
520:                             'type' => [2,3,4],
521:                             'mode' => 'input',
522:                             'regex' => '^[A-Za-z ]{2,60}$',
523:                             'limit' => 60,
524:                         ],
525:                         'kumai-desc' => 'EG-QNB-Alahli-payeeName-desc',
526:                     ],
527:                 ],
528:                 'pay_fee' => 0.03,
529:                 'additional_pay_fee' => 0,
530:                 'transfer_reward' => 0.03,
531:                 'pay_rate' => 52.90408958,
532:                 'currency' => 'EGP',
533:             ],
534:             [
535:                 'supported_regions' => ['global'],  //global为全部开放
536:                 'name' => 'Opay',
537:                 'img_url' => 'uc/img/Opay.png',
538:                 'kumai-desc' => 'EG-Opay-desc',
539:                 'params' => [
540:                     [
541:                         'title' => 'payeeAccountNo',
```

### `waky6_miyouphp/php/config/anchorPayment.php:566`

- 命中检索词：additional_pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
554:                         'kumai-showTitle' => 'EG-Opay-payeeName',
555:                         'rules' => [
556:                             'required' => true,
557:                             'type' => [2,3,4],
558:                             'mode' => 'input',
559:                             'regex' => '^[A-Za-z ]{2,60}$',
560:                             'limit' => 60,
561:                         ],
562:                         'kumai-desc' => 'EG-Opay-payeeName-desc',
563:                     ],
564:                 ],
565:                 'pay_fee' => 0.03,
566:                 'additional_pay_fee' => 0,
567:                 'transfer_reward' => 0.03,
568:                 'pay_rate' => 52.90408958,
569:                 'currency' => 'EGP',
570:             ],
571:             [
572:                 'supported_regions' => ['global'],  //global为全部开放
573:                 'name' => 'Vodafone Cash',
574:                 'img_url' => 'uc/img/Vodafone Cash.png',
575:                 'kumai-desc' => 'EG-Vodafone-Cash-desc',
576:                 'params' => [
577:                     [
578:                         'title' => 'payeeAccountNo',
```

### `waky6_miyouphp/php/config/anchorPayment.php:603`

- 命中检索词：additional_pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
591:                         'kumai-showTitle' => 'EG-Vodafone-Cash-payeeName',
592:                         'rules' => [
593:                             'required' => true,
594:                             'type' => [2,3,4],
595:                             'mode' => 'input',
596:                             'regex' => '^[A-Za-z ]{2,60}$',
597:                             'limit' => 60,
598:                         ],
599:                         'kumai-desc' => 'EG-Vodafone-Cash-payeeName-desc',
600:                     ],
601:                 ],
602:                 'pay_fee' => 0.03,
603:                 'additional_pay_fee' => 0,
604:                 'transfer_reward' => 0.03,
605:                 'pay_rate' => 52.90408958,
606:                 'currency' => 'EGP',
607:             ],
608:         ],
609:         'KW' => [ // 科威特（Kuwait）
610:             [
611:                 'supported_regions' => ['global'],  //global为全部开放
612:                 'name' => 'NBK',
613:                 'img_url' => 'uc/img/NBK.png',
614:                 'kumai-desc' => 'KW-NBK-desc',
615:                 'params' => [
```

### `waky6_miyouphp/php/config/anchorPayment.php:642`

- 命中检索词：additional_pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
630:                         'kumai-showTitle' => 'KW-NBK-payeeName',
631:                         'rules' => [
632:                             'required' => true,
633:                             'type' => [2,3,4],
634:                             'mode' => 'input',
635:                             'regex' => '^[A-Za-z ]{2,60}$',
636:                             'limit' => 60,
637:                         ],
638:                         'kumai-desc' => 'KW-NBK-payeeName-desc',
639:                     ],
640:                 ],
641:                 'pay_fee' => 0.03,
642:                 'additional_pay_fee' => 0,
643:                 'transfer_reward' => 0.03,
644:                 'pay_rate' => 0.361051607,
645:                 'currency' => 'KWD',
646:             ],
647:             [
648:                 'supported_regions' => ['global'],  //global为全部开放
649:                 'name' => 'KFH',
650:                 'img_url' => 'uc/img/KFH.png',
651:                 'kumai-desc' => 'KW-KFH-desc',
652:                 'params' => [
653:                     [
654:                         'title' => 'payeeAccountNo',
```

### `waky6_miyouphp/php/config/anchorPayment.php:679`

- 命中检索词：additional_pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
667:                         'kumai-showTitle' => 'KW-KFH-payeeName',
668:                         'rules' => [
669:                             'required' => true,
670:                             'type' => [2,3,4],
671:                             'mode' => 'input',
672:                             'regex' => '^[A-Za-z ]{2,60}$',
673:                             'limit' => 60,
674:                         ],
675:                         'kumai-desc' => 'KW-KFH-payeeName-desc',
676:                     ],
677:                 ],
678:                 'pay_fee' => 0.03,
679:                 'additional_pay_fee' => 0,
680:                 'transfer_reward' => 0.03,
681:                 'pay_rate' => 0.361051607,
682:                 'currency' => 'KWD',
683:             ],
684:             [
685:                 'supported_regions' => ['global'],  //global为全部开放
686:                 'name' => 'Boubyan Bank',
687:                 'img_url' => 'uc/img/Boubyan Bank.png',
688:                 'kumai-desc' => 'KW-Boubyan-Bank-desc',
689:                 'params' => [
690:                     [
691:                         'title' => 'payeeAccountNo',
```

### `waky6_miyouphp/php/config/anchorPayment.php:716`

- 命中检索词：additional_pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
704:                         'kumai-showTitle' => 'KW-Boubyan-Bank-payeeName',
705:                         'rules' => [
706:                             'required' => true,
707:                             'type' => [2,3,4],
708:                             'mode' => 'input',
709:                             'regex' => '^[A-Za-z ]{2,60}$',
710:                             'limit' => 60,
711:                         ],
712:                         'kumai-desc' => 'KW-Boubyan-Bank-payeeName-desc',
713:                     ],
714:                 ],
715:                 'pay_fee' => 0.03,
716:                 'additional_pay_fee' => 0,
717:                 'transfer_reward' => 0.03,
718:                 'pay_rate' => 0.361051607,
719:                 'currency' => 'KWD',
720:             ],
721:             [
722:                 'supported_regions' => ['global'],  //global为全部开放
723:                 'name' => 'Gulf Bank',
724:                 'img_url' => 'uc/img/Gulf Bank.png',
725:                 'kumai-desc' => 'KW-Gulf-Bank-desc',
726:                 'params' => [
727:                     [
728:                         'title' => 'payeeAccountNo',
```

### `waky6_miyouphp/php/config/anchorPayment.php:753`

- 命中检索词：additional_pay_fee
- 证据行：'additional_pay_fee' => 0,

```text
741:                         'kumai-showTitle' => 'KW-Gulf-Bank-payeeName',
742:                         'rules' => [
743:                             'required' => true,
744:                             'type' => [2,3,4],
745:                             'mode' => 'input',
746:                             'regex' => '^[A-Za-z ]{2,60}$',
747:                             'limit' => 60,
748:                         ],
749:                         'kumai-desc' => 'KW-Gulf-Bank-payeeName-desc',
750:                     ],
751:                 ],
752:                 'pay_fee' => 0.03,
753:                 'additional_pay_fee' => 0,
754:                 'transfer_reward' => 0.03,
755:                 'pay_rate' => 0.361051607,
756:                 'currency' => 'KWD',
757:             ],
758:         ],
759:         'LB' => [ // 黎巴嫩（Lebanon）
760:             [
761:                 'supported_regions' => ['global'],  //global为全部开放
762:                 'name' => 'Bank Audi',
763:                 'img_url' => 'uc/img/Bank Audi.png',
764:                 'kumai-desc' => 'LB-Bank-Audi-desc',
765:                 'params' => [
```

### `waky6_miyouphp/php/config/anchorPayment.php:1402`

- 命中检索词：'name' => 'USDT-TRC20'
- 证据行：'name' => 'USDT-TRC20',

```text
1390:                 'additional_pay_fee' => 0,
1391:                 'transfer_reward' => 0.03,
1392:                 'pay_rate' => 3.79104187,
1393:                 'currency' => 'SAR',
1394:             ],
1395:         ],
1396:     ],
1397:     'OFFICIAL' => [
1398:         'USDT' => [
1399:             [
1400:                 'supported_regions' => ['global'],
1401:                 'unsupported_countries' => [],
1402:                 'name' => 'USDT-TRC20',
1403:                 'kumai-desc' => '',
1404:                 'img_url' => 'uc/img/TRON.png',
1405:                 'params' => [
1406:                     [
1407:                         'title' => 'payeeAccountNo',
1408:                         'kumai-showTitle' => 'ETH-USDT-TRC20-payeeAccountNo',
1409:                         'rules' => [
1410:                             'required' => true,
1411:                             'type' => [1,2,3],
1412:                             'mode' => 'input',
1413:                             'regex' => '^T[a-zA-Z0-9]{33}$',
1414:                             'limit' => 34,
```

### `waky6_miyouphp/php/config/anchorPayment.php:1419`

- 命中检索词：'pay_fee' => 0.01
- 证据行：'pay_fee' => 0.01,

```text
1407:                         'title' => 'payeeAccountNo',
1408:                         'kumai-showTitle' => 'ETH-USDT-TRC20-payeeAccountNo',
1409:                         'rules' => [
1410:                             'required' => true,
1411:                             'type' => [1,2,3],
1412:                             'mode' => 'input',
1413:                             'regex' => '^T[a-zA-Z0-9]{33}$',
1414:                             'limit' => 34,
1415:                         ],
1416:                         'kumai-desc' => 'ETH-USDT-TRC20-payeeAccountNo-desc',
1417:                     ],
1418:                 ],
1419:                 'pay_fee' => 0.01,
1420:                 'additional_pay_fee' => 2.5,
1421:                 'transfer_reward' => 0,
1422:                 'pay_rate' => 1,
1423:                 'currency' => 'USDT',
1424:                 'platform' => 'Obolum',//三方打款平台
1425:             ],
1426:         ],
1427:         'Epay-USD' => [
1428:             [
1429:                 'supported_regions' => ['global'],  //global为全部开放
1430:                 'name' => 'Epay', //渠道名
1431:                 'kumai-desc' => 'GL-Epay-desc',
```

### `waky6_miyouphp/php/config/anchorPayment.php:1430`

- 命中检索词：'name' => 'Epay'
- 证据行：'name' => 'Epay', //渠道名

```text
1418:                 ],
1419:                 'pay_fee' => 0.01,
1420:                 'additional_pay_fee' => 2.5,
1421:                 'transfer_reward' => 0,
1422:                 'pay_rate' => 1,
1423:                 'currency' => 'USDT',
1424:                 'platform' => 'Obolum',//三方打款平台
1425:             ],
1426:         ],
1427:         'Epay-USD' => [
1428:             [
1429:                 'supported_regions' => ['global'],  //global为全部开放
1430:                 'name' => 'Epay', //渠道名
1431:                 'kumai-desc' => 'GL-Epay-desc',
1432:                 'img_url' => 'uc/img/epay.png',
1433:                 'params' => [ //参数
1434:                     [
1435:                         'title' => 'payeeAccountNo',
1436:                         'kumai-showTitle' => 'GL-Epay-payeeAccountNo',
1437:                         'rules' => [
1438:                             'required' => true,
1439:                             'type' => [1,2,3,5,7,10],
1440:                             'mode' => 'input', //input select check
1441:                             'regex' => '^[0-9a-zA-Z@.\-]{4,60}$',
1442:                             'limit' => 60,
```

### `waky6_miyouphp/php/config/anchorPayment.php:1448`

- 命中检索词：'pay_fee' => 0.01
- 证据行：'pay_fee' => 0.01,          // 1%

```text
1436:                         'kumai-showTitle' => 'GL-Epay-payeeAccountNo',
1437:                         'rules' => [
1438:                             'required' => true,
1439:                             'type' => [1,2,3,5,7,10],
1440:                             'mode' => 'input', //input select check
1441:                             'regex' => '^[0-9a-zA-Z@.\-]{4,60}$',
1442:                             'limit' => 60,
1443:                         ],
1444:                         'kumai-desc' => 'GL-Epay-payeeAccountNo-desc',
1445:                         'kumai-remark' => 'GL-Epay-payeeAccountNo-remark',
1446:                     ],
1447:                 ],
1448:                 'pay_fee' => 0.01,          // 1%
1449:                 'additional_pay_fee' => 0.5,// +1
1450:                 'transfer_reward' => 0,
1451:                 'pay_rate' => 1,            // 1 Epay-USD = 1 美元
1452:                 'currency' => 'USD',
1453:                 'platform' => 'Epay', //三方打款平台
1454:             ],
1455:         ],
1456:     ]
1457: ];
```

### `waky6_miyouphp/php/config/guildPayment.php:410`

- 命中检索词：'name' => 'USDT-TRC20'
- 证据行：'name' => 'USDT-TRC20', //渠道名

```text
398:                                 'showTitle' => 'CORPORATION',
399:                             ],
400:                         ],
401:                         'limit' => 100,
402:                     ],
403:                     'kumai-desc' => 'TR-BankTransfer-payeeType-desc',
404:                 ],
405:             ],
406:         ],
407:     ],
408:     'MA' => [ //国家码
409:         [
410:             'name' => 'USDT-TRC20', //渠道名
411:             'kumai-desc' => 'MA-USDT-TRC20-desc', //备注
412:             'params' => [ //参数
413:                 [
414:                     'title' => 'payeeAccountNo',
415:                     'kumai-showTitle' => 'MA-USDT-TRC20-payeeAccountNo',
416:                     'rules' => [
417:                         'required' => true,
418:                         'type' => [1, 2, 3],
419:                         'mode' => 'input', //input select check
420:                         'regex' => '^T[a-zA-Z\d]+$',
421:                         'limit' => 100,
422:                     ],
```

### `waky6_miyouphp/php/config/guildPayment.php:524`

- 命中检索词：'name' => 'USDT-TRC20'
- 证据行：'name' => 'USDT-TRC20', //渠道名

```text
512:                         'type' => [1, 2, 3, 5, 7, 9, 10],
513:                         'mode' => 'input', //input select check
514:                         'regex' => '^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$',
515:                         'limit' => 100,
516:                     ],
517:                     'kumai-desc' => 'MA-Payoneer-payeeAccountNo-desc',
518:                 ],
519:             ],
520:         ],
521:     ],
522:     'SA' => [ //国家码
523:         [
524:             'name' => 'USDT-TRC20', //渠道名
525:             'kumai-desc' => 'SA-USDT-TRC20-desc', //备注
526:             'params' => [ //参数
527:                 [
528:                     'title' => 'payeeAccountNo',
529:                     'kumai-showTitle' => 'SA-USDT-TRC20-payeeAccountNo',
530:                     'rules' => [
531:                         'required' => true,
532:                         'type' => [1, 2, 3],
533:                         'mode' => 'input', //input select check
534:                         'regex' => '^T[a-zA-Z\d]+$',
535:                         'limit' => 100,
536:                     ],
```

### `waky6_miyouphp/php/config/guildPayment.php:668`

- 命中检索词：'name' => 'USDT-TRC20'
- 证据行：'name' => 'USDT-TRC20', //渠道名

```text
656:                     'rules' => [
657:                         'required' => true,
658:                         'type' => [0],
659:                         'mode' => 'input', //input select check
660:                         'regex' => "^(?=.{3,50}$)[A-Za-z\x{0600}-\x{06FF} \-.,]+$",
661:                         'limit' => 50,
662:                     ],
663:                     'kumai-desc' => 'EG-Vodafone-payeeName-desc',
664:                 ],
665:             ],
666:         ],
667:         [
668:             'name' => 'USDT-TRC20', //渠道名
669:             'kumai-desc' => 'EG-USDT-TRC20-desc', //备注
670:             'params' => [ //参数
671:                 [
672:                     'title' => 'payeeAccountNo',
673:                     'kumai-showTitle' => 'EG-USDT-TRC20-payeeAccountNo',
674:                     'rules' => [
675:                         'required' => true,
676:                         'type' => [1, 2, 3],
677:                         'mode' => 'input', //input select check
678:                         'regex' => '^T[a-zA-Z\d]+$',
679:                         'limit' => 100,
680:                     ],
```

## 运行环境验证

- 本次未执行数据库、配置或日志事实验证。

## AI 补充

- 未启用或未生成 AI 诊断叙写

## 检索与关联线索

### 检索关键词

- 钻石提现
- DiamondControl
- withdrawOrder
- WithdrawalOrderCreateService
- withdrawal_limits
- min_diamonds
- multiple
- dollar_to_diamond
- buildOfficialSnapshot
- calcOfficialSettlement
- $fee = round
- pay_fee
- 'name' => 'USDT-TRC20'
- 'pay_fee' => 0.01
- 'name' => 'Epay'
- function getByTestMode
- 手续费
- additional_pay_fee

### 符号

- method DiamondControl::withdrawOrder waky6_miyouphp/php/action/DiamondControl.php:44
- method WithdrawalOrderCreateService::calcOfficialSettlement waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:401
- method WithdrawalOrderCreateService::buildOfficialSnapshot waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:557
- class DiamondControl waky6_miyouphp/php/action/DiamondControl.php:17
- method DiamondControl::confirmWithdrawalOrder waky6_miyouphp/php/action/DiamondControl.php:83
- method DiamondControl::getWithdrawalConfig waky6_miyouphp/php/action/DiamondControl.php:107
- method DiamondControl::getDiamondGearList waky6_miyouphp/php/action/DiamondControl.php:121
- method DiamondControl::getDiamondWithdrawRecord waky6_miyouphp/php/action/DiamondControl.php:156
- method DiamondControl::record waky6_miyouphp/php/action/DiamondControl.php:178
- method DiamondControl::recordTimeRange waky6_miyouphp/php/action/DiamondControl.php:233
- method DiamondControl::normalizeRecordDate waky6_miyouphp/php/action/DiamondControl.php:260
- method DiamondControl::exchange waky6_miyouphp/php/action/DiamondControl.php:282
- method DiamondControl::diamondWithdraw waky6_miyouphp/php/action/DiamondControl.php:307
- method DiamondControl::getDiamondPage waky6_miyouphp/php/action/DiamondControl.php:327
- method DiamondControl::getExchangeMerchants waky6_miyouphp/php/action/DiamondControl.php:340
- method DiamondControl::getAppointMerchants waky6_miyouphp/php/action/DiamondControl.php:360
- method DiamondControl::exchangeDiamondToCoins waky6_miyouphp/php/action/DiamondControl.php:378
- method DiamondControl::getDiamondIMNum waky6_miyouphp/php/action/DiamondControl.php:397
- method DiamondControl::getDiamondIMList waky6_miyouphp/php/action/DiamondControl.php:413
- method DiamondControl::getAgentDiamondIMNum waky6_miyouphp/php/action/DiamondControl.php:431
- method DiamondControl::getAgentDiamondIMList waky6_miyouphp/php/action/DiamondControl.php:447
- method DiamondControl::getWithdrawalOrder waky6_miyouphp/php/action/DiamondControl.php:466
- class WithdrawalOrderCreateService waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:29
- method WithdrawalOrderCreateService::create waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:63
- method WithdrawalOrderCreateService::assertOrderType waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:173
- method WithdrawalOrderCreateService::assertWithdrawMode waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:190
- method WithdrawalOrderCreateService::resolveAppointUid waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:219
- method WithdrawalOrderCreateService::assertAppointDealer waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:238
- method WithdrawalOrderCreateService::assertDiamonds waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:262
- method WithdrawalOrderCreateService::assertDailyLimitNotReached waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:309
- method WithdrawalOrderCreateService::assertOfficialDailyLimit waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:330
- method WithdrawalOrderCreateService::assertRemark waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:360
- method WithdrawalOrderCreateService::calcIncome waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:373
- method WithdrawalOrderCreateService::officialApiChannel waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:461
- method WithdrawalOrderCreateService::assertRegionOpen waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:475
- method WithdrawalOrderCreateService::assertDiamondBalance waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:493
- method WithdrawalOrderCreateService::assertNotFrequent waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:518
- method WithdrawalOrderCreateService::assertNotFrequentByRedis waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:532
- method WithdrawalOrderCreateService::freezeAndCreate waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:579
- method WithdrawalOrderCreateService::publishDiamondRiskFlow waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:713
- method WithdrawalOrderCreateService::diamondFlowTypeOf waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:751
- method WithdrawalOrderCreateService::publishOrderCreatedEvent waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:769
- method Main::getSuperDisplayMultipleModeMeta waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:42
- class MultipleItemsFoundException waky6_miyouphp/php/lib/Illuminate/Collections/MultipleItemsFoundException.php:7
- method SuperRebackGiftModel::normalizeDisplayMultipleMode waky6_miyouphp/php/model/SuperRebackGift/SuperRebackGiftModel.php:137
- method SuperRebackGiftModel::getDisplayMultipleMode waky6_miyouphp/php/model/SuperRebackGift/SuperRebackGiftModel.php:164
- method SuperRebackGiftModel::calcMultiple waky6_miyouphp/php/model/SuperRebackGift/SuperRebackGiftModel.php:183
- method SuperRebackGiftModel::calcMultipleByCurrentDisplayMode waky6_miyouphp/php/model/SuperRebackGift/SuperRebackGiftModel.php:221
- method MultiOpenGuard::checkMultipleAppValue waky6_miyouphp/php/services/Risk/MultiOpenGuard.php:115
- class RebateHighMultipleTask waky6_miyouphp/php/services/SuperRebateIncentive/DailyTask/Tasks/RebateHighMultipleTask.php:11
- method RebateHighMultipleTask::taskId waky6_miyouphp/php/services/SuperRebateIncentive/DailyTask/Tasks/RebateHighMultipleTask.php:23
- method RebateHighMultipleTask::__construct waky6_miyouphp/php/services/SuperRebateIncentive/DailyTask/Tasks/RebateHighMultipleTask.php:28
- method RebateHighMultipleTask::handleEvent waky6_miyouphp/php/services/SuperRebateIncentive/DailyTask/Tasks/RebateHighMultipleTask.php:44
- method RebateHighMultipleTask::getUserProgress waky6_miyouphp/php/services/SuperRebateIncentive/DailyTask/Tasks/RebateHighMultipleTask.php:111
- method RebateHighMultipleTask::loadMinTimes waky6_miyouphp/php/services/SuperRebateIncentive/DailyTask/Tasks/RebateHighMultipleTask.php:185
- method RebateHighMultipleTask::loadRules waky6_miyouphp/php/services/SuperRebateIncentive/DailyTask/Tasks/RebateHighMultipleTask.php:190
- method RebateHighMultipleTask::parseRules waky6_miyouphp/php/services/SuperRebateIncentive/DailyTask/Tasks/RebateHighMultipleTask.php:195
- method RebateHighMultipleTask::dailyHighMultiCntKey waky6_miyouphp/php/services/SuperRebateIncentive/DailyTask/Tasks/RebateHighMultipleTask.php:221
- method RebateHighMultipleTask::dailyHighMultiClaimKey waky6_miyouphp/php/services/SuperRebateIncentive/DailyTask/Tasks/RebateHighMultipleTask.php:226
- method SysEnv::getByTestMode waky6_miyouphp/php/conf/SysEnv.php:122
- method PHPExcel_Calculation_MathTrig::MROUND waky6_miyouadmin/admin/lib/PHPExcel/Calculation/MathTrig.php:721
- method PHPExcel_Chart_Renderer_jpgraph::renderPieChart waky6_miyouadmin/admin/lib/PHPExcel/Chart/Renderer/jpgraph.php:626
- method PHPExcel_Calculation_MathTrig::MROUND waky6_miyouadmin/agent_admin/lib/PHPExcel/Calculation/MathTrig.php:721
- method PHPExcel_Chart_Renderer_jpgraph::renderPieChart waky6_miyouadmin/agent_admin/lib/PHPExcel/Chart/Renderer/jpgraph.php:626
- method RocketPrizeConfModel::getLotteryPrizesByIds waky6_miyouphp/php/model/RocketPrizeConfModel.php:210
- method SuperRebackGiftLotteryServices::updateUserSureHitCounts waky6_miyouphp/php/services/SuperRebackGift/SuperRebackGiftLotteryServices.php:288
- function getByTestMode waky6_miyouphp/room/index.php:22
- class SysEnv waky6_miyouphp/php/conf/SysEnv.php:16
- method SysEnv::load waky6_miyouphp/php/conf/SysEnv.php:24
- method SysEnv::sameTzRegions waky6_miyouphp/php/conf/SysEnv.php:40
- method SysEnv::get waky6_miyouphp/php/conf/SysEnv.php:50
- method SysEnv::getSenUserZone waky6_miyouphp/php/conf/SysEnv.php:74
- method SysEnv::appAreaIsTW waky6_miyouphp/php/conf/SysEnv.php:83
- method SysEnv::appAreaIsVN waky6_miyouphp/php/conf/SysEnv.php:88
- method SysEnv::appAreaIsID waky6_miyouphp/php/conf/SysEnv.php:93
- method SysEnv::appAreaIsAR waky6_miyouphp/php/conf/SysEnv.php:98
- method SysEnv::appAreaIsTR waky6_miyouphp/php/conf/SysEnv.php:103
- method SysEnv::isTestEnv waky6_miyouphp/php/conf/SysEnv.php:108
- method SysEnv::env waky6_miyouphp/php/conf/SysEnv.php:115
- method SysEnv::checkEnv waky6_miyouphp/php/conf/SysEnv.php:129

### 配置线索

- superRebackGift.lotteryPoolLimit waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:38
- superRebackGift.displayMultipleMode waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:45
- superRebackGift.displayMultipleMode waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:887
- urlConf.jump_url_coins_merchant waky6_miyouphp/php/config/diamonds.php:99
- urlConf.jump_url_coins_merchant waky6_miyouphp/php/config/diamonds.php:170
- superRebackGift.desc_multiple waky6_miyouphp/php/action/giftControl.php:178
- superRebackGift.lotteryPoolLimit waky6_miyouphp/php/model/SuperRebackGift/SuperRebackGiftModel.php:106
- superRebackGift.lotteryPoolLimit waky6_miyouphp/php/model/SuperRebackGift/SuperRebackGiftModel.php:153
- superRebackGift.displayMultipleMode waky6_miyouphp/php/model/SuperRebackGift/SuperRebackGiftModel.php:166
- superRebackGift.singleSendOneLotteryWhiteList waky6_miyouphp/php/model/SuperRebackGift/SuperRebackGiftModel.php:239
- superRebackGift.lotteryPoolLimit waky6_miyouphp/php/model/SuperRebackGift/SuperRebackGiftModel.php:598
- superRebackGift.rebackWeekToDayTime waky6_miyouphp/php/model/SuperRebackGift/SuperRebackGiftModel.php:622
- superRebackGift.RebackNewVersionWhitelist waky6_miyouphp/php/model/SuperRebackGift/SuperRebackGiftModel.php:1018
- diamonds.withdrawal_limits.multiple waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:269
- diamonds.withdrawal_limits.dealer.min_diamonds waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:280
- diamonds.withdrawal_limits.official.min_diamonds waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:292
- diamonds.withdrawal_limits.official.max_diamonds waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:293
- diamonds.withdrawal_limits.official.daily_limit waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:336
- diamonds.readonly_ratios.dollar_to_diamond waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:375
- diamonds.official_api_channels waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:463
- superRebateIncentive.daily_tasks.rebate_high_multiple.min_times waky6_miyouphp/php/services/SuperRebateIncentive/DailyTask/Tasks/RebateHighMultipleTask.php:187
- superRebateIncentive.daily_tasks.rebate_high_multiple.rules waky6_miyouphp/php/services/SuperRebateIncentive/DailyTask/Tasks/RebateHighMultipleTask.php:192
- family.familyGiftGrowthMultiple waky6_miyouphp/php/services/family/HandleGrowthEvents.php:114
- superRebackGift.rebackNewVersionSureHitWhiteList waky6_miyouphp/php/services/SuperRebackGift/SuperRebackGiftLotteryServices.php:79
- superRebackGift.one_duration_time waky6_miyouphp/php/services/SuperRebackGift/SuperRebackGiftLotteryServices.php:607
- superRebackGift.im_reback_lottery_group waky6_miyouphp/php/services/SuperRebackGift/SuperRebackGiftLotteryServices.php:697
- superRebackGift.im_reback_lottery_group waky6_miyouphp/php/services/SuperRebackGift/SuperRebackGiftLotteryServices.php:743
- superRebackGift.animation_effects waky6_miyouphp/php/services/SuperRebackGift/SuperRebackGiftLotteryServices.php:756
- multiple_app.switch waky6_miyouphp/php/services/Risk/MultiOpenGuard.php:60
- multiple_app.device_white_list waky6_miyouphp/php/services/Risk/MultiOpenGuard.php:71
- diamonds.withdrawal_limits waky6_miyouphp/phpunit/Unit/Domain/Withdrawal/WithdrawalOrderSkeletonTest.php:330
- diamonds.readonly_ratios.dollar_to_diamond waky6_miyouphp/phpunit/Unit/Domain/Withdrawal/WithdrawalOrderSkeletonTest.php:360

### SQL 线索

- SELECT select * from waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:90
- SELECT select id from waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:256
- INSERT insert into t_reback_gifts_total_config set waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:257
- UPDATE update t_reback_gifts_total_config set waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:261
- INSERT insert into t_reback_gifts_total_config set waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:323
- INSERT insert into t_reback_gifts_total_config set waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:332
- SELECT select * from waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:334
- UPDATE update t_reback_gifts_total_config set waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:339
- UPDATE update t_reback_gifts_total_config set waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:369
- UPDATE update t_reback_gifts_total_config set waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:370
- SELECT select * from waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:454
- INSERT insert into t_super_reback_gifts_total_config set waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:574
- INSERT insert into t_super_reback_gifts_total_config set waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:584
- UPDATE update t_super_reback_gifts_total_config set waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:590
- SELECT select configs from waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:708
- UPDATE update t_super_reback_gifts_total_config set waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:721
- UPDATE update t_super_reback_gifts_total_config set waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:756
- UPDATE update t_super_reback_gifts_total_config set waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:757
- SELECT select configs from waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:877
- SELECT select id from waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:891
- INSERT insert into t_super_reback_gifts_total_config set waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:892
- UPDATE update t_super_reback_gifts_total_config set waky6_miyouadmin/admin/html/rebackGiftTotal/index.php:896
- SELECT select a.*,b.id as bid,b.level from waky6_miyouphp/php/model/RocketPrizeConfModel.php:77
- SELECT select a.*,b.id as bid,b.level from waky6_miyouphp/php/model/RocketPrizeConfModel.php:108
- SELECT select a.*,b.id as bid,b.level from waky6_miyouphp/php/model/RocketPrizeConfModel.php:134
- SELECT select a.*,b.id as bid,b.level from waky6_miyouphp/php/model/RocketPrizeConfModel.php:161
- SELECT select * from waky6_miyouphp/php/model/RocketPrizeConfModel.php:213
- UPDATE UPDATE t_rocket_prize_stock SET waky6_miyouphp/php/model/RocketPrizeConfModel.php:241
- SELECT select * from waky6_miyouphp/php/model/RocketPrizeConfModel.php:258
- SELECT select `id`,`type`,`sub_type`,`pid`,`configs` from waky6_miyouphp/php/model/SuperRebackGift/SuperRebackGiftModel.php:478
- SELECT select a.configs from waky6_miyouphp/php/model/SuperRebackGift/SuperRebackGiftModel.php:543
- SELECT select * from waky6_miyouphp/php/model/SuperRebackGift/SuperRebackGiftModel.php:1056
- SELECT select count(*) total from waky6_miyouphp/php/model/SuperRebackGift/SuperRebackGiftModel.php:1061
- SELECT select * from waky6_miyouphp/php/model/SuperRebackGift/SuperRebackGiftModel.php:1068

## 未验证项与警告

- 已按任务设置跳过 AI 诊断叙写，源码确定性分析不受影响
- 官方手续费的具体金额取决于用户所选渠道、结算币种和提现金额，未选择渠道时不存在唯一费用。
- 代码只能证明订单手续费快照和官方扣费；币商在线下另行约定的费用不在本链路内。
- 未成功完成数据库、配置或日志事实验证
- 诊断警告：已按任务设置跳过 AI 诊断叙写，源码确定性分析不受影响

## 建议下一步

- 沿确定性事实引用的文件和行号核对运行环境是否启用了对应熔断来源。
- 使用已定位文件和行号检查条件判断、异常分支与实际返回路径。
- 在目标环境只读核对配置键的实际值是否与代码预期一致。
- 使用只读 SQL 参数核对相关业务表记录、状态和关键时间字段。
- 沿报告中的调用链确认错误码来源和上层调用方。
- 补充数据库、配置或日志的按需只读验证计划以确认运行态事实。
- 先处理未验证项和警告，再判断候选原因是否成立。
