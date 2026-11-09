# 诊断证据：diag_be4e01819416473a8bb4dfa8542da67a

> ⚠️ 历史证据，已失效：本文部分调用链引用当前挂载仓库中不存在的 `WithdrawalOrderCreateService`。本文保留用于审计旧任务，不应直接作为当前源码事实；需要时请按当前索引重新诊断。

- 项目：waky6
- 问题：waky6项目 排查触发美元熔断时，业务有哪些具体限制？
- 环境：development
- 任务状态：COMPLETED
- 创建时间：2026-11-06T04:48:26.189057+00:00
- 结果更新时间：2026-11-06T04:49:04.142262+00:00

> 证据口径：业务限制来自源码确定性分析；数据库、配置和日志验证及 AI 叙写分别标注，未执行或失败的项目不能视为已确认。

## 诊断结论

已从源码确认美元熔断的业务边界：只支持消费方向，并按官方提现和美元兑代理币两类来源生效；官方提现会被下单拒绝并返回业务码 17；官方渠道和历史回填会隐藏；币商提现不受该熔断直接限制；美元兑换会直接中止并返回业务码 20018；没有单笔最大阈值；不处理资产恢复；读取异常时 fail-open。

## 置信度

中（0.72）：已由源码证据生成确定性业务事实；运行环境状态仍需按需验证。

## 业务限制与源码依据

### 1. 作用范围

美元熔断只支持消费方向；可选来源为官方提现（OFFICIAL_PAYMENT=1）和美元兑代理币（USD_EXCHANGE=2），不支持派发方向。

源码引用：`waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:36`、`waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:47`、`waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:48`

- `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:36`：return [FuseTypes::FUSE_TYPE_CONSUME];
- `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:47`：self::OFFICIAL_PAYMENT => '熔断官方提现',
- `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:48`：self::USD_EXCHANGE => '熔断美元兑代理币',

### 2. 单笔阈值

美元熔断没有单笔金额最大阈值判断，主要依赖运营后台配置的来源级熔断标识。

源码引用：`waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:77`、`waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:79`

- `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:77`：public function isTriggerMaxThreshold(int $uid, int $type, $num): array
- `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:79`：return [false, 0];

### 3. 官方提现

创建官方提现订单时直接拒绝，业务错误码为 17（OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED），用户侧提示为“官方提现暂不可用，请选择币商渠道或稍后重试”。

源码引用：`waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:87`、`waky6_miyouphp/php/domain/Withdrawal/Exception/WithdrawalException.php:71`、`waky6_miyouphp/php/lang/zh/exception.php:29`

