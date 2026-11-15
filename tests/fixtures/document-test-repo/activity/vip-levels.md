<!-- ragent-document-test-corpus: generated -->
# VIP 等级与升级门槛

> 检索关键词：VIP 贵族 noble level score 门槛 升级

## 贵族等级配置

全局配置 `php/config/noble.php` 使用常量键映射等级，例如
`\Model\UserNobleModel::LEVEL_1 => [...]`。门槛字段为 `score`，
测试环境与正式环境可能通过 `getByTestMode(测试值, 正式值)` 给出成对数值。

## 查询建议

问题涉及“升级门槛”“等级奖励”时，优先读取配置中的等级键和 `score` 字段；
如果配置表无法逐级枚举，应明确返回缺口，不能回退到其他活动或奖品表的数值。

## 测试数据

等级名称示例：业余、通晓、资深人士、鉴宝专家。名称只用于展示，不参与门槛计算。
