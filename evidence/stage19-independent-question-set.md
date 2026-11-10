# 阶段 19 独立问题集

## 目的

本文件在运行诊断前固定问题，用于检验 RAgent 能否从真实源码中检索、解释并暴露证据缺口。
问题只根据 waky3、waky4、waky6 的业务目录和服务文件名生成，不预设业务答案、关键数值、
调用链或预期命中的实现符号。

## 选择边界

- 问题来源仅为仓库目录名、类名和文件名，观察到的是“项目存在哪个业务模块”；
- 未读取目标问题对应的方法实现来反推答案；
- 不将类名、方法名或配置键作为完整问题到答案的固定映射写入 RAgent；
- 执行后先记录系统输出，再回到源码独立复核；
- 负向控制题用于验证系统是否会在业务对象不存在时停止扩展，而不是用通用词拼凑结论。

## 固定问题

| 编号 | 项目 | 问题 | 生成依据 | 任务 ID | 状态 |
|---|---|---|---|---|---|
| Q1 | waky3 | familyOilTycoons 的 PersonalAssetLevelService 按什么条件计算个人资产等级？ | `familyOilTycoons/service/PersonalAssetLevelService.php` | `diag_6c568b812fcf4db49a440494c3e5fe9c` | 已完成 |
| Q2 | waky3 | ceremony2025Family 的 AwardQueueService 如何排队和发放奖励？ | `ceremony2025Family/service/AwardQueueService.php` | `diag_2e10e79b3fc043c986b7a41540a843d7` | 已完成 |
| Q3 | waky3 | PatronCharge 的 SettlementService 在结算时如何处理积分和奖励？ | `miyouphp/php/services/PatronCharge/SettlementService.php` | `diag_fd62f293378f40aab572322286b6ac94` | 已完成 |
| Q4 | waky4 | GiftWeekStarSettlementService 按什么条件和时间范围结算周星礼物奖励？ | `giftWeekStarNewRegion/service/GiftWeekStarSettlementService.php` | `diag_acb36319247f4b4eb3292bda2de66740` | 已完成 |
| Q5 | waky4 | giftWeekStarNewRegion 的 ClearOldRedisKeyService 清理哪些 Redis key，触发边界是什么？ | `giftWeekStarNewRegion/service/ClearOldRedisKeyService.php` | `diag_8eff414495074de3a7ca566229c7ad52` | 已完成 |
| Q6 | waky4 | happyPark 的 UserScoreService 如何累计和消耗用户积分？ | `happyPark/service/UserScoreService.php` | `diag_56a81bef02394c7fb31ffb3359c19595` | 已完成 |
| Q7 | waky6 | FuseRecoveryService 中美元熔断后的恢复需要满足哪些条件？ | `miyouadmin/admin/service/FuseRecoveryService.php` | `diag_0361cbaa7abd48d38021158449fd14b8` | 已完成 |
| Q8 | waky6 | 晨曦通行证的量子积分每日兑换上限是多少？ | 当前三个仓库目录中未观察到对应业务模块，作为负向控制 | `diag_a072b525016048278002d231548c87e3` | 已完成（零证据） |

## 执行记录

最终执行使用 `use_ai=false`，避免外部模型把检索结果叙写成不可复核的事实。任务全部达到
`COMPLETED`；Q1 至 Q7 使用 `semantic_discovery`，Q8 使用 `insufficient_support`。

| 编号 | 检索模式 | 证据 | 确定性事实 | 核心输出摘要 |
|---|---|---:|---:|---|
| Q1 | `semantic_discovery` | 64 | 12 | 资产经 `hIncrBy` 累计；按 `asset_levels.limit` 升序判断等级；`hSetNx` 保证每级奖励只发一次 |
| Q2 | `semantic_discovery` | 64 | 12 | 荣誉和等级奖励使用 Redis 队列；入队用 `lpush`，消费用 `brPop`，奖励通过消息或奖励队列派发 |
| Q3 | `semantic_discovery` | 64 | 12 | 当前活动按配置遍历；积分经 `realScore`；档位奖励调用 `grantPeriodEnd`，排名奖励交给 dispatcher |
| Q4 | `semantic_discovery` | 64 | 12 | 用 `tm-600` 找活动；仅在 `etime+300 < tm < etime+1200` 结算，并有 `send_lock` 幂等 Key |
| Q5 | `semantic_discovery` | 64 | 12 | 每天 `Hi == '0000'` 触发；按最大 vol 扫描 `_{vol}_*`；保留最近 10 期，超期 key 随机过期 1–3600 秒 |
| Q6 | `semantic_discovery` | 64 | 5 | 发送礼物时计算并增加双方积分；该类证据未见积分消耗语句，系统明确报告消耗动作缺口 |
| Q7 | `semantic_discovery` | 64 | 12 | 全局恢复锁 10 秒；排除当前记录；与其他措施共享的熔断记录只计数、不直接恢复 |
| Q8 | `insufficient_support` | 0 | 0 | 未找到可相互印证的源码业务锚点，停止扩展并要求补充业务对象、入口或符号 |

## 复核与修复

第一轮结果暴露出直接基类事实污染：Q1 引用 `ServiceHelper::getType()`，Q3 引用
`BaseCrontab::__destruct()`，Q6 引用 `ServiceHelper::getInstance/getType/getId/getCfg()`。
这些方法虽然来自真实父类，却不回答用户问题。复核后没有添加项目类名或问题映射，而是增加
通用继承证据门禁：

- 目标类已有足够证据且没有缺失动作时，不把父类事实并入结论；
- 问题明确询问累计、消耗、清理等动作，而目标类缺失该动作时，只纳入真正覆盖缺失动作的父类；
- 结论范围按最终事实实际引用的文件计算，不把“已验证但未使用”的父类写成事实来源。

修复后重放结果：

- Q1、Q3、Q6 的父类事实引用数均为 0；
- Q5 继续使用 `ClearActivityService`，保留 `expire`、保留期和过期 vol 计算；
- Q6 继续明确“未观察到消耗动作”，不推断整个项目不存在积分消耗。

逐题源码复核、阶段 18 回归和完整验证记录见
[`stage19-final-verification.md`](stage19-final-verification.md)。