- `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:87`：if ($orderType === self::ORDER_TYPE_OFFICIAL && UsdFuseService::isOfficialPaymentTriggered($uid)) {
- `waky6_miyouphp/php/domain/Withdrawal/Exception/WithdrawalException.php:71`：public const OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED = 17;
- `waky6_miyouphp/php/lang/zh/exception.php:29`：'WITHDRAWAL_OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED' => '官方提现暂不可用，请选择币商渠道或稍后重试',

### 4. 官方渠道

美元熔断期间官方渠道从提现目录中隐藏，官方历史渠道回填也会被清空；币商渠道继续参与目录组装。

源码引用：`waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:70`、`waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:71`、`waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:135`、`waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:136`

- `waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:70`：if ($isOfficial && $officialFuseTriggered) {
- `waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:71`：return [];
- `waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:135`：if ($isOfficialGroup && $officialFuseTriggered) {
- `waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:136`：continue;

### 5. 币商提现

币商提现不受美元熔断直接拦截；它继续受独立的“提现到代理”钻石熔断控制，命中后使用另一条异常码和提示。

源码引用：`waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:92`

- `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:92`：if ($orderType === self::ORDER_TYPE_DEALER && DiamondFuseService::isWithdrawToProxyTriggered($uid, $region)) {

### 6. 美元兑换

美元兑代理币会在余额扣减和兑换计算前中止，返回错误码 20018，用户侧提示为“美元兑换暂不可用，请稍后重试”。

源码引用：`waky6_miyouphp/php/action/CoinsMerchantControl.php:1731`、`waky6_miyouphp/php/action/CoinsMerchantControl.php:1733`、`waky6_miyouphp/php/conf/CoinsMerchantConf.php:33`、`waky6_miyouphp/php/lang/zh/coins_merchant.php:58`

- `waky6_miyouphp/php/action/CoinsMerchantControl.php:1731`：if (UsdFuseService::isUsdExchangeTriggered($uid)) {
- `waky6_miyouphp/php/action/CoinsMerchantControl.php:1733`：CoinsMerchantConf::getText('ERR_USD_EXCHANGE_FUSE_TRIGGERED', 'coins_merchant'),
- `waky6_miyouphp/php/conf/CoinsMerchantConf.php:33`：const ERR_USD_EXCHANGE_FUSE_TRIGGERED = 20018; // 美元兑代理币命中熔断
- `waky6_miyouphp/php/lang/zh/coins_merchant.php:58`：'ERR_USD_EXCHANGE_FUSE_TRIGGERED' => '美元兑换暂不可用，请稍后重试',

### 7. 隔离范围

熔断状态写入按熔断大类形成的 Redis Hash，业务读取按来源标识判断；同一来源的状态没有按 UID 分区，因此一个用户触发后会影响同来源的其他用户。

源码引用：`waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:62`、`waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:69`、`waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:102`

- `waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:62`：public function formatCacheKey(): string
- `waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:69`：return sprintf($this->hashFormatKey, $key, $this->dataStructure);
- `waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:102`：public function checkFuseFlag(array $fuseFlags): bool

### 8. 资产恢复

美元熔断只阻断业务入口，不生成可恢复资产记录；恢复服务为空，handelRecover 会明确返回失败。

源码引用：`waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:98`、`waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:115`

- `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:98`：public function getRestoreService()
- `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:115`：return $this->returnFail('usd business fuse does not support asset recovery');

### 9. 故障策略

风控读取采用 fail-open：uid 不大于 0 时直接放行；Redis 或读取异常记录日志后返回未熔断，避免风控故障扩大为提现和兑换整体不可用。

源码引用：`waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:151`、`waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:162`、`waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:163`

- `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:151`：if ($uid <= 0) {
- `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:162`：} catch (\Throwable $e) {
- `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:163`：\ExceptionGuard::log(\ExceptionGuard::logger(), $e, __METHOD__, ['uid' => $uid, 'source' => $source]);

## 源码证据上下文

### `waky6_miyouphp/php/lang/zh/coins_merchant.php:58`

- 命中检索词：ERR_USD_EXCHANGE_FUSE_TRIGGERED
- 证据行：'ERR_USD_EXCHANGE_FUSE_TRIGGERED' => '美元兑换暂不可用，请稍后重试',

```text
46:     'ERR_MERCHANT_STATUS_ABNORMAL' => '币商状态异常',
47:     'ERR_MERCHANT_NOT_IN_REGION' => '非本区币商',
48:     'ERR_MERCHANT_NOT_LEVEL1' => '非1级币商',
49:     'ERR_MERCHANT_BALANCE_NOT_ENOUGH' => '币商余额不足',
50:     'ERR_MERCHANT_EXCHANGE_DISABLED' => '该币商不可接钻石兑金币',
51:     'ERR_REGION_NOT_OPEN' => '该区未开放',
52:
53:     // 币商前台在线治理提醒（骨架 §13.3.2 第 11 点，文案与 waky7 coins_merchant.php:51-54 一致）
54:     'agent_offline_push_title' => '请保持APP在前台！',
55:     'agent_offline_push_content' => '长时间不在前台，将自动关闭接单！',
56:     'agent_auto_close_push_title' => '已自动关闭接单！',
57:     'agent_auto_close_push_content' => '长时间不在前台，已自动关闭接单，请手动打开接单开关！',
58:     'ERR_USD_EXCHANGE_FUSE_TRIGGERED' => '美元兑换暂不可用，请稍后重试',
59: ];
```

### `waky6_miyouphp/php/lang/zh/exception.php:29`

- 命中检索词：OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED
- 证据行：'WITHDRAWAL_OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED' => '官方提现暂不可用，请选择币商渠道或稍后重试',

```text
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

### `waky6_miyouphp/php/action/CoinsMerchantControl.php:35`

- 命中检索词：UsdFuseService
- 证据行：use Services\Monitor\Fuse\UsdFuseService;

```text
23: use HSingUrl;
24: use HttpRequest;
25: use Lang\LangObj;
26: use \Model\CoinsMerchantModel;
27: use Model\CoinsDealerLevelModel;
28: use Model\CoinsMerchantPropsModel;
29: use Model\RedisLockerModel;
30: use ServiceFactory;
31: use Services\PrettyUid\PrettyUidTrait;
32: use Services\Storage;
33: use Services\CoinsAgent\CoinsMerchantListService;
34: use Services\CoinsAgent\PaymentChannelSource;
35: use Services\Monitor\Fuse\UsdFuseService;
36: use Timezone\Auth;
37: use Timezone\Time;
38: use Timezone\TimeUser;
39: use Timezone\TimeUsers;
40: use Services\Anchor\AdvanceServices;
41: use Validation\Validator;
42:
43:
44: class CoinsMerchantControl
45: {
46:     use PrettyUidTrait;
47:
```

### `waky6_miyouphp/php/action/CoinsMerchantControl.php:1731`

- 命中检索词：UsdFuseService
- 证据行：if (UsdFuseService::isUsdExchangeTriggered($uid)) {

```text
1719:
1720:         $is_show_usd_balance = $config->getByRegion('coins_merchant.is_show_usd_balance');
1721:         //功能开关
1722:         if (!$is_show_usd_balance) {
1723:             \Application::stop(CoinsMerchantConf::getText('ParamsInvalid', 'default'), 403);
1724:         }
1725:
1726:         if (filter_var($money, FILTER_VALIDATE_INT, ['options' => ['min_range' => $min_money]]) === false) {
1727:             \Application::stop(CoinsMerchantConf::getText('ParamsInvalid', 'default'), 403);
1728:         }
1729:
1730:         // 美元兑代理币命中来源级熔断时停止兑换；风控 Redis 异常由服务层按放行处理。
1731:         if (UsdFuseService::isUsdExchangeTriggered($uid)) {
1732:             \Application::stop(
1733:                 CoinsMerchantConf::getText('ERR_USD_EXCHANGE_FUSE_TRIGGERED', 'coins_merchant'),
1734:                 CoinsMerchantConf::ERR_USD_EXCHANGE_FUSE_TRIGGERED
1735:             );
1736:         }
1737:
1738:         //美元余额
1739:         if ($money > $merchantInfo['usd_balance']) {
1740:             \Application::stop(CoinsMerchantConf::getText('ERR_NOT_ENOUGH_BALANCE', 'coins_merchant'), CoinsMerchantConf::ERR_NOT_ENOUGH_BALANCE);
1741:         }
1742:
1743:         $currency = $config->getByRegion('coins_merchant.currency');
```

### `waky6_miyouphp/php/action/CoinsMerchantControl.php:1733`

- 命中检索词：ERR_USD_EXCHANGE_FUSE_TRIGGERED
- 证据行：CoinsMerchantConf::getText('ERR_USD_EXCHANGE_FUSE_TRIGGERED', 'coins_merchant'),

```text
1721:         //功能开关
1722:         if (!$is_show_usd_balance) {
1723:             \Application::stop(CoinsMerchantConf::getText('ParamsInvalid', 'default'), 403);
1724:         }
1725:
1726:         if (filter_var($money, FILTER_VALIDATE_INT, ['options' => ['min_range' => $min_money]]) === false) {
1727:             \Application::stop(CoinsMerchantConf::getText('ParamsInvalid', 'default'), 403);
1728:         }
1729:
1730:         // 美元兑代理币命中来源级熔断时停止兑换；风控 Redis 异常由服务层按放行处理。
1731:         if (UsdFuseService::isUsdExchangeTriggered($uid)) {
1732:             \Application::stop(
1733:                 CoinsMerchantConf::getText('ERR_USD_EXCHANGE_FUSE_TRIGGERED', 'coins_merchant'),
1734:                 CoinsMerchantConf::ERR_USD_EXCHANGE_FUSE_TRIGGERED
1735:             );
1736:         }
1737:
1738:         //美元余额
1739:         if ($money > $merchantInfo['usd_balance']) {
1740:             \Application::stop(CoinsMerchantConf::getText('ERR_NOT_ENOUGH_BALANCE', 'coins_merchant'), CoinsMerchantConf::ERR_NOT_ENOUGH_BALANCE);
1741:         }
1742:
1743:         $currency = $config->getByRegion('coins_merchant.currency');
1744:         $addition_rate2 = $config->getByRegion('coins_merchant.addition_rate2');
1745:         $exchange_rate2 = $config->getByRegion('coins_merchant.exchange_rate2');
```

### `waky6_miyouphp/php/action/CoinsMerchantControl.php:1734`

- 命中检索词：ERR_USD_EXCHANGE_FUSE_TRIGGERED
- 证据行：CoinsMerchantConf::ERR_USD_EXCHANGE_FUSE_TRIGGERED

```text
1722:         if (!$is_show_usd_balance) {
1723:             \Application::stop(CoinsMerchantConf::getText('ParamsInvalid', 'default'), 403);
1724:         }
1725:
1726:         if (filter_var($money, FILTER_VALIDATE_INT, ['options' => ['min_range' => $min_money]]) === false) {
1727:             \Application::stop(CoinsMerchantConf::getText('ParamsInvalid', 'default'), 403);
1728:         }
1729:
1730:         // 美元兑代理币命中来源级熔断时停止兑换；风控 Redis 异常由服务层按放行处理。
1731:         if (UsdFuseService::isUsdExchangeTriggered($uid)) {
1732:             \Application::stop(
1733:                 CoinsMerchantConf::getText('ERR_USD_EXCHANGE_FUSE_TRIGGERED', 'coins_merchant'),
1734:                 CoinsMerchantConf::ERR_USD_EXCHANGE_FUSE_TRIGGERED
1735:             );
1736:         }
1737:
1738:         //美元余额
1739:         if ($money > $merchantInfo['usd_balance']) {
1740:             \Application::stop(CoinsMerchantConf::getText('ERR_NOT_ENOUGH_BALANCE', 'coins_merchant'), CoinsMerchantConf::ERR_NOT_ENOUGH_BALANCE);
1741:         }
1742:
1743:         $currency = $config->getByRegion('coins_merchant.currency');
1744:         $addition_rate2 = $config->getByRegion('coins_merchant.addition_rate2');
1745:         $exchange_rate2 = $config->getByRegion('coins_merchant.exchange_rate2');
1746:
```

### `waky6_miyouphp/php/conf/CoinsMerchantConf.php:33`

- 命中检索词：ERR_USD_EXCHANGE_FUSE_TRIGGERED
- 证据行：const ERR_USD_EXCHANGE_FUSE_TRIGGERED = 20018; // 美元兑代理币命中熔断

```text
21:     const ERR_SEND_PROP_FAIL = 20011;   // 派发失败
22:     const ERR_NOT_VERIFIED = 40005; // 未验证短信验证码
23:     const ERR_THE_USER_NOT_SUB_MERCHANT = 50005; // 不是二级币商
24:     const ERR_THE_USER_NOT_YOUR_SUB_MERCHANT = 50006; // 不是二级币商
25:
26:     // 钻石兑金币（向币商）——R-DG-07
27:     const ERR_MERCHANT_STATUS_ABNORMAL = 20012; // 币商状态异常
28:     const ERR_MERCHANT_NOT_IN_REGION = 20013; // 非本区币商
29:     const ERR_MERCHANT_NOT_LEVEL1 = 20014; // 非1级币商
30:     const ERR_MERCHANT_BALANCE_NOT_ENOUGH = 20015; // 币商余额不足
31:     const ERR_MERCHANT_EXCHANGE_DISABLED = 20016; // 不可接钻石兑金币
32:     const ERR_REGION_NOT_OPEN = 20017; // 该区未开放
33:     const ERR_USD_EXCHANGE_FUSE_TRIGGERED = 20018; // 美元兑代理币命中熔断
34:
35:     // 短信验证码类型
36:     const MOBILE_CODE_TYPE_LOGIN = 0;     // 登录验证
37:     const MOBILE_CODE_TYPE_CHANGE_PW = 1; // 修改交易密码
38:     const MOBILE_CODE_TYPE_LIST = [
39:         self::MOBILE_CODE_TYPE_LOGIN,
40:         self::MOBILE_CODE_TYPE_CHANGE_PW,
41:     ];
42:
43:     // redis keys
44:     const REDIS_KEY_LOGIN_VERIFIED = 'coins_merchant_login_verified:'; // [string] 是否已验证短信验证码 {prefix}{uid}:{device_id}
45:     const REDIS_KEY_VERIFY_CODE = 'coins_merchant_code:'; // [string] 短信验证码 {prefix}{mobile}:{type}
```

### `waky6_miyouphp/php/domain/Withdrawal/Exception/WithdrawalException.php:70`

- 命中检索词：美元熔断
- 证据行：/** 官方提现命中美元熔断，需引导用户改用币商渠道或稍后重试。 */

```text
58:     /** 订单状态已变（重复点击、并发操作或事件重投）。 */
59:     public const ORDER_STATUS_CHANGED = 13;
60:
61:     /** 已超出 2 小时确认窗口（R-WD-07）。 */
62:     public const CONFIRM_WINDOW_CLOSED = 14;
63:
64:     /** 这笔单不在当前币商名下，或该状态不允许这个动作。 */
65:     public const ORDER_NOT_ACTIONABLE = 15;
66:
67:     /** 官方提现结算净额不大于 0（提现金额过低，无法覆盖打款手续费）。 */
68:     public const OFFICIAL_SETTLEMENT_AMOUNT_INVALID = 16;
69:
70:     /** 官方提现命中美元熔断，需引导用户改用币商渠道或稍后重试。 */
71:     public const OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED = 17;
72:
73:     /** 提现到代理命中来源级熔断，暂停币商提现渠道。 */
74:     public const DEALER_WITHDRAWAL_FUSE_TRIGGERED = 18;
75:
76:     /** 领域异常文案所在的语言文件。 */
77:     private const LANG_FILE = 'exception';
78:
79:     /**
80:      * @var array
81:      */
82:     private static $langKeys = [
```

### `waky6_miyouphp/php/domain/Withdrawal/Exception/WithdrawalException.php:71`

- 命中检索词：OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED
- 证据行：public const OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED = 17;

```text
59:     public const ORDER_STATUS_CHANGED = 13;
60:
61:     /** 已超出 2 小时确认窗口（R-WD-07）。 */
62:     public const CONFIRM_WINDOW_CLOSED = 14;
63:
64:     /** 这笔单不在当前币商名下，或该状态不允许这个动作。 */
65:     public const ORDER_NOT_ACTIONABLE = 15;
66:
67:     /** 官方提现结算净额不大于 0（提现金额过低，无法覆盖打款手续费）。 */
68:     public const OFFICIAL_SETTLEMENT_AMOUNT_INVALID = 16;
69:
70:     /** 官方提现命中美元熔断，需引导用户改用币商渠道或稍后重试。 */
71:     public const OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED = 17;
72:
73:     /** 提现到代理命中来源级熔断，暂停币商提现渠道。 */
74:     public const DEALER_WITHDRAWAL_FUSE_TRIGGERED = 18;
75:
76:     /** 领域异常文案所在的语言文件。 */
77:     private const LANG_FILE = 'exception';
78:
79:     /**
80:      * @var array
81:      */
82:     private static $langKeys = [
83:         self::INVALID_PARAMS => 'WITHDRAWAL_INVALID_PARAMS',
```

### `waky6_miyouphp/php/domain/Withdrawal/Exception/WithdrawalException.php:99`

- 命中检索词：OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED
- 证据行：self::OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED => 'WITHDRAWAL_OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED',

```text
87:         self::CHANNEL_UNAVAILABLE => 'WITHDRAWAL_CHANNEL_UNAVAILABLE',
88:         self::MERCHANT_UNAVAILABLE => 'WITHDRAWAL_MERCHANT_UNAVAILABLE',
89:         self::REGION_NOT_OPEN => 'WITHDRAWAL_REGION_NOT_OPEN',
90:         self::FREQUENT_OPERATION => 'WITHDRAWAL_FREQUENT_OPERATION',
91:         self::OFFICIAL_NOT_IMPLEMENTED => 'WITHDRAWAL_OFFICIAL_NOT_IMPLEMENTED',
92:         self::WRITE_NOT_IMPLEMENTED => 'WITHDRAWAL_WRITE_NOT_IMPLEMENTED',
93:         self::ORDER_NOT_FOUND => 'WITHDRAWAL_ORDER_NOT_FOUND',
94:         self::NOT_MY_ORDER => 'WITHDRAWAL_NOT_MY_ORDER',
95:         self::ORDER_STATUS_CHANGED => 'WITHDRAWAL_ORDER_STATUS_CHANGED',
96:         self::CONFIRM_WINDOW_CLOSED => 'WITHDRAWAL_CONFIRM_WINDOW_CLOSED',
97:         self::ORDER_NOT_ACTIONABLE => 'WITHDRAWAL_ORDER_NOT_ACTIONABLE',
98:         self::OFFICIAL_SETTLEMENT_AMOUNT_INVALID => 'WITHDRAWAL_OFFICIAL_SETTLEMENT_AMOUNT_INVALID',
99:         self::OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED => 'WITHDRAWAL_OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED',
100:         self::DEALER_WITHDRAWAL_FUSE_TRIGGERED => 'WITHDRAWAL_DEALER_WITHDRAWAL_FUSE_TRIGGERED',
101:     ];
102:
103:     /**
104:      * @param int $code
105:      * @return string
106:      */
107:     protected static function getMessageByCode(int $code): string
108:     {
109:         return \Lang\Lang::get(self::$langKeys[$code] ?? 'ERROR', self::LANG_FILE);
110:     }
111:
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:15`

- 命中检索词：UsdFuseService
- 证据行：use Services\Monitor\Fuse\UsdFuseService;

```text
3: namespace Domain\Withdrawal\Service;
4:
5: use Conf\Config;
6: use Conf\MoneyFlowType;
7: use Domain\Withdrawal\Exception\WithdrawalException;
8: use Model\CoinsMerchantModel;
9: use Model\DiamondModel;
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
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:86`

- 命中检索词：美元熔断
- 证据行：// 3.1 官方打款命中美元熔断时直接拒绝；币商渠道不受影响，检查异常按放行处理。

```text
74:
75:             // 指定币商下单先验身份：必须是在用的 1 级币商、且与提现人同区（老区 to_uid 同口径）
76:             if ($mode === self::WITHDRAW_MODE_APPOINT) {
77:                 $this->assertAppointDealer($dealerUid, $region);
78:             }
79:         }
80:
81:         // 3. 钻石数与备注的入参合法性
82:         $diamonds = intval($input['diamonds'] ?? 0);
83:         self::assertDiamonds($diamonds, $orderType);
84:         self::assertRemark(strval($input['remark'] ?? ''));
85:
86:         // 3.1 官方打款命中美元熔断时直接拒绝；币商渠道不受影响，检查异常按放行处理。
87:         if ($orderType === self::ORDER_TYPE_OFFICIAL && UsdFuseService::isOfficialPaymentTriggered($uid)) {
88:             throw new WithdrawalException(WithdrawalException::OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED);
89:         }
90:
91:         // 3.2 币商提现命中“提现到代理”来源熔断时拒绝；官方渠道按独立的美元熔断判断。
92:         if ($orderType === self::ORDER_TYPE_DEALER && DiamondFuseService::isWithdrawToProxyTriggered($uid, $region)) {
93:             throw new WithdrawalException(WithdrawalException::DEALER_WITHDRAWAL_FUSE_TRIGGERED);
94:         }
95:
96:         // 4. 渠道定位 + 收款字段逐渠道校验（规则源 php/config/anchorPayment.php，字段平铺自 $payeeFields）
97:         //    分组由 order_type 决定，不另收 is_official，避免同一个真值两个键
98:         //    currency_type 的「国家_币种」拼接形态由 verify() 拆回目录键，落在 $channel['info']['country'] 上
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:87`

- 命中检索词：UsdFuseService
- 证据行：if ($orderType === self::ORDER_TYPE_OFFICIAL && UsdFuseService::isOfficialPaymentTriggered($uid)) {

```text
75:             // 指定币商下单先验身份：必须是在用的 1 级币商、且与提现人同区（老区 to_uid 同口径）
76:             if ($mode === self::WITHDRAW_MODE_APPOINT) {
77:                 $this->assertAppointDealer($dealerUid, $region);
78:             }
79:         }
80:
81:         // 3. 钻石数与备注的入参合法性
82:         $diamonds = intval($input['diamonds'] ?? 0);
83:         self::assertDiamonds($diamonds, $orderType);
84:         self::assertRemark(strval($input['remark'] ?? ''));
85:
86:         // 3.1 官方打款命中美元熔断时直接拒绝；币商渠道不受影响，检查异常按放行处理。
87:         if ($orderType === self::ORDER_TYPE_OFFICIAL && UsdFuseService::isOfficialPaymentTriggered($uid)) {
88:             throw new WithdrawalException(WithdrawalException::OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED);
89:         }
90:
91:         // 3.2 币商提现命中“提现到代理”来源熔断时拒绝；官方渠道按独立的美元熔断判断。
92:         if ($orderType === self::ORDER_TYPE_DEALER && DiamondFuseService::isWithdrawToProxyTriggered($uid, $region)) {
93:             throw new WithdrawalException(WithdrawalException::DEALER_WITHDRAWAL_FUSE_TRIGGERED);
94:         }
95:
96:         // 4. 渠道定位 + 收款字段逐渠道校验（规则源 php/config/anchorPayment.php，字段平铺自 $payeeFields）
97:         //    分组由 order_type 决定，不另收 is_official，避免同一个真值两个键
98:         //    currency_type 的「国家_币种」拼接形态由 verify() 拆回目录键，落在 $channel['info']['country'] 上
99:         $channel = (new WithdrawalChannelService())->verify(
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:88`

- 命中检索词：OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED
- 证据行：throw new WithdrawalException(WithdrawalException::OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED);

```text
76:             if ($mode === self::WITHDRAW_MODE_APPOINT) {
77:                 $this->assertAppointDealer($dealerUid, $region);
78:             }
79:         }
80:
81:         // 3. 钻石数与备注的入参合法性
82:         $diamonds = intval($input['diamonds'] ?? 0);
83:         self::assertDiamonds($diamonds, $orderType);
84:         self::assertRemark(strval($input['remark'] ?? ''));
85:
86:         // 3.1 官方打款命中美元熔断时直接拒绝；币商渠道不受影响，检查异常按放行处理。
87:         if ($orderType === self::ORDER_TYPE_OFFICIAL && UsdFuseService::isOfficialPaymentTriggered($uid)) {
88:             throw new WithdrawalException(WithdrawalException::OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED);
89:         }
90:
91:         // 3.2 币商提现命中“提现到代理”来源熔断时拒绝；官方渠道按独立的美元熔断判断。
92:         if ($orderType === self::ORDER_TYPE_DEALER && DiamondFuseService::isWithdrawToProxyTriggered($uid, $region)) {
93:             throw new WithdrawalException(WithdrawalException::DEALER_WITHDRAWAL_FUSE_TRIGGERED);
94:         }
95:
96:         // 4. 渠道定位 + 收款字段逐渠道校验（规则源 php/config/anchorPayment.php，字段平铺自 $payeeFields）
97:         //    分组由 order_type 决定，不另收 is_official，避免同一个真值两个键
98:         //    currency_type 的「国家_币种」拼接形态由 verify() 拆回目录键，落在 $channel['info']['country'] 上
99:         $channel = (new WithdrawalChannelService())->verify(
100:             strval($input['currency_type'] ?? ''),
```

### `waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:91`

- 命中检索词：美元熔断
- 证据行：// 3.2 币商提现命中“提现到代理”来源熔断时拒绝；官方渠道按独立的美元熔断判断。

```text
79:         }
80:
81:         // 3. 钻石数与备注的入参合法性
82:         $diamonds = intval($input['diamonds'] ?? 0);
83:         self::assertDiamonds($diamonds, $orderType);
84:         self::assertRemark(strval($input['remark'] ?? ''));
85:
86:         // 3.1 官方打款命中美元熔断时直接拒绝；币商渠道不受影响，检查异常按放行处理。
87:         if ($orderType === self::ORDER_TYPE_OFFICIAL && UsdFuseService::isOfficialPaymentTriggered($uid)) {
88:             throw new WithdrawalException(WithdrawalException::OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED);
89:         }
90:
91:         // 3.2 币商提现命中“提现到代理”来源熔断时拒绝；官方渠道按独立的美元熔断判断。
92:         if ($orderType === self::ORDER_TYPE_DEALER && DiamondFuseService::isWithdrawToProxyTriggered($uid, $region)) {
93:             throw new WithdrawalException(WithdrawalException::DEALER_WITHDRAWAL_FUSE_TRIGGERED);
94:         }
95:
96:         // 4. 渠道定位 + 收款字段逐渠道校验（规则源 php/config/anchorPayment.php，字段平铺自 $payeeFields）
97:         //    分组由 order_type 决定，不另收 is_official，避免同一个真值两个键
98:         //    currency_type 的「国家_币种」拼接形态由 verify() 拆回目录键，落在 $channel['info']['country'] 上
99:         $channel = (new WithdrawalChannelService())->verify(
100:             strval($input['currency_type'] ?? ''),
101:             strval($input['pay_channel'] ?? ''),
102:             $orderType === self::ORDER_TYPE_OFFICIAL
103:                 ? WithdrawalChannelService::GROUP_OFFICIAL
```

### `waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:12`

- 命中检索词：UsdFuseService
- 证据行：use Services\Monitor\Fuse\UsdFuseService;

```text
1: <?php
2:
3: namespace Services\Diamond;
4:
5: use Conf\Config;
6: use Domain\Withdrawal\Service\WithdrawalChannelService;
7: use Domain\Withdrawal\Service\WithdrawalOrderCreateService;
8: use Lang\Lang;
9: use Model\DiamondsWithdrawalOrderModel;
10: use Model\Orm\HsingPayOrm\TCoinsMerchantPayment;
11: use Services\Monitor\Fuse\DiamondFuseService;
12: use Services\Monitor\Fuse\UsdFuseService;
13: use Timezone\TimeUser;
14: use UserModel;
15:
16: /**
17:  * 提现页渠道目录（action=Action/Diamond.getWithdrawalConfig）
18:  * 需求：2026-09-21-waky6-withdrawal-redesign（R-WD-14/15 的表单数据源）
19:  *
20:  * 出参形态对齐 waky7 AdvanceServices::getWithdrawalSelfLastConfig() + getWithdrawalAllConfig()：
21:  * ['last' => 上次填写回填, 'configs' => ['currencys' => GLOBAL/OTHER 两桶, 'channels' => 按币种与国家_币种两级键]]
22:  *
23:  * 与 waky7 的三处刻意差异，都记在下面对应方法里：
24:  *   1. pay_rate 不叠加汇率表（w6 的 t_pay_currency_config 没有 exchange3 列，全仓也零引用）；
```

### `waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:25`

- 命中检索词：美元熔断
- 证据行：*   2. 不搬 Epay 一次性屏蔽窗（w7 那是硬编码绝对时间）；官方组按美元熔断，币商组按

```text
13: use Timezone\TimeUser;
14: use UserModel;
15:
16: /**
17:  * 提现页渠道目录（action=Action/Diamond.getWithdrawalConfig）
18:  * 需求：2026-09-21-waky6-withdrawal-redesign（R-WD-14/15 的表单数据源）
19:  *
20:  * 出参形态对齐 waky7 AdvanceServices::getWithdrawalSelfLastConfig() + getWithdrawalAllConfig()：
21:  * ['last' => 上次填写回填, 'configs' => ['currencys' => GLOBAL/OTHER 两桶, 'channels' => 按币种与国家_币种两级键]]
22:  *
23:  * 与 waky7 的三处刻意差异，都记在下面对应方法里：
24:  *   1. pay_rate 不叠加汇率表（w6 的 t_pay_currency_config 没有 exchange3 列，全仓也零引用）；
25:  *   2. 不搬 Epay 一次性屏蔽窗（w7 那是硬编码绝对时间）；官方组按美元熔断，币商组按
26:  *      “提现到代理”来源熔断过滤，再叠加三方网络码/币商接单判断，与下单侧同一判据；
27:  *   3. 渠道有没有币商接的比对按大小写不敏感做——库里存量是小写 bank、目录是 Bank，
28:  *      严格比对会把一个下单实际能用的渠道从目录里藏掉（脏数据本身另见 §17 待办）。
29:  */
30: class WithdrawalChannelConfigService
31: {
32:     /**
33:      * 端上提现页一次要拿的全部配置
34:      *
35:      * @return array{last:array,configs:array}
36:      */
37:     public function getConfigs(int $uid): array
```

### `waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:39`

- 命中检索词：UsdFuseService
- 证据行：$officialFuseTriggered = UsdFuseService::isOfficialPaymentTriggered($uid);

```text
27:  *   3. 渠道有没有币商接的比对按大小写不敏感做——库里存量是小写 bank、目录是 Bank，
28:  *      严格比对会把一个下单实际能用的渠道从目录里藏掉（脏数据本身另见 §17 待办）。
29:  */
30: class WithdrawalChannelConfigService
31: {
32:     /**
33:      * 端上提现页一次要拿的全部配置
34:      *
35:      * @return array{last:array,configs:array}
36:      */
37:     public function getConfigs(int $uid): array
38:     {
39:         $officialFuseTriggered = UsdFuseService::isOfficialPaymentTriggered($uid);
40:         [$region] = $this->userScope($uid);
41:         $dealerFuseTriggered = DiamondFuseService::isWithdrawToProxyTriggered($uid, $region);
42:
43:         return [
44:             'last' => $this->lastConfig($uid, $officialFuseTriggered, $dealerFuseTriggered),
45:             'configs' => $this->allConfig($uid, $officialFuseTriggered, $dealerFuseTriggered),
46:         ];
47:     }
48:
49:     /**
50:      * 上次提现填的渠道与收款字段，供表单回填
51:      *
```

### `waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:44`

- 命中检索词：officialFuseTriggered
- 证据行：'last' => $this->lastConfig($uid, $officialFuseTriggered, $dealerFuseTriggered),

```text
32:     /**
33:      * 端上提现页一次要拿的全部配置
34:      *
35:      * @return array{last:array,configs:array}
36:      */
37:     public function getConfigs(int $uid): array
38:     {
39:         $officialFuseTriggered = UsdFuseService::isOfficialPaymentTriggered($uid);
40:         [$region] = $this->userScope($uid);
41:         $dealerFuseTriggered = DiamondFuseService::isWithdrawToProxyTriggered($uid, $region);
42:
43:         return [
44:             'last' => $this->lastConfig($uid, $officialFuseTriggered, $dealerFuseTriggered),
45:             'configs' => $this->allConfig($uid, $officialFuseTriggered, $dealerFuseTriggered),
46:         ];
47:     }
48:
49:     /**
50:      * 上次提现填的渠道与收款字段，供表单回填
51:      *
52:      * 历史单现在不可用（渠道没了 / 没币商接 / 该区不覆盖）就返回空，让端上走全新选择
53:      */
54:     private function lastConfig(int $uid, bool $officialFuseTriggered, bool $dealerFuseTriggered): array
55:     {
56:         [$region, $userCountry] = $this->userScope($uid);
```

### `waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:45`

- 命中检索词：officialFuseTriggered
- 证据行：'configs' => $this->allConfig($uid, $officialFuseTriggered, $dealerFuseTriggered),

```text
33:      * 端上提现页一次要拿的全部配置
34:      *
35:      * @return array{last:array,configs:array}
36:      */
37:     public function getConfigs(int $uid): array
38:     {
39:         $officialFuseTriggered = UsdFuseService::isOfficialPaymentTriggered($uid);
40:         [$region] = $this->userScope($uid);
41:         $dealerFuseTriggered = DiamondFuseService::isWithdrawToProxyTriggered($uid, $region);
42:
43:         return [
44:             'last' => $this->lastConfig($uid, $officialFuseTriggered, $dealerFuseTriggered),
45:             'configs' => $this->allConfig($uid, $officialFuseTriggered, $dealerFuseTriggered),
46:         ];
47:     }
48:
49:     /**
50:      * 上次提现填的渠道与收款字段，供表单回填
51:      *
52:      * 历史单现在不可用（渠道没了 / 没币商接 / 该区不覆盖）就返回空，让端上走全新选择
53:      */
54:     private function lastConfig(int $uid, bool $officialFuseTriggered, bool $dealerFuseTriggered): array
55:     {
56:         [$region, $userCountry] = $this->userScope($uid);
57:
```

### `waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:54`

- 命中检索词：officialFuseTriggered
- 证据行：private function lastConfig(int $uid, bool $officialFuseTriggered, bool $dealerFuseTriggered): array

```text
42:
43:         return [
44:             'last' => $this->lastConfig($uid, $officialFuseTriggered, $dealerFuseTriggered),
45:             'configs' => $this->allConfig($uid, $officialFuseTriggered, $dealerFuseTriggered),
46:         ];
47:     }
48:
49:     /**
50:      * 上次提现填的渠道与收款字段，供表单回填
51:      *
52:      * 历史单现在不可用（渠道没了 / 没币商接 / 该区不覆盖）就返回空，让端上走全新选择
53:      */
54:     private function lastConfig(int $uid, bool $officialFuseTriggered, bool $dealerFuseTriggered): array
55:     {
56:         [$region, $userCountry] = $this->userScope($uid);
57:
58:         $last = (new DiamondsWithdrawalOrderModel())->getLastOrder($uid, ['payment', 'withdrawal']);
59:         if (empty($last['payment'])) {
60:             return [];
61:         }
62:
63:         $payment = (array)$last['payment'];
64:         $country = strval($payment['country'] ?? '');
65:         $method = strval($payment['method'] ?? '');
66:         $isOfficial = isset($payment['is_official']) && strval($payment['is_official']) === '1';
```

### `waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:69`

- 命中检索词：美元熔断
- 证据行：// 美元熔断只隐藏官方历史回填，币商历史渠道继续按原规则返回。

```text
57:
58:         $last = (new DiamondsWithdrawalOrderModel())->getLastOrder($uid, ['payment', 'withdrawal']);
59:         if (empty($last['payment'])) {
60:             return [];
61:         }
62:
63:         $payment = (array)$last['payment'];
64:         $country = strval($payment['country'] ?? '');
65:         $method = strval($payment['method'] ?? '');
66:         $isOfficial = isset($payment['is_official']) && strval($payment['is_official']) === '1';
67:         $group = $isOfficial ? WithdrawalChannelService::GROUP_OFFICIAL : WithdrawalChannelService::GROUP_MERCHANT;
68:
69:         // 美元熔断只隐藏官方历史回填，币商历史渠道继续按原规则返回。
70:         if ($isOfficial && $officialFuseTriggered) {
71:             return [];
72:         }
73:
74:         // “提现到代理”熔断只隐藏币商历史回填，不影响官方渠道。
75:         if (!$isOfficial && $dealerFuseTriggered) {
76:             return [];
77:         }
78:
79:         if ($group === WithdrawalChannelService::GROUP_MERCHANT && !$this->hasMerchantFor($country, $method)) {
80:             return [];
81:         }
```

### `waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:70`

- 命中检索词：officialFuseTriggered
- 证据行：if ($isOfficial && $officialFuseTriggered) {

```text
58:         $last = (new DiamondsWithdrawalOrderModel())->getLastOrder($uid, ['payment', 'withdrawal']);
59:         if (empty($last['payment'])) {
60:             return [];
61:         }
62:
63:         $payment = (array)$last['payment'];
64:         $country = strval($payment['country'] ?? '');
65:         $method = strval($payment['method'] ?? '');
66:         $isOfficial = isset($payment['is_official']) && strval($payment['is_official']) === '1';
67:         $group = $isOfficial ? WithdrawalChannelService::GROUP_OFFICIAL : WithdrawalChannelService::GROUP_MERCHANT;
68:
69:         // 美元熔断只隐藏官方历史回填，币商历史渠道继续按原规则返回。
70:         if ($isOfficial && $officialFuseTriggered) {
71:             return [];
72:         }
73:
74:         // “提现到代理”熔断只隐藏币商历史回填，不影响官方渠道。
75:         if (!$isOfficial && $dealerFuseTriggered) {
76:             return [];
77:         }
78:
79:         if ($group === WithdrawalChannelService::GROUP_MERCHANT && !$this->hasMerchantFor($country, $method)) {
80:             return [];
81:         }
82:
```

### `waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:121`

- 命中检索词：officialFuseTriggered
- 证据行：private function allConfig(int $uid, bool $officialFuseTriggered, bool $dealerFuseTriggered): array

```text
109:                 'nationalFlag' => $this->countryFlag($country),
110:                 'currency' => strval($last['withdrawal']['currency'] ?? $channel['currency'] ?? ''),
111:             ],
112:             'channel' => $channel,
113:         ];
114:     }
115:
116:     /**
117:      * 全部可用渠道：currencys 给币种选择器，channels 给渠道选择器
118:      *
119:      * @return array
120:      */
121:     private function allConfig(int $uid, bool $officialFuseTriggered, bool $dealerFuseTriggered): array
122:     {
123:         [$region, $userCountry] = $this->userScope($uid);
124:
125:         $all = (array)Config::all('anchorPayment');
126:         $channelService = new WithdrawalChannelService();
127:
128:         $res = [];
129:         $addedCountries = [];
130:
131:         foreach ($all as $group => $countries) {
132:             $isOfficialGroup = $group === WithdrawalChannelService::GROUP_OFFICIAL;
133:
```

### `waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:134`

- 命中检索词：美元熔断
- 证据行：// 美元熔断只阻断官方打款，币商渠道必须继续参与目录组装。

```text
122:     {
123:         [$region, $userCountry] = $this->userScope($uid);
124:
125:         $all = (array)Config::all('anchorPayment');
126:         $channelService = new WithdrawalChannelService();
127:
128:         $res = [];
129:         $addedCountries = [];
130:
131:         foreach ($all as $group => $countries) {
132:             $isOfficialGroup = $group === WithdrawalChannelService::GROUP_OFFICIAL;
133:
134:             // 美元熔断只阻断官方打款，币商渠道必须继续参与目录组装。
135:             if ($isOfficialGroup && $officialFuseTriggered) {
136:                 continue;
137:             }
138:
139:             // “提现到代理”熔断只阻断币商目录，官方渠道继续按独立熔断状态展示。
140:             if (!$isOfficialGroup && $dealerFuseTriggered) {
141:                 continue;
142:             }
143:
144:             foreach ((array)$countries as $country => $channels) {
145:                 foreach ((array)$channels as $channel) {
146:                     $method = strval($channel['name'] ?? '');
```

### `waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:135`

- 命中检索词：officialFuseTriggered
- 证据行：if ($isOfficialGroup && $officialFuseTriggered) {

```text
123:         [$region, $userCountry] = $this->userScope($uid);
124:
125:         $all = (array)Config::all('anchorPayment');
126:         $channelService = new WithdrawalChannelService();
127:
128:         $res = [];
129:         $addedCountries = [];
130:
131:         foreach ($all as $group => $countries) {
132:             $isOfficialGroup = $group === WithdrawalChannelService::GROUP_OFFICIAL;
133:
134:             // 美元熔断只阻断官方打款，币商渠道必须继续参与目录组装。
135:             if ($isOfficialGroup && $officialFuseTriggered) {
136:                 continue;
137:             }
138:
139:             // “提现到代理”熔断只阻断币商目录，官方渠道继续按独立熔断状态展示。
140:             if (!$isOfficialGroup && $dealerFuseTriggered) {
141:                 continue;
142:             }
143:
144:             foreach ((array)$countries as $country => $channels) {
145:                 foreach ((array)$channels as $channel) {
146:                     $method = strval($channel['name'] ?? '');
147:
```

### `waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:62`

- 命中检索词：formatCacheKey
- 证据行：public function formatCacheKey(): string

```text
50:
51:         if (true === empty($this->getFuseTypes())) {
52:             throw new Exception("fuse types is empty.");
53:         }
54:
55:         $this->logger = ServiceFactory::getLogger();
56:     }
57:
58:     /**
59:      * 格式化缓存key
60:      * @return string
61:      */
62:     public function formatCacheKey(): string
63:     {
64:         $key = $this->getKey();
65:         if (true === empty($this->keyPrefix)) {
66:             $key = $this->keyPrefix . "_" . $this->getKey();
67:         }
68:
69:         return sprintf($this->hashFormatKey, $key, $this->dataStructure);
70:     }
71:
72:     /**
73:      * 设置熔断标识
74:      * @param array $fuseFlags
```

### `waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:84`

- 命中检索词：formatCacheKey
- 证据行：$cacheKey = $this->formatCacheKey();

```text
72:     /**
73:      * 设置熔断标识
74:      * @param array $fuseFlags
75:      * @return bool
76:      * @throws RedisException
77:      */
78:     public function setFuseFlag(array $fuseFlags): bool
79:     {
80:         if (true === empty($fuseFlags)) {
81:             return false;
82:         }
83:
84:         $cacheKey = $this->formatCacheKey();
85:
86:         $this->redis->pipeline();
87:         foreach ($fuseFlags as $flag) {
88:             $this->redis->hset($cacheKey, $flag, time());
89:         }
90:
91:         $this->redis->exec();
92:
93:         return true;
94:     }
95:
96:     /**
```

### `waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:104`

- 命中检索词：formatCacheKey
- 证据行：$cacheKey = $this->formatCacheKey();

```text
92:
93:         return true;
94:     }
95:
96:     /**
97:      * 检查熔断标识
98:      * @param array $fuseFlags
99:      * @return bool
100:      * @throws RedisException
101:      */
102:     public function checkFuseFlag(array $fuseFlags): bool
103:     {
104:         $cacheKey = $this->formatCacheKey();
105:
106:         $all = $this->redis->hgetall($cacheKey);
107:         if (true === empty($all)) {
108:             return false;
109:         }
110:
111:         $allFuseFlags = array_keys($all);
112:         if (false === empty(array_intersect($allFuseFlags, $fuseFlags))) {
113:             return true;
114:         }
115:
116:         return false;
```

### `waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:126`

- 命中检索词：formatCacheKey
- 证据行：$cacheKey = $this->formatCacheKey();

```text
114:         }
115:
116:         return false;
117:     }
118:
119:     /**
120:      * 获取某个大类所有熔断标识
121:      * @return array
122:      * @throws RedisException
123:      */
124:     public function getAllFuseFlags(): array
125:     {
126:         $cacheKey = $this->formatCacheKey();
127:
128:         if (0 == $this->redis->exists($cacheKey)) {
129:             return array();
130:         }
131:
132:         return $this->redis->hgetall($cacheKey);
133:     }
134:
135:
136:     /**
137:      * 删除熔断标识
138:      * @param array $fuseFlags
```

### `waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:148`

- 命中检索词：formatCacheKey
- 证据行：$cacheKey = $this->formatCacheKey();

```text
136:     /**
137:      * 删除熔断标识
138:      * @param array $fuseFlags
139:      * @return bool
140:      * @throws RedisException
141:      */
142:     public function delFuseFlag(array $fuseFlags): bool
143:     {
144:         if (true === empty($fuseFlags)) {
145:             return false;
146:         }
147:
148:         $cacheKey = $this->formatCacheKey();
149:
150:         if (0 == $this->redis->exists($cacheKey)) {
151:             return true;
152:         }
153:
154:         // redis hash 没值会自动删除，因此这里不需要特别删除这个key
155:         $this->redis->pipeline();
156:         foreach ($fuseFlags as $flag) {
157:             $this->redis->hdel($cacheKey, $flag);
158:         }
159:         $this->redis->exec();
160:
```

### `waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:171`

- 命中检索词：formatCacheKey
- 证据行：$cacheKey = $this->formatCacheKey();

```text
159:         $this->redis->exec();
160:
161:         return true;
162:     }
163:
164:     /**
165:      * 检查缓存key是否存在
166:      * @return bool
167:      * @throws RedisException
168:      */
169:     protected function checkCacheKeyIsExist(): bool
170:     {
171:         $cacheKey = $this->formatCacheKey();
172:         if (0 == $this->redis->exists($cacheKey)) {
173:             return false;
174:         }
175:         return true;
176:     }
177:
178:     /**
179:      * 获取熔断源
180:      * @param int $from
181:      * @return string
182:      */
183:     public function fromToString(int $from): string
```

### `waky6_miyouphp/php/services/Monitor/Fuse/CoinFuseService.php:141`

- 命中检索词：isTriggerMaxThreshold
- 证据行：public function isTriggerMaxThreshold(int $uid, int $type, $num): array

```text
129:         }
130:
131:         return [$masterSwitch, $from];
132:     }
133:
134:     /**
135:      * 判断是否触发最大阈值
136:      * @param int $uid
137:      * @param int $type
138:      * @param $num
139:      * @return array
140:      */
141:     public function isTriggerMaxThreshold(int $uid, int $type, $num): array
142:     {
143:         switch ($type) {
144:             case FuseTypes::FUSE_TYPE_ADD:
145:                 return [$num >= FuseConf::getMaxAddCoins(), FuseConf::getMaxAddCoins()];
146:             case FuseTypes::FUSE_TYPE_CONSUME:
147:                 $res = $num >= FuseConf::getMaxConsumeCoins();
148:                 if (true === $res) {
149:                     $balance = (new \UserModel(null))->getWalletInfo($uid)['balance'] ?? 0;
150:                     $res = $balance >= $num;
151:                 }
152:                 return [$res, FuseConf::getMaxConsumeCoins()];
153:             default:
```

### `waky6_miyouphp/php/services/Monitor/Fuse/CoinFuseService.php:162`

- 命中检索词：getRestoreService
- 证据行：public function getRestoreService()

```text
150:                     $res = $balance >= $num;
151:                 }
152:                 return [$res, FuseConf::getMaxConsumeCoins()];
153:             default:
154:                 return [false, 0];
155:         }
156:     }
157:
158:     /**
159:      * 获取恢复服务
160:      * @return mixed
161:      */
162:     public function getRestoreService()
163:     {
164:         if ($this->restoreService === null) {
165:             $this->restoreService = new UserModel(ServiceFactory::getMainDB());
166:         }
167:         return $this->restoreService;
168:     }
169:
170:     /**
171:      * 恢复熔断数据
172:      * @param int $uid
173:      * @param int $bigType
174:      * @param int $type
```

### `waky6_miyouphp/php/services/Monitor/Fuse/CoinFuseService.php:203`

- 命中检索词：getRestoreService
- 证据行：$res = $this->getRestoreService()->addWallet(...$content);

```text
191:         }
192:
193:         end($content);
194:         $lastKey = key($content);
195:         $extra = &$content[$lastKey]; // 使用引用
196:         if (true === is_array($extra) && $monitorIgnoreStat > 0) {
197:             // 不需要重新赋值，因为使用的是引用
198:             $extra['monitorIgnoreStat'] = $monitorIgnoreStat;
199:         }
200:         $this->logger->logInfo('addWallet:' . json_encode($content));
201:
202:         // 恢复
203:         $res = $this->getRestoreService()->addWallet(...$content);
204:         if (0 > $res) {
205:             return $this->returnFail('add wallet fail. ' . json_encode($content));
206:         }
207:         return $this->returnSuccess();
208:     }
209:
210: }
```

### `waky6_miyouphp/php/services/Monitor/Fuse/DiamondFuseService.php:123`

- 命中检索词：isTriggerMaxThreshold
- 证据行：public function isTriggerMaxThreshold(int $uid, int $type, $num): array

```text
111:         }
112:
113:         return array_values(array_unique($flags));
114:     }
115:
116:     /**
117:      * 判断是否触发最大阈值
118:      * @param int $uid
119:      * @param int $type
120:      * @param $num
121:      * @return array
122:      */
123:     public function isTriggerMaxThreshold(int $uid, int $type, $num): array
124:     {
125:         switch ($type) {
126:             case FuseTypes::FUSE_TYPE_ADD:
127:                 return [$num >= FuseConf::getMaxAddDiamond(), FuseConf::getMaxAddDiamond()];
128:             case FuseTypes::FUSE_TYPE_CONSUME:
129:
130:                 $res = $num >= FuseConf::getMaxConsumeDiamond();
131:                 if (true === $res) {
132:                     $balance = (new DiamondModel())->getUserDiamonds($uid)['diamonds'];
133:                     $res = $balance >= $num;
134:                 }
135:                 return [$res, FuseConf::getMaxConsumeDiamond()];
```

### `waky6_miyouphp/php/services/Monitor/Fuse/DiamondFuseService.php:146`

- 命中检索词：getRestoreService
- 证据行：public function getRestoreService()

```text
134:                 }
135:                 return [$res, FuseConf::getMaxConsumeDiamond()];
136:
137:             default:
138:                 return [false, 0];
139:         }
140:     }
141:
142:     /**
143:      * 获取恢复服务
144:      * @return mixed
145:      */
146:     public function getRestoreService()
147:     {
148:         if ($this->restoreService === null) {
149:             $this->restoreService = new DiamondModel();
150:         }
151:         return $this->restoreService;
152:     }
153:
154:     /**
155:      * 恢复熔断数据
156:      * @param int $uid
157:      * @param int $bigType
158:      * @param int $type
```

### `waky6_miyouphp/php/services/Monitor/Fuse/DiamondFuseService.php:186`

- 命中检索词：getRestoreService
- 证据行：$res = $this->getRestoreService()->incrDiamond(...$content);

```text
174:             return $this->returnFuse('cache key is exist. stop 2');
175:         }
176:
177:         end($content);
178:         $lastKey = key($content);
179:         $extra = &$content[$lastKey]; // 使用引用
180:         if (true === is_array($extra) && $monitorIgnoreStat > 0) {
181:             // 不需要重新赋值，因为使用的是引用
182:             $extra['monitorIgnoreStat'] = $monitorIgnoreStat;
183:         }
184:         $this->logger->logInfo('incrDiamond:' . json_encode($content));
185:
186:         $res = $this->getRestoreService()->incrDiamond(...$content);
187:
188:         if (false === $res) {
189:             return $this->returnFail('incrDiamond fail. ' . json_encode($content));
190:         }
191:         return $this->returnSuccess();
192:     }
193:
194:     /**
195:      * 检查提现到代理是否命中来源级熔断。
196:      *
197:      * 同时兼容既有全局/区域钻石消耗熔断；Redis 异常按放行处理并记录日志。
198:      *
```

### `waky6_miyouphp/php/services/Monitor/Fuse/FuseFactory.php:19`

- 命中检索词：BIG_TYPE_USD
- 证据行：case FuseTypes::BIG_TYPE_USD:

```text
7: {
8:     public static function getService(int $bigType): ?BaseFuseService
9:     {
10:         switch ($bigType) {
11:             case FuseTypes::BIG_TYPE_COINS:
12:                 return new CoinFuseService();
13:             case FuseTypes::BIG_TYPE_DIAMOND:
14:                 return new DiamondFuseService();
15:             case FuseTypes::BIG_TYPE_PACK_GIFT:
16:                 return new PackGiftFuseService();
17:             case FuseTypes::BIG_TYPE_STRANGE_FATE:
18:                 return new StrangeFateFuseService();
19:             case FuseTypes::BIG_TYPE_USD:
20:                 return new UsdFuseService();
21:             default:
22:                 return null;
23:         }
24:     }
25: }
```

### `waky6_miyouphp/php/services/Monitor/Fuse/FuseFactory.php:20`

- 命中检索词：UsdFuseService
- 证据行：return new UsdFuseService();

```text
8:     public static function getService(int $bigType): ?BaseFuseService
9:     {
10:         switch ($bigType) {
11:             case FuseTypes::BIG_TYPE_COINS:
12:                 return new CoinFuseService();
13:             case FuseTypes::BIG_TYPE_DIAMOND:
14:                 return new DiamondFuseService();
15:             case FuseTypes::BIG_TYPE_PACK_GIFT:
16:                 return new PackGiftFuseService();
17:             case FuseTypes::BIG_TYPE_STRANGE_FATE:
18:                 return new StrangeFateFuseService();
19:             case FuseTypes::BIG_TYPE_USD:
20:                 return new UsdFuseService();
21:             default:
22:                 return null;
23:         }
24:     }
25: }
```

### `waky6_miyouphp/php/services/Monitor/Fuse/FuseInterface.php:36`

- 命中检索词：isTriggerMaxThreshold
- 证据行：public function isTriggerMaxThreshold(int $uid, int $type, $num): array;

```text
24:      * @param array $extra
25:      * @return array
26:      */
27:     public function getFuseFlag(int $type, int $from, array $extra = array()): array;
28:
29:     /**
30:      * 判断是否触发最大阈值
31:      * @param int $uid
32:      * @param int $type
33:      * @param $num
34:      * @return array
35:      */
36:     public function isTriggerMaxThreshold(int $uid, int $type, $num): array;
37:
38:     /**
39:      * 获取熔断源
40:      * @return array
41:      */
42:     public static function getSourceMap(): array;
43:
44:     /**
45:      * 获取熔断源
46:      * @param int $from
47:      * @return string
48:      */
```

### `waky6_miyouphp/php/services/Monitor/Fuse/FuseInterface.php:55`

- 命中检索词：getRestoreService
- 证据行：public function getRestoreService();

```text
43:
44:     /**
45:      * 获取熔断源
46:      * @param int $from
47:      * @return string
48:      */
49:     public function fromToString(int $from): string;
50:
51:     /**
52:      * 获取恢复服务
53:      * @return mixed
54:      */
55:     public function getRestoreService();
56:
57:     /**
58:      * 恢复熔断数据
59:      * @param int $uid
60:      * @param int $bigType
61:      * @param int $type
62:      * @param int $monitorIgnoreStat
63:      * @param array $content
64:      * @return array
65:      */
66:     public  function handelRecover(int $uid, int $bigType, int $type, int $monitorIgnoreStat, array $content): array;
67:
```

### `waky6_miyouphp/php/services/Monitor/Fuse/FuseService.php:102`

- 命中检索词：isTriggerMaxThreshold
- 证据行：list($code, $maxThreshold) = $service->isTriggerMaxThreshold($uid, $type, $num);

```text
90:      * @throws RedisException
91:      */
92:     public function checkFuseFlag(int $uid, int $bigType, int $type, int $from, $num = 0, array $extra = array()): bool
93:     {
94:         $service = FuseFactory::getService($bigType);
95:         if (!$service instanceof BaseFuseService) {
96:             // 因为可能会涉及到接口请求，故选择使用Crit级别，避免影响正常业务，直接返回true
97:             $this->logger->logCrit("get service error. bigType:" . $bigType);
98:             return false;
99:         }
100:
101:         // 判断是否触发最大阈值
102:         list($code, $maxThreshold) = $service->isTriggerMaxThreshold($uid, $type, $num);
103:         if (true === $code) {
104:             $this->logger->logWarn("trigger max threshold. bigType:$bigType. type:$type. from:$from. num:$num. extra:" . json_encode($extra));
105:             $fromName = $service->fromToString($from);
106:             $fromName .= "($from)";
107:             (new WebhookNotifyService())->asyncSendMaxThresholdWebhook(
108:                 FuseTypes::getBigTypeName($bigType), FuseTypes::getFuseTypeName($type), $fromName, $uid, $num, $maxThreshold, FuseConf::getWebhook()
109:             );
110:             return true;
111:         }
112:
113: //        $whitelist = FuseConf::FUSE_USER_WHITELIST;
114: //        if (false === empty($whitelist) && false === in_array($uid, $whitelist)) {
```

### `waky6_miyouphp/php/services/Monitor/Fuse/FuseTypes.php:17`

- 命中检索词：UsdFuseService
- 证据行：const BIG_TYPE_USD = 6;  // 美元兑代理币/官方提现，仅支持消费熔断，对应UsdFuseService

```text
5: class FuseTypes
6: {
7:     // 熔断方式  1.派发熔断 2.消费熔断
8:     const FUSE_TYPE_ADD = 1;
9:     const FUSE_TYPE_CONSUME = 2;
10:
11:     //熔断大类
12:     const BIG_TYPE_COINS = 1;  //金币，对应CoinFuseService
13:     const BIG_TYPE_DIAMOND = 2;    //钻石，对应DiamondFuseService
14:     const BIG_TYPE_PACK_GIFT = 3;    //背包礼物，对应PackGiftFuseService
15:     const BIG_TYPE_STRANGE_FATE = 4;  // 火箭，对应StrangeFateFuseService
16:     const BIG_TYPE_GEMSTONE = 5;  // 游戏宝石 GemstoneFuseService
17:     const BIG_TYPE_USD = 6;  // 美元兑代理币/官方提现，仅支持消费熔断，对应UsdFuseService
18:
19:     //大类映射
20:     public static function getBigTypesMap(): array
21:     {
22:         return [
23:             self::BIG_TYPE_COINS => '金流熔断',
24:             self::BIG_TYPE_DIAMOND => '钻石熔断',
25:             self::BIG_TYPE_PACK_GIFT => '背包礼物熔断',
26:             self::BIG_TYPE_STRANGE_FATE => '火箭熔断',
27: //            self::BIG_TYPE_GEMSTONE => '游戏宝石熔断',
28:             self::BIG_TYPE_USD => '美元熔断',
29:         ];
```

### `waky6_miyouphp/php/services/Monitor/Fuse/FuseTypes.php:28`

- 命中检索词：美元熔断
- 证据行：self::BIG_TYPE_USD => '美元熔断',

```text
16:     const BIG_TYPE_GEMSTONE = 5;  // 游戏宝石 GemstoneFuseService
17:     const BIG_TYPE_USD = 6;  // 美元兑代理币/官方提现，仅支持消费熔断，对应UsdFuseService
18:
19:     //大类映射
20:     public static function getBigTypesMap(): array
21:     {
22:         return [
23:             self::BIG_TYPE_COINS => '金流熔断',
24:             self::BIG_TYPE_DIAMOND => '钻石熔断',
25:             self::BIG_TYPE_PACK_GIFT => '背包礼物熔断',
26:             self::BIG_TYPE_STRANGE_FATE => '火箭熔断',
27: //            self::BIG_TYPE_GEMSTONE => '游戏宝石熔断',
28:             self::BIG_TYPE_USD => '美元熔断',
29:         ];
30:     }
31:
32:     /**
33:      * 大类名称
34:      * @param int $bigType
35:      * @return string
36:      */
37:     public static function getBigTypeName(int $bigType): string
38:     {
39:         return self::getBigTypesMap()[$bigType] ?? '';
40:     }
```

### `waky6_miyouphp/php/services/Monitor/Fuse/FuseTypes.php:63`

- 命中检索词：UsdFuseService
- 证据行：self::BIG_TYPE_USD => UsdFuseService::getSourceMap(),

```text
51:             self::FUSE_TYPE_CONSUME => '消费熔断',
52:         ][$fuseType] ?? '';
53:     }
54:
55:     public static function getChildTypes(): array
56:     {
57:         return [
58:             self::BIG_TYPE_COINS => CoinFuseService::getSourceMap(),
59:             self::BIG_TYPE_DIAMOND => DiamondFuseService::getSourceMap(),
60:             self::BIG_TYPE_PACK_GIFT => PackGiftFuseService::getSourceMap(),
61:             self::BIG_TYPE_STRANGE_FATE => StrangeFateFuseService::getSourceMap(),
62: //            self::BIG_TYPE_GEMSTONE => GemstoneFuseService::getSourceMap(),
63:             self::BIG_TYPE_USD => UsdFuseService::getSourceMap(),
64:         ];
65:     }
66:
67:     //运营后台大类选项
68:     public static function getBigTypesSelectOptions(): array
69:     {
70:         $types = self::getBigTypesMap();
71:         $options = [];
72:
73:         foreach ($types as $value => $label) {
74:             $options[] = [
75:                 'value' => $value,
```

### `waky6_miyouphp/php/services/Monitor/Fuse/PackGiftFuseService.php:67`

- 命中检索词：isTriggerMaxThreshold
- 证据行：public function isTriggerMaxThreshold(int $uid, int $type, $num): array

```text
55:
56:         $flag = self::FUSE_TYPE_PACK_GIFT_MAP[$type];
57:         return [$flag];
58:     }
59:
60:     /**
61:      * 判断是否触发最大阈值
62:      * @param int $uid
63:      * @param int $type
64:      * @param $num
65:      * @return array
66:      */
67:     public function isTriggerMaxThreshold(int $uid, int $type, $num): array
68:     {
69:         switch ($type) {
70:             case FuseTypes::FUSE_TYPE_ADD:
71:                 return [$num >= FuseConf::getMaxAddPackGift(), FuseConf::getMaxAddPackGift()];
72:             default:
73:                 return [false, 0];
74:         }
75:     }
76:
77:     /**
78:      * 获取恢复服务
79:      */
```

### `waky6_miyouphp/php/services/Monitor/Fuse/PackGiftFuseService.php:80`

- 命中检索词：getRestoreService
- 证据行：public function getRestoreService()

```text
68:     {
69:         switch ($type) {
70:             case FuseTypes::FUSE_TYPE_ADD:
71:                 return [$num >= FuseConf::getMaxAddPackGift(), FuseConf::getMaxAddPackGift()];
72:             default:
73:                 return [false, 0];
74:         }
75:     }
76:
77:     /**
78:      * 获取恢复服务
79:      */
80:     public function getRestoreService()
81:     {
82:         if ($this->restoreService === null) {
83:             $this->restoreService = new \giftModel(getMainDB(), getRedis());
84:         }
85:         return $this->restoreService;
86:     }
87:
88:     /**
89:      * 恢复熔断数据
90:      * @param int $uid
91:      * @param int $bigType
92:      * @param int $type
```

### `waky6_miyouphp/php/services/Monitor/Fuse/PackGiftFuseService.php:120`

- 命中检索词：getRestoreService
- 证据行：$res = $this->getRestoreService()->sendPackGift(...$content);

```text
108:             return $this->returnFuse('cache key is exist. stop 2');
109:         }
110:
111:         end($content);
112:         $lastKey = key($content);
113:         $extra = &$content[$lastKey]; // 使用引用
114:         if (true === is_array($extra) && $monitorIgnoreStat > 0) {
115:             // 不需要重新赋值，因为使用的是引用
116:             $extra['monitorIgnoreStat'] = $monitorIgnoreStat;
117:         }
118:         $this->logger->logInfo('sendPackGift:' . json_encode($content));
119:
120:         $res = $this->getRestoreService()->sendPackGift(...$content);
121:
122:         if (false === $res) {
123:             return $this->returnFail('sendPackGift fail. ' . json_encode($content));
124:         }
125:         return $this->returnSuccess();
126:     }
127: }
```

### `waky6_miyouphp/php/services/Monitor/Fuse/StrangeFateFuseService.php:64`

- 命中检索词：isTriggerMaxThreshold
- 证据行：public function isTriggerMaxThreshold(int $uid, int $type, $num): array

```text
52:
53:         $flag = self::FUSE_TYPE_STRANGE_FATE_MAP[$type];
54:         return [$flag];
55:     }
56:
57:     /**
58:      * 判断是否触发最大阈值
59:      * @param int $uid
60:      * @param int $type
61:      * @param $num
62:      * @return array
63:      */
64:     public function isTriggerMaxThreshold(int $uid, int $type, $num): array
65:     {
66:         return [false, 0];
67:     }
68:
69:     /**
70:      * 获取恢复服务
71:      * @return mixed
72:      */
73:     public function getRestoreService()
74:     {
75:         if ($this->restoreService === null) {
76:             $db = ServiceFactory::getMainDB();
```

### `waky6_miyouphp/php/services/Monitor/Fuse/StrangeFateFuseService.php:73`

- 命中检索词：getRestoreService
- 证据行：public function getRestoreService()

```text
61:      * @param $num
62:      * @return array
63:      */
64:     public function isTriggerMaxThreshold(int $uid, int $type, $num): array
65:     {
66:         return [false, 0];
67:     }
68:
69:     /**
70:      * 获取恢复服务
71:      * @return mixed
72:      */
73:     public function getRestoreService()
74:     {
75:         if ($this->restoreService === null) {
76:             $db = ServiceFactory::getMainDB();
77:             $redis2 = ServiceFactory::getRedis();
78:             $this->restoreService = new StrangeFateModelV2($db, $redis2);
79:         }
80:         return $this->restoreService;
81:     }
82:
83:     /**
84:      * 恢复熔断数据
85:      * @param int $uid
```

### `waky6_miyouphp/php/services/Monitor/Fuse/StrangeFateFuseService.php:111`

- 命中检索词：getRestoreService
- 证据行：$this->getRestoreService()->addValue(...$content);

```text
99:
100:         // 检测一下熔断缓存key是否存在
101:         if (true === $this->restoreFlag && true === $this->checkCacheKeyIsExist()) {
102:             $this->restoreFlag = false;
103:             return $this->returnFuse('cache key is exist.');
104:         }
105:         // 火箭熔断之后恢复数据算恢复时的数据
106:         list($rid, $uid, $coin, $tm, $begin) = $content;
107:         $content = [$rid, $uid, $coin, time(), $begin];
108:
109:         $this->logger->logInfo('addStrangeFateValue:' . json_encode($content));
110:
111:         $this->getRestoreService()->addValue(...$content);
112:
113:         return $this->returnSuccess();
114:     }
115:
116: }
```

### `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:10`

- 命中检索词：UsdFuseService
- 证据行：class UsdFuseService extends BaseFuseService

```text
1: <?php
2:
3: namespace Services\Monitor\Fuse;
4:
5: /**
6:  * 美元业务熔断。
7:  *
8:  * 覆盖美元兑代理币与官方提现两类消费场景；两者只阻断业务入口，不产生可恢复的资产记录。
9:  */
10: class UsdFuseService extends BaseFuseService
11: {
12:     const KEY_PREFIX = 'usd_fuse';
13:
14:     /** 官方提现来源，仅允许消费熔断 */
15:     const OFFICIAL_PAYMENT = 1;
16:     /** 美元兑代理币来源，仅允许消费熔断 */
17:     const USD_EXCHANGE = 2;
18:
19:     /**
20:      * 获取美元熔断独立 Redis 前缀。
21:      *
22:      * @return string
```

### `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:20`

- 命中检索词：美元熔断
- 证据行：* 获取美元熔断独立 Redis 前缀。

```text
8:  * 覆盖美元兑代理币与官方提现两类消费场景；两者只阻断业务入口，不产生可恢复的资产记录。
9:  */
10: class UsdFuseService extends BaseFuseService
11: {
12:     const KEY_PREFIX = 'usd_fuse';
13:
14:     /** 官方提现来源，仅允许消费熔断 */
15:     const OFFICIAL_PAYMENT = 1;
16:     /** 美元兑代理币来源，仅允许消费熔断 */
17:     const USD_EXCHANGE = 2;
18:
19:     /**
20:      * 获取美元熔断独立 Redis 前缀。
21:      *
22:      * @return string
23:      */
24:     public function getKey(): string
25:     {
26:         return self::KEY_PREFIX;
27:     }
28:
29:     /**
30:      * 美元熔断仅支持消费方向。
31:      *
32:      * @return array<int,int>
```

### `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:30`

- 命中检索词：美元熔断
- 证据行：* 美元熔断仅支持消费方向。

```text
18:
19:     /**
20:      * 获取美元熔断独立 Redis 前缀。
21:      *
22:      * @return string
23:      */
24:     public function getKey(): string
25:     {
26:         return self::KEY_PREFIX;
27:     }
28:
29:     /**
30:      * 美元熔断仅支持消费方向。
31:      *
32:      * @return array<int,int>
33:      */
34:     public function getFuseTypes(): array
35:     {
36:         return [FuseTypes::FUSE_TYPE_CONSUME];
37:     }
38:
39:     /**
40:      * 运营后台可选的美元熔断来源。
41:      *
42:      * @return array<int,string>
```

### `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:40`

- 命中检索词：美元熔断
- 证据行：* 运营后台可选的美元熔断来源。

```text
28:
29:     /**
30:      * 美元熔断仅支持消费方向。
31:      *
32:      * @return array<int,int>
33:      */
34:     public function getFuseTypes(): array
35:     {
36:         return [FuseTypes::FUSE_TYPE_CONSUME];
37:     }
38:
39:     /**
40:      * 运营后台可选的美元熔断来源。
41:      *
42:      * @return array<int,string>
43:      */
44:     public static function getSourceMap(): array
45:     {
46:         return [
47:             self::OFFICIAL_PAYMENT => '熔断官方提现',
48:             self::USD_EXCHANGE => '熔断美元兑代理币',
49:         ];
50:     }
51:
52:     /**
```

### `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:77`

- 命中检索词：isTriggerMaxThreshold
- 证据行：public function isTriggerMaxThreshold(int $uid, int $type, $num): array

```text
65:
66:         return [$from];
67:     }
68:
69:     /**
70:      * 美元业务没有单笔最大阈值，只消费运营后台配置的熔断规则。
71:      *
72:      * @param int $uid 用户 UID
73:      * @param int $type 熔断方向
74:      * @param mixed $num 本次数量
75:      * @return array{0:bool,1:int}
76:      */
77:     public function isTriggerMaxThreshold(int $uid, int $type, $num): array
78:     {
79:         return [false, 0];
80:     }
81:
82:     /**
83:      * 获取熔断来源展示文案。
84:      *
85:      * @param int $from 业务来源
86:      * @return string
87:      */
88:     public function fromToString(int $from): string
89:     {
```

### `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:94`

- 命中检索词：美元熔断
- 证据行：* 美元熔断不涉及资产恢复。

```text
82:     /**
83:      * 获取熔断来源展示文案。
84:      *
85:      * @param int $from 业务来源
86:      * @return string
87:      */
88:     public function fromToString(int $from): string
89:     {
90:         return self::getSourceMap()[$from] ?? '';
91:     }
92:
93:     /**
94:      * 美元熔断不涉及资产恢复。
95:      *
96:      * @return null
97:      */
98:     public function getRestoreService()
99:     {
100:         return null;
101:     }
102:
103:     /**
104:      * 美元熔断不支持资产恢复。
105:      *
106:      * @param int $uid 用户 UID
```

### `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:98`

- 命中检索词：getRestoreService
- 证据行：public function getRestoreService()

```text
86:      * @return string
87:      */
88:     public function fromToString(int $from): string
89:     {
90:         return self::getSourceMap()[$from] ?? '';
91:     }
92:
93:     /**
94:      * 美元熔断不涉及资产恢复。
95:      *
96:      * @return null
97:      */
98:     public function getRestoreService()
99:     {
100:         return null;
101:     }
102:
103:     /**
104:      * 美元熔断不支持资产恢复。
105:      *
106:      * @param int $uid 用户 UID
107:      * @param int $bigType 熔断大类
108:      * @param int $type 熔断方向
109:      * @param int $monitorIgnoreStat 是否忽略状态
110:      * @param array $content 恢复参数
```

### `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:104`

- 命中检索词：美元熔断
- 证据行：* 美元熔断不支持资产恢复。

```text
92:
93:     /**
94:      * 美元熔断不涉及资产恢复。
95:      *
96:      * @return null
97:      */
98:     public function getRestoreService()
99:     {
100:         return null;
101:     }
102:
103:     /**
104:      * 美元熔断不支持资产恢复。
105:      *
106:      * @param int $uid 用户 UID
107:      * @param int $bigType 熔断大类
108:      * @param int $type 熔断方向
109:      * @param int $monitorIgnoreStat 是否忽略状态
110:      * @param array $content 恢复参数
111:      * @return array{code:int,msg:string}
112:      */
113:     public function handelRecover(int $uid, int $bigType, int $type, int $monitorIgnoreStat, array $content): array
114:     {
115:         return $this->returnFail('usd business fuse does not support asset recovery');
116:     }
```

### `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:119`

- 命中检索词：美元熔断
- 证据行：* 检查官方提现是否命中美元熔断。

```text
107:      * @param int $bigType 熔断大类
108:      * @param int $type 熔断方向
109:      * @param int $monitorIgnoreStat 是否忽略状态
110:      * @param array $content 恢复参数
111:      * @return array{code:int,msg:string}
112:      */
113:     public function handelRecover(int $uid, int $bigType, int $type, int $monitorIgnoreStat, array $content): array
114:     {
115:         return $this->returnFail('usd business fuse does not support asset recovery');
116:     }
117:
118:     /**
119:      * 检查官方提现是否命中美元熔断。
120:      *
121:      * 熔断读取异常时按 waky7 的约定放行，避免 Redis 故障扩大为提现整体不可用；异常必须记录。
122:      *
123:      * @param int $uid 提现用户 UID
124:      * @return bool true=命中熔断，false=未命中或检查异常
125:      */
126:     public static function isOfficialPaymentTriggered(int $uid): bool
127:     {
128:         return self::isTriggered($uid, self::OFFICIAL_PAYMENT);
129:     }
130:
131:     /**
```

### `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:126`

- 命中检索词：isOfficialPaymentTriggered
- 证据行：public static function isOfficialPaymentTriggered(int $uid): bool

```text
114:     {
115:         return $this->returnFail('usd business fuse does not support asset recovery');
116:     }
117:
118:     /**
119:      * 检查官方提现是否命中美元熔断。
120:      *
121:      * 熔断读取异常时按 waky7 的约定放行，避免 Redis 故障扩大为提现整体不可用；异常必须记录。
122:      *
123:      * @param int $uid 提现用户 UID
124:      * @return bool true=命中熔断，false=未命中或检查异常
125:      */
126:     public static function isOfficialPaymentTriggered(int $uid): bool
127:     {
128:         return self::isTriggered($uid, self::OFFICIAL_PAYMENT);
129:     }
130:
131:     /**
132:      * 检查美元兑代理币是否命中熔断。
133:      *
134:      * @param int $uid 发起兑换的币商 UID
135:      * @return bool true=命中熔断，false=未命中或检查异常
136:      */
137:     public static function isUsdExchangeTriggered(int $uid): bool
138:     {
```

### `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:137`

- 命中检索词：isUsdExchangeTriggered
- 证据行：public static function isUsdExchangeTriggered(int $uid): bool

```text
125:      */
126:     public static function isOfficialPaymentTriggered(int $uid): bool
127:     {
128:         return self::isTriggered($uid, self::OFFICIAL_PAYMENT);
129:     }
130:
131:     /**
132:      * 检查美元兑代理币是否命中熔断。
133:      *
134:      * @param int $uid 发起兑换的币商 UID
135:      * @return bool true=命中熔断，false=未命中或检查异常
136:      */
137:     public static function isUsdExchangeTriggered(int $uid): bool
138:     {
139:         return self::isTriggered($uid, self::USD_EXCHANGE);
140:     }
141:
142:     /**
143:      * 读取指定美元来源的熔断标识；Redis 异常按放行处理并记录日志。
144:      *
145:      * @param int $uid UID
146:      * @param int $source 美元来源
147:      * @return bool
148:      */
149:     private static function isTriggered(int $uid, int $source): bool
```

### `waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:158`

- 命中检索词：BIG_TYPE_USD
- 证据行：FuseTypes::BIG_TYPE_USD,

```text
146:      * @param int $source 美元来源
147:      * @return bool
148:      */
149:     private static function isTriggered(int $uid, int $source): bool
150:     {
151:         if ($uid <= 0) {
152:             return false;
153:         }
154:
155:         try {
156:             return (new FuseService())->checkFuseFlag(
157:                 $uid,
158:                 FuseTypes::BIG_TYPE_USD,
159:                 FuseTypes::FUSE_TYPE_CONSUME,
160:                 $source
161:             );
162:         } catch (\Throwable $e) {
163:             \ExceptionGuard::log(\ExceptionGuard::logger(), $e, __METHOD__, ['uid' => $uid, 'source' => $source]);
164:             return false;
165:         }
166:     }
167: }
```

### `waky6_miyouphp/php/services/Monitor/MonitorConfigValidateService.php:371`

- 命中检索词：美元熔断
- 证据行：// 204 的美元来源挂在“其他来源”下，只有美元类来源才允许匹配美元熔断措施。

```text
359:             }
360:             $bigType = intval($config['bigType'] ?? 0);
361:             // 统一抽象成 coin/diamond/rocket 等标签，供风控来源和熔断来源匹配使用。
362:             switch ($bigType) {
363:                 case MonitorTypes::BIG_TYPE_COINS_FLOW:
364:                 case MonitorTypes::BIG_TYPE_CHARGE:
365:                 case MonitorTypes::BIG_TYPE_GAME_ADD_WALLET:
366:                 case MonitorTypes::BIG_TYPE_GAME_CONSUME_WALLET:
367:                     $tags[] = 'coin';
368:                     break;
369:                 case MonitorTypes::BIG_TYPE_ADMIN:
370:                     $tags[] = 'coin';
371:                     // 204 的美元来源挂在“其他来源”下，只有美元类来源才允许匹配美元熔断措施。
372:                     foreach ((array)($config['childTypes'] ?? []) as $childType) {
373:                         if (in_array($childType, [
374:                             AdminMonitorService::COINS_MERCHANT_BUY_MONEY,
375:                             AdminMonitorService::USD_TO_PROXY_COINS,
376:                             AdminMonitorService::USD_CONSUME,
377:                             AdminMonitorService::USD_INCREASE,
378:                             AdminMonitorService::COINS_MERCHANT_USD_INCREASE,
379:                         ], true)) {
380:                             $tags[] = 'usd';
381:                             break;
382:                         }
383:                     }
```

### `waky6_miyouphp/php/services/Monitor/MonitorConfigValidateService.php:405`

- 命中检索词：美元熔断
- 证据行：// 只有官方提现来源属于美元域；普通钻石产出/消耗不能误匹配美元熔断。

```text
393:                         if (intval($childType) === GiftMonitorService::SOURCE_PACKET) {
394:                             $tags[] = 'pack_gift';
395:                             break;
396:                         }
397:                     }
398:                     break;
399:                 case MonitorTypes::BIG_TYPE_ACTIVITY_GIFT:
400:                     $tags[] = 'coin';
401:                     $tags[] = 'gift';
402:                     break;
403:                 case MonitorTypes::BIG_TYPE_DIAMOND:
404:                     $tags[] = 'diamond';
405:                     // 只有官方提现来源属于美元域；普通钻石产出/消耗不能误匹配美元熔断。
406:                     foreach ((array)($config['childTypes'] ?? []) as $childType) {
407:                         if (in_array($childType, [
408:                             DiamondMonitorService::WITHDRAW_OFFICIAL_SINGLE,
409:                             DiamondMonitorService::WITHDRAW_OFFICIAL_PLATFORM,
410:                         ], true)) {
411:                             $tags[] = 'usd';
412:                             break;
413:                         }
414:                     }
415:                     if (in_array(DiamondMonitorService::WITHDRAW_TO_PROXY, (array)($config['childTypes'] ?? []), true)) {
416:                         $tags[] = self::TAG_WITHDRAW_TO_PROXY;
417:                     }
```

### `waky6_miyouphp/php/services/Monitor/MonitorConfigValidateService.php:463`

- 命中检索词：BIG_TYPE_USD
- 证据行：case FuseTypes::BIG_TYPE_USD:

```text
451:                         $tags[] = self::TAG_WITHDRAW_TO_PROXY;
452:                     }
453:                     break;
454:                 case FuseTypes::BIG_TYPE_PACK_GIFT:
455:                     $tags[] = 'pack_gift';
456:                     break;
457:                 case FuseTypes::BIG_TYPE_STRANGE_FATE:
458:                     $tags[] = 'rocket';
459:                     break;
460:                 case FuseTypes::BIG_TYPE_GEMSTONE:
461:                     $tags[] = 'gemstone';
462:                     break;
463:                 case FuseTypes::BIG_TYPE_USD:
464:                     $tags[] = 'usd';
465:                     break;
466:             }
467:         }
468:
469:         return array_values(array_unique($tags));
470:     }
471:
472:     private function hasTagIntersection(array $left, array $right): bool
473:     {
474:         if (empty($left) || empty($right)) {
475:             return false;
```

### `waky6_miyouphp/php/lang/ar/coins_merchant.php:67`

- 命中检索词：ERR_USD_EXCHANGE_FUSE_TRIGGERED
- 证据行：'ERR_USD_EXCHANGE_FUSE_TRIGGERED' => 'تحويل الدولار غير متاح مؤقتًا. يرجى المحاولة لاحقًا',

```text
55:     'ERR_MERCHANT_NOT_IN_REGION' => 'ليس وكيلاً في منطقتك',
56:     'ERR_MERCHANT_NOT_LEVEL1' => 'ليس وكيلاً من المستوى الأول',
57:     'ERR_MERCHANT_BALANCE_NOT_ENOUGH' => 'رصيد الوكيل غير كافٍ',
58:     'ERR_MERCHANT_EXCHANGE_DISABLED' => 'هذا الوكيل لا يدعم تبادل الألماس مقابل العملات الذهبية',
59:     'ERR_REGION_NOT_OPEN' => 'هذه المنطقة غير مفتوحة بعد',
60:
61:     // 币商前台在线治理提醒（骨架 §13.3.2 第 11 点，文案与 waky7 coins_merchant.php:51-54 一致）
62:     'agent_offline_push_title' => 'يرجى إبقاء APP في الواجهة الأمامية！',
63:     'agent_offline_push_content' => 'في حال البقاء خارج الواجهة الأمامية مدة طويلة، سيُغلق استقبال الطلبات تلقائياً！',
64:     'agent_auto_close_push_title' => 'أُغلق استقبال الطلبات تلقائياً！',
65:     'agent_auto_close_push_content' => 'بسبب البقاء خارج الواجهة الأمامية مدة طويلة، أُغلق استقبال الطلبات تلقائياً، يرجى فتح مفتاح الاستقبال يدوياً！',
66:
67:     'ERR_USD_EXCHANGE_FUSE_TRIGGERED' => 'تحويل الدولار غير متاح مؤقتًا. يرجى المحاولة لاحقًا',
68: ];
```

### `waky6_miyouphp/php/lang/ar/exception.php:11`

- 命中检索词：OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED
- 证据行：'WITHDRAWAL_OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED' => 'السحب الرسمي غير متاح مؤقتًا. يرجى اختيار قناة تاجر أو المحاولة لاحقًا',

```text
1: <?php
2: return [
3:     'ERROR' => 'نظام غير طبيعي',
4:     'INVALID_PARAMS' => 'خطأ في البارامتر',
5:     'INVALID_HTTP_METHODS' => 'خطأ طريقة http',
6:     'DB_ERROR' => 'خطأ في قاعدة البيانات',
7:     'VALIDATION_ERR' => 'خطأ في المدقق',
8:     'WS_ERR' => 'خطأ websocket',
9:     'unkwonException' => 'خطأ غير محدد',
10:     'WITHDRAWAL_OFFICIAL_SETTLEMENT_AMOUNT_INVALID' => 'مبلغ السحب منخفض جدًا لتغطية رسوم الدفع',
11:     'WITHDRAWAL_OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED' => 'السحب الرسمي غير متاح مؤقتًا. يرجى اختيار قناة تاجر أو المحاولة لاحقًا',
12:     'WITHDRAWAL_DEALER_WITHDRAWAL_FUSE_TRIGGERED' => 'السحب إلى الوكيل غير متاح مؤقتًا. يرجى المحاولة لاحقًا',
13:
14: ];
```

### `waky6_miyouphp/php/lang/bn/coins_merchant.php:44`

- 命中检索词：ERR_USD_EXCHANGE_FUSE_TRIGGERED
- 证据行：'ERR_USD_EXCHANGE_FUSE_TRIGGERED' => 'USD এক্সচেঞ্জ সাময়িকভাবে অনুপলব্ধ। অনুগ্রহ করে পরে আবার চেষ্টা করুন',

```text
32:     'STOP_SUB_MERCHANT_MSG_TO_LEADER' => 'আপনার পরিবেশক #nick# (UID: #uid#) দীর্ঘদিন ধরে প্রক্সি কয়েন ক্রয় করেনি এবং তার সেকেন্ডারি এজেন্ট স্ট্যাটাস নিষ্ক্রিয় করা হয়েছে। আপনার কোন প্রশ্ন থাকলে, অফিসিয়াল প্রশাসকের সাথে যোগাযোগ করুন।',
33:     'PROP_DAYS' => '#days# দিন(গুলি)',
34:     'SEND_PROP_FAIL' => 'বিতরণ ব্যর্থ হয়েছে৷',
35:     'THE_USER_NOT_YOUR_SUB_MERCHANT' => 'এই ব্যবহারকারী আপনার পরিবেশক নয়',
36:     'NON_IN_REGIONAL_USERS' => 'ব্যবহারকারী এই এলাকার অন্তর্গত নয়। পরিচালনা করতে অক্ষম!',
37:
38:     'SYS_DEL_SUM_MERCHANT_MSG' => 'অ্যাডমিন #nick# (UID: #uid#) এ আপনাররিসেলার স্ট্যাটাস সরিয়ে দিয়েছেন। আপনার কোন প্রশ্ন থাকলে, প্রশাসকের সাথে যোগাযোগ করুন',
39:
40:     'SYS_DEL_SUM_MERCHANT_MSG_TO_USER' => 'অ্যাডমিন আপনার পরিবেশক #nick# (UID: #uid#) সরিয়ে দিয়েছেন। আপনার কোন প্রশ্ন থাকলে, প্রশাসকের সাথে যোগাযোগ করুন',
41:
42:     'REQ_LOGIN_SUCCESS_MSG' => 'অফিসিয়াল অ্যাডমিন কয়েন এজেন্ট কেন্দ্রে লগ ইন করার জন্য আপনার অনুরোধ নিশ্চিত করেছেন। চেক করুন।',
43:     'NON_IN_WITHDRAW_USERS' => 'Unable to withdraw funds to this user',
44:     'ERR_USD_EXCHANGE_FUSE_TRIGGERED' => 'USD এক্সচেঞ্জ সাময়িকভাবে অনুপলব্ধ। অনুগ্রহ করে পরে আবার চেষ্টা করুন',
45: ];
```

### `waky6_miyouphp/php/lang/bn/exception.php:11`

- 命中检索词：OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED
- 证据行：'WITHDRAWAL_OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED' => 'অফিসিয়াল উত্তোলন সাময়িকভাবে অনুপলব্ধ। অনুগ্রহ করে ডিলার চ্যানেল বেছে নিন অথবা পরে আবার চেষ্টা করুন',

```text
1: <?php
2: return [
3:     'ERROR' => 'সিস্টেম ত্রুটি',
4:     'INVALID_PARAMS' => 'ত্রুটি তথ্য',
5:     'INVALID_HTTP_METHODS' => 'http পদ্ধতি ত্রুটি',
6:     'DB_ERROR' => 'ডাটাবেস ত্রুটি',
7:     'VALIDATION_ERR' => 'যাচাইকরণ কোড ত্রুটি',
8:     'WS_ERR' => 'websocket ত্রুটি',
9:     'unkwonException' => 'অনির্ধারিত ত্রুটি',
10:     'WITHDRAWAL_OFFICIAL_SETTLEMENT_AMOUNT_INVALID' => 'উত্তোলনের পরিমাণ পেমেন্ট ফি মেটানোর জন্য খুব কম',
11:     'WITHDRAWAL_OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED' => 'অফিসিয়াল উত্তোলন সাময়িকভাবে অনুপলব্ধ। অনুগ্রহ করে ডিলার চ্যানেল বেছে নিন অথবা পরে আবার চেষ্টা করুন',
12:     'WITHDRAWAL_DEALER_WITHDRAWAL_FUSE_TRIGGERED' => 'ডিলার উত্তোলন সাময়িকভাবে অনুপলব্ধ। অনুগ্রহ করে পরে আবার চেষ্টা করুন',
13:
14: ];
```

### `waky6_miyouphp/php/lang/en/coins_merchant.php:72`

- 命中检索词：ERR_USD_EXCHANGE_FUSE_TRIGGERED
- 证据行：'ERR_USD_EXCHANGE_FUSE_TRIGGERED' => 'USD exchange is temporarily unavailable. Please try again later',

```text
60:     'ERR_MERCHANT_NOT_IN_REGION' => 'Not a merchant of your region',
61:     'ERR_MERCHANT_NOT_LEVEL1' => 'Not a level-1 merchant',
62:     'ERR_MERCHANT_BALANCE_NOT_ENOUGH' => 'The merchant has insufficient balance',
63:     'ERR_MERCHANT_EXCHANGE_DISABLED' => 'This merchant does not accept diamond-to-coins exchange',
64:     'ERR_REGION_NOT_OPEN' => 'This region is not open yet',
65:
66:     // 币商前台在线治理提醒（骨架 §13.3.2 第 11 点，文案与 waky7 coins_merchant.php:51-54 一致）
67:     'agent_offline_push_title' => 'Please keep the APP running in the foreground!',
68:     'agent_offline_push_content' => 'If the APP stays in the background for too long, order receiving will be turned off automatically.',
69:     'agent_auto_close_push_title' => 'Order receiving has been turned off automatically!',
70:     'agent_auto_close_push_content' => 'The APP has stayed in the background for too long. Order receiving has been turned off automatically. Please turn it back on manually!',
71:
72:     'ERR_USD_EXCHANGE_FUSE_TRIGGERED' => 'USD exchange is temporarily unavailable. Please try again later',
73: ];
```

### `waky6_miyouphp/php/lang/en/exception.php:29`

- 命中检索词：OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED
- 证据行：'WITHDRAWAL_OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED' => 'Official withdrawal is temporarily unavailable. Please choose a dealer channel or try again later',

```text
17: 'WITHDRAWAL_CHANNEL_UNAVAILABLE' => 'Payment channel unavailable',
18: 'WITHDRAWAL_MERCHANT_UNAVAILABLE' => 'The dealer cannot accept this order',
19: 'WITHDRAWAL_REGION_NOT_OPEN' => 'Withdrawal is not available in your region',
20: 'WITHDRAWAL_FREQUENT_OPERATION' => 'Operations are too frequent',
21: 'WITHDRAWAL_OFFICIAL_NOT_IMPLEMENTED' => 'Official withdrawal is not available yet',
22: 'WITHDRAWAL_WRITE_NOT_IMPLEMENTED' => 'Withdrawal is not available yet',
23: 'WITHDRAWAL_ORDER_NOT_FOUND' => 'Order not found',
24: 'WITHDRAWAL_NOT_MY_ORDER' => 'This is not your order',
25: 'WITHDRAWAL_ORDER_STATUS_CHANGED' => 'Order status has changed, please refresh',
26: 'WITHDRAWAL_CONFIRM_WINDOW_CLOSED' => 'Confirmation time has passed',
27: 'WITHDRAWAL_ORDER_NOT_ACTIONABLE' => 'This order cannot be processed',
28: 'WITHDRAWAL_OFFICIAL_SETTLEMENT_AMOUNT_INVALID' => 'The withdrawal amount is too low to cover the payout fee',
29: 'WITHDRAWAL_OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED' => 'Official withdrawal is temporarily unavailable. Please choose a dealer channel or try again later',
30: 'WITHDRAWAL_DEALER_WITHDRAWAL_FUSE_TRIGGERED' => 'Dealer withdrawal is temporarily unavailable. Please try again later',
31: ];
```

### `waky6_miyouphp/php/lang/es/coins_merchant.php:44`

- 命中检索词：ERR_USD_EXCHANGE_FUSE_TRIGGERED
- 证据行：'ERR_USD_EXCHANGE_FUSE_TRIGGERED' => 'El cambio de USD no está disponible temporalmente. Inténtalo de nuevo más tarde',

```text
32:     'STOP_SUB_MERCHANT_MSG_TO_LEADER' => 'Su distribuidor #nick# (UID: #uid#) ha sido desactivado como agente secundario porque no ha comprado monedas de agente durante mucho tiempo. Si tiene alguna pregunta, comuníquese con el administrador oficial.',
33:     'PROP_DAYS' => '#días# día(s)',
34:     'SEND_PROP_FAIL' => 'Error en la distribución',
35:     'THE_USER_NOT_YOUR_SUB_MERCHANT' => 'Este usuario no es su distribuidor',
36:     'NON_IN_REGIONAL_USERS' => 'El usuario no pertenece a esta área. ¡No se puede operar!',
37:
38:     'SYS_DEL_SUM_MERCHANT_MSG' => 'El administrador ha eliminado tu estado de distribuidor en #nick# (ID de usuario: #uid#). Si tienes alguna pregunta, contacta al administrador.',
39:
40:     'SYS_DEL_SUM_MERCHANT_MSG_TO_USER' => 'El administrador ha eliminado tu distribuidor #nick# (UID: #uid#). Si tienes alguna pregunta, por favor contáctalo al administrador.',
41:
42:     'REQ_LOGIN_SUCCESS_MSG' => 'El administrador oficial ha confirmado su solicitud para iniciar sesión en el centro de agentes de monedas. Por favor, compruebe.',
43:     'NON_IN_WITHDRAW_USERS' => 'Unable to withdraw funds to this user',
44:     'ERR_USD_EXCHANGE_FUSE_TRIGGERED' => 'El cambio de USD no está disponible temporalmente. Inténtalo de nuevo más tarde',
45: ];
```

### `waky6_miyouphp/php/lang/es/exception.php:11`

- 命中检索词：OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED
- 证据行：'WITHDRAWAL_OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED' => 'El retiro oficial no está disponible temporalmente. Elige un canal de distribuidor o inténtalo más tarde',

```text
1: <?php
2: return [
3:     'ERROR' => 'Error del sistema',
4:     'INVALID_PARAMS' => 'Error de datos',
5:     'INVALID_HTTP_METHODS' => 'error de método http',
6:     'DB_ERROR' => 'Error de base de datos',
7:     'VALIDATION_ERR' => 'Error del código de verificación',
8:     'WS_ERR' => 'websocket error',
9:     'unkwonException' => 'error desconocido',
10:     'WITHDRAWAL_OFFICIAL_SETTLEMENT_AMOUNT_INVALID' => 'El importe del retiro es demasiado bajo para cubrir la comisión de pago',
11:     'WITHDRAWAL_OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED' => 'El retiro oficial no está disponible temporalmente. Elige un canal de distribuidor o inténtalo más tarde',
12:     'WITHDRAWAL_DEALER_WITHDRAWAL_FUSE_TRIGGERED' => 'El retiro al distribuidor no está disponible temporalmente. Inténtalo de nuevo más tarde',
13:
14: ];
```

### `waky6_miyouphp/php/lang/hi/coins_merchant.php:44`

- 命中检索词：ERR_USD_EXCHANGE_FUSE_TRIGGERED
- 证据行：'ERR_USD_EXCHANGE_FUSE_TRIGGERED' => 'USD एक्सचेंज फ़िलहाल उपलब्ध नहीं है। कृपया बाद में पुनः प्रयास करें',

```text
32:     'STOP_SUB_MERCHANT_MSG_TO_LEADER' => 'आपके वितरक #nick# (UID: #uid#) को द्वितीयक एजेंट के रूप में निष्क्रिय कर दिया गया है क्योंकि उसने लंबे समय से एजेंट सिक्के नहीं खरीदे हैं। यदि आपके कोई प्रश्न हैं, तो कृपया आधिकारिक व्यवस्थापक से संपर्क करें।',
33:     'PROP_DAYS' => '#day# दिन(दिन)',
34:     'SEND_PROP_FAIL' => 'वितरण विफल',
35:     'THE_USER_NOT_YOUR_SUB_MERCHANT' => 'यह उपयोगकर्ता आपका वितरक नहीं है',
36:     'NON_IN_REGIONAL_USERS' => 'उपयोगकर्ता इस क्षेत्र से संबंधित नहीं है। संचालन करने में असमर्थ!',
37:
38:     'SYS_DEL_SUM_MERCHANT_MSG' => 'व्यवस्थापक ने #nick# (UID: #uid#) में आपका वितरक दर्जा हटा दिया है। यदि आपके कोई प्रश्न हैं, तो कृपया व्यवस्थापक से संपर्क करें',
39:
40:     'SYS_DEL_SUM_MERCHANT_MSG_TO_USER' => 'व्यवस्थापक ने आपका वितरक #nick# (UID: #uid#) हटा दिया है। यदि आपके कोई प्रश्न हैं, तो कृपया व्यवस्थापक से संपर्क करें',
41:
42:     'REQ_LOGIN_SUCCESS_MSG' => 'आधिकारिक व्यवस्थापक ने सिक्का एजेंट केंद्र में लॉग इन करने के आपके अनुरोध की पुष्टि कर दी है। कृपया जाँच करें।',
43:     'NON_IN_WITHDRAW_USERS' => 'इस यूज़र के पैसे नहीं निकाले जा सके!',
44:     'ERR_USD_EXCHANGE_FUSE_TRIGGERED' => 'USD एक्सचेंज फ़िलहाल उपलब्ध नहीं है। कृपया बाद में पुनः प्रयास करें',
45: ];
```

### `waky6_miyouphp/php/lang/hi/exception.php:11`

- 命中检索词：OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED
- 证据行：'WITHDRAWAL_OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED' => 'आधिकारिक निकासी फ़िलहाल उपलब्ध नहीं है। कृपया डीलर चैनल चुनें या बाद में पुनः प्रयास करें',

```text
1: <?php
2: return [
3:    'ERROR' => 'सिस्टम त्रुटि',
4:     'INVALID_PARAMS' => 'त्रुटि डेटा',
5:     'INVALID_HTTP_METHODS' => 'http विधि त्रुटि',
6:     'DB_ERROR' => 'डेटाबेस त्रुटि',
7:     'VALIDATION_ERR' => 'सत्यापनकर्ता त्रुटि',
8:     'WS_ERR' => 'websocket त्रुटि',
9:     'unkwonException' => 'अपरिभाषित त्रुटि',
10:     'WITHDRAWAL_OFFICIAL_SETTLEMENT_AMOUNT_INVALID' => 'निकासी राशि भुगतान शुल्क को कवर करने के लिए बहुत कम है',
11:     'WITHDRAWAL_OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED' => 'आधिकारिक निकासी फ़िलहाल उपलब्ध नहीं है। कृपया डीलर चैनल चुनें या बाद में पुनः प्रयास करें',
12:     'WITHDRAWAL_DEALER_WITHDRAWAL_FUSE_TRIGGERED' => 'डीलर निकासी फ़िलहाल उपलब्ध नहीं है। कृपया बाद में पुनः प्रयास करें',
13:
14: ];
```

### `waky6_miyouphp/php/lang/in/coins_merchant.php:53`

- 命中检索词：ERR_USD_EXCHANGE_FUSE_TRIGGERED
- 证据行：'ERR_USD_EXCHANGE_FUSE_TRIGGERED' => 'Penukaran USD sementara tidak tersedia. Silakan coba lagi nanti',

```text
41:     'SEND_PROP_FAIL' => 'Pengiriman gagal!',
42:     'THE_USER_NOT_YOUR_SUB_MERCHANT' => 'Pengguna ini bukan Agen Sekunder Anda!',
43:
44:     'SYS_DEL_SUM_MERCHANT_MSG' => 'Admin Resmi menghapus sekunder Anda #nick#（UID：#uid#) dari sistem, Jika Anda memiliki pertanyaan, silakan hubungi admin resmi ',
45:
46:     'SYS_DEL_SUM_MERCHANT_MSG_TO_USER'=>'Admin Resmi menghapus status sekunder Anda dari #nick#（UID：#uid#）, Jika Anda memiliki pertanyaan, silakan hubungi admin resmi  ',
47:
48:     'NON_IN_REGIONAL_USERS' => 'Operasi gagal, karena pengguna non-lokal',
49:
50:     'REQ_LOGIN_SUCCESS_MSG' => 'Admin resmi telah mengkonfirmasi permintaan Anda untuk memasuki pusat agen, sekarang Anda dapat mengeceknya.',
51:
52:     'NON_IN_WITHDRAW_USERS' => 'Tidak dapat menarik dana ke pengguna ini',
53:     'ERR_USD_EXCHANGE_FUSE_TRIGGERED' => 'Penukaran USD sementara tidak tersedia. Silakan coba lagi nanti',
54: ];
```

### `waky6_miyouphp/php/lang/in/exception.php:11`

- 命中检索词：OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED
- 证据行：'WITHDRAWAL_OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED' => 'Penarikan resmi sementara tidak tersedia. Pilih saluran dealer atau coba lagi nanti',

```text
1: <?php
2: return [
3:     'ERROR' => 'Sistem abnormal',
4:     'INVALID_PARAMS' => 'Parameter salah',
5:     'INVALID_HTTP_METHODS' => 'Metode http salah',
6:     'DB_ERROR' => 'Basis data salah',
7:     'VALIDATION_ERR' => 'Validator salah',
8:     'WS_ERR' => 'websocket salah',
9:     'unkwonException' => 'Kesalahan tidak terdefinisi',
10:     'WITHDRAWAL_OFFICIAL_SETTLEMENT_AMOUNT_INVALID' => 'Jumlah penarikan terlalu rendah untuk menutupi biaya pembayaran',
11:     'WITHDRAWAL_OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED' => 'Penarikan resmi sementara tidak tersedia. Pilih saluran dealer atau coba lagi nanti',
12:     'WITHDRAWAL_DEALER_WITHDRAWAL_FUSE_TRIGGERED' => 'Penarikan ke dealer sementara tidak tersedia. Silakan coba lagi nanti',
13:
14: ];
```

### `waky6_miyouphp/php/lang/pt/coins_merchant.php:44`

- 命中检索词：ERR_USD_EXCHANGE_FUSE_TRIGGERED
- 证据行：'ERR_USD_EXCHANGE_FUSE_TRIGGERED' => 'A troca de USD está temporariamente indisponível. Tente novamente mais tarde',

```text
32:     'STOP_SUB_MERCHANT_MSG_TO_LEADER' => 'Seu distribuidor #nick# (UID: #uid#) foi desativado como agente secundário porque não compra moedas de agente há muito tempo. Se você tiver alguma dúvida, entre em contato com o administrador oficial.',
33:     'PROP_DAYS' => '#dias# dia(s)',
34:     'SEND_PROP_FAIL' => 'Falha na distribuição',
35:     'THE_USER_NOT_YOUR_SUB_MERCHANT' => 'Este usuário não é seu distribuidor',
36:     'NON_IN_REGIONAL_USERS' => 'Incapaz de operar, não usuários nesta área!',
37:
38:     'SYS_DEL_SUM_MERCHANT_MSG' => 'O administrador removeu seu status de revendador seguinte em #nick# (UID: #uid#). Se você tiver alguma dúvida, entre em contato com o administrador.',
39:
40:     'SYS_DEL_SUM_MERCHANT_MSG_TO_USER' => 'O administrador removeu seu revendador seguinte #nick# (UID: #uid#). Se você tiver alguma dúvida, entre em contato com o administrador.',
41:
42:     'REQ_LOGIN_SUCCESS_MSG' => 'O adm oficial confirmou sua solicitação para fazer login no centro do revendedor. Por favor, verifique.',
43:     'NON_IN_WITHDRAW_USERS' => 'Unable to withdraw funds to this user',
44:     'ERR_USD_EXCHANGE_FUSE_TRIGGERED' => 'A troca de USD está temporariamente indisponível. Tente novamente mais tarde',
45: ];
```

## 运行环境验证

- 本次未执行数据库、配置或日志事实验证。

## AI 补充

- 未启用或未生成 AI 诊断叙写

## 检索与关联线索

### 检索关键词

- 美元熔断
- UsdFuseService
- BIG_TYPE_USD
- isOfficialPaymentTriggered
- isUsdExchangeTriggered
- isTriggerMaxThreshold
- officialFuseTriggered
- formatCacheKey
- getRestoreService
- OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED
- ERR_USD_EXCHANGE_FUSE_TRIGGERED
- 官方提现暂不可用
- 美元兑换暂不可用

### 符号

- method UsdFuseService::isTriggerMaxThreshold waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:77
- method UsdFuseService::getRestoreService waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:98
- method UsdFuseService::isOfficialPaymentTriggered waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:126
- method UsdFuseService::isUsdExchangeTriggered waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:137
- method BaseFuseService::formatCacheKey waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:62
- class UsdFuseService waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:10
- method UsdFuseService::getKey waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:24
- method UsdFuseService::getFuseTypes waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:34
- method UsdFuseService::getSourceMap waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:44
- method UsdFuseService::getFuseFlag waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:60
- method UsdFuseService::fromToString waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:88
- method UsdFuseService::handelRecover waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:113
- method UsdFuseService::isTriggered waky6_miyouphp/php/services/Monitor/Fuse/UsdFuseService.php:149
- method CoinFuseService::isTriggerMaxThreshold waky6_miyouphp/php/services/Monitor/Fuse/CoinFuseService.php:141
- method CoinFuseService::getRestoreService waky6_miyouphp/php/services/Monitor/Fuse/CoinFuseService.php:162
- method DiamondFuseService::isTriggerMaxThreshold waky6_miyouphp/php/services/Monitor/Fuse/DiamondFuseService.php:123
- method DiamondFuseService::getRestoreService waky6_miyouphp/php/services/Monitor/Fuse/DiamondFuseService.php:146
- method PackGiftFuseService::isTriggerMaxThreshold waky6_miyouphp/php/services/Monitor/Fuse/PackGiftFuseService.php:67
- method PackGiftFuseService::getRestoreService waky6_miyouphp/php/services/Monitor/Fuse/PackGiftFuseService.php:80
- method StrangeFateFuseService::isTriggerMaxThreshold waky6_miyouphp/php/services/Monitor/Fuse/StrangeFateFuseService.php:64
- method StrangeFateFuseService::getRestoreService waky6_miyouphp/php/services/Monitor/Fuse/StrangeFateFuseService.php:73
- method FuseInterface::isTriggerMaxThreshold waky6_miyouphp/php/services/Monitor/Fuse/FuseInterface.php:36
- method FuseInterface::getRestoreService waky6_miyouphp/php/services/Monitor/Fuse/FuseInterface.php:55
- class UsdFuseServiceTest waky6_miyouphp/phpunit/Unit/Services/Monitor/Fuse/UsdFuseServiceTest.php:16
- method UsdFuseServiceTest::setUp waky6_miyouphp/phpunit/Unit/Services/Monitor/Fuse/UsdFuseServiceTest.php:21
- method UsdFuseServiceTest::tearDown waky6_miyouphp/phpunit/Unit/Services/Monitor/Fuse/UsdFuseServiceTest.php:27
- method UsdFuseServiceTest::testFuseTypeAndFactoryRegistration waky6_miyouphp/phpunit/Unit/Services/Monitor/Fuse/UsdFuseServiceTest.php:33
- method UsdFuseServiceTest::testSupportedConsumeSourcesProduceTheirOwnFuseFlag waky6_miyouphp/phpunit/Unit/Services/Monitor/Fuse/UsdFuseServiceTest.php:46
- method UsdFuseServiceTest::testOfficialPaymentGuardReadsConfiguredFuseFlag waky6_miyouphp/phpunit/Unit/Services/Monitor/Fuse/UsdFuseServiceTest.php:63
- method UsdFuseServiceTest::testUsdExchangeGuardReadsConfiguredFuseFlag waky6_miyouphp/phpunit/Unit/Services/Monitor/Fuse/UsdFuseServiceTest.php:72
- method UsdFuseServiceTest::testUsdGuardsFailOpenWhenRiskRedisIsUnavailable waky6_miyouphp/phpunit/Unit/Services/Monitor/Fuse/UsdFuseServiceTest.php:81
- method UsdFuseServiceTest::testUsdExchangeFuseErrorHasAllLocaleTranslations waky6_miyouphp/phpunit/Unit/Services/Monitor/Fuse/UsdFuseServiceTest.php:89
- method WithdrawalChannelConfigService::lastConfig waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:54
- method WithdrawalChannelConfigService::allConfig waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:121
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
- method WithdrawalOrderCreateService::calcOfficialSettlement waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:401
- method WithdrawalOrderCreateService::officialApiChannel waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:461
- method WithdrawalOrderCreateService::assertRegionOpen waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:475
- method WithdrawalOrderCreateService::assertDiamondBalance waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:493
- method WithdrawalOrderCreateService::assertNotFrequent waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:518
- method WithdrawalOrderCreateService::assertNotFrequentByRedis waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:532
- method WithdrawalOrderCreateService::buildOfficialSnapshot waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:557
- method WithdrawalOrderCreateService::freezeAndCreate waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:579
- method WithdrawalOrderCreateService::publishDiamondRiskFlow waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:713
- method WithdrawalOrderCreateService::diamondFlowTypeOf waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:751
- method WithdrawalOrderCreateService::publishOrderCreatedEvent waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:769
- class WithdrawalChannelConfigService waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:30
- method WithdrawalChannelConfigService::getConfigs waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:37
- method WithdrawalChannelConfigService::userScope waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:209
- method WithdrawalChannelConfigService::isChannelVisible waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:222
- method WithdrawalChannelConfigService::hasMerchantFor waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:238
- method WithdrawalChannelConfigService::translateParams waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:257
- method WithdrawalChannelConfigService::countryName waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:275
- method WithdrawalChannelConfigService::countryFlag waky6_miyouphp/php/services/Diamond/WithdrawalChannelConfigService.php:283
- class BaseFuseService waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:13
- method BaseFuseService::__construct waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:40
- method BaseFuseService::setFuseFlag waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:78
- method BaseFuseService::checkFuseFlag waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:102
- method BaseFuseService::getAllFuseFlags waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:124
- method BaseFuseService::delFuseFlag waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:142
- method BaseFuseService::checkCacheKeyIsExist waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:169
- method BaseFuseService::fromToString waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:183
- method BaseFuseService::returnSuccess waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:192
- method BaseFuseService::returnFail waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:197
- method BaseFuseService::returnFuse waky6_miyouphp/php/services/Monitor/Fuse/BaseFuseService.php:202
- class CoinsMerchantControl waky6_miyouphp/php/action/CoinsMerchantControl.php:44
- method CoinsMerchantControl::__construct waky6_miyouphp/php/action/CoinsMerchantControl.php:50
- method CoinsMerchantControl::getProps waky6_miyouphp/php/action/CoinsMerchantControl.php:60
- method CoinsMerchantControl::checkProps waky6_miyouphp/php/action/CoinsMerchantControl.php:68
- method CoinsMerchantControl::getPropsAddLogsList waky6_miyouphp/php/action/CoinsMerchantControl.php:108

### 配置线索

- sms.countryList waky6_miyouphp/php/action/CoinsMerchantControl.php:544
- coins_merchant.currency_display waky6_miyouphp/php/action/CoinsMerchantControl.php:649
- currency. waky6_miyouphp/php/action/CoinsMerchantControl.php:665
- coins_merchant.verified_expire waky6_miyouphp/php/action/CoinsMerchantControl.php:721
- adjust.rate waky6_miyouphp/php/action/CoinsMerchantControl.php:1375
- currency. waky6_miyouphp/php/action/CoinsMerchantControl.php:1487
- currency. waky6_miyouphp/php/action/CoinsMerchantControl.php:1717
- adjust.rate waky6_miyouphp/php/action/CoinsMerchantControl.php:1787
- coins_merchant.lang waky6_miyouphp/php/conf/CoinsMerchantConf.php:49
- diamonds.withdrawal_limits.multiple waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:269
- diamonds.withdrawal_limits.dealer.min_diamonds waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:280
- diamonds.withdrawal_limits.official.min_diamonds waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:292
- diamonds.withdrawal_limits.official.max_diamonds waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:293
- diamonds.withdrawal_limits.official.daily_limit waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:336
- diamonds.readonly_ratios.dollar_to_diamond waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:375
- diamonds.official_api_channels waky6_miyouphp/php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php:463

### SQL 线索

- 无

## 未验证项与警告

- 代码证据不足，已跳过 AI 诊断叙写
- 未成功完成数据库、配置或日志事实验证
- 诊断警告：代码证据不足，已跳过 AI 诊断叙写

## 建议下一步

- 沿确定性事实引用的文件和行号核对运行环境是否启用了对应熔断来源。
- 使用已定位文件和行号检查条件判断、异常分支与实际返回路径。
- 在目标环境只读核对配置键的实际值是否与代码预期一致。
- 沿报告中的调用链确认错误码来源和上层调用方。
- 补充数据库、配置或日志的按需只读验证计划以确认运行态事实。
- 先处理未验证项和警告，再判断候选原因是否成立。
