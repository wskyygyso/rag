# waky6 钻石兑金币规则证据

## 任务信息

- 任务 ID：`diag_3e2352801851422094dcc0f2e60efd34`
- 项目：`waky6`
- 问题：钻石兑金币的整数倍是多少？
- 环境：`development`
- AI：任务显式关闭，以下结论来自源码确定性分析

## 直接结论

- 用户钻石兑金币的数量必须是 `10,000` 钻石的整数倍。
- 单次最低兑换 `10,000` 钻石，同时必须满足 `10,000` 钻石整数倍。
- 当前配置固定按 `2` 钻石兑换 `1` 金币。
- 当前 `max_diamonds=0`，表示未设置单次钻石兑金币上限。

## 源码依据

| 文件 | 行号 | 证明内容 |
|---|---:|---|
| `waky6_miyouphp/php/config/diamonds.php` | 11 | `diamond_to_coin` 在测试和正式环境均为 `2` |
| `waky6_miyouphp/php/config/diamonds.php` | 13 | `min_diamonds=10000` |
| `waky6_miyouphp/php/config/diamonds.php` | 15 | `multiple=10000` |
| `waky6_miyouphp/php/config/diamonds.php` | 17 | `max_diamonds=0` |
| `waky6_miyouphp/php/domain/DiamondExchange/Service/DiamondExchangeService.php` | 309 | 服务端同时校验下限和整数倍 |

服务端守卫：

```php
if ($diamonds < (int)$conf['min_diamonds'] || $diamonds % (int)$conf['multiple'] !== 0) {
    \Application::stop(Lang::get('INVALID_PARAMS', 'exception'), 10003);
}
```

## 自动发现过程

问题没有配置“钻石兑金币”领域映射。系统先从中文问题生成通用业务片段，命中配置、
注释和文档后，再从 PHP 符号索引自动发现：

- `DiamondExchangeService`
- `diamonds.diamond_exchange`
- `DiamondExchangeService::exchange`
- `DiamondExchangeService::exchangeOfficial`

本轮另发现 `CoinsMerchantModel` 及其兑换候选查询方法，用于确认币商兑换链路的存在；
它们没有被当作金额规则的直接证据。

## 边界

- `max_diamonds=0` 只证明当前服务端配置未限制单次上限，不代表以后不会调整。
- 客户端档位是否同步展示仍需结合端上版本验证。
- 本文件只记录静态源码可证明的事实；未执行目标业务数据库或生产配置读取。
