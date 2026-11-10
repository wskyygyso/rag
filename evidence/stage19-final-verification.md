# 阶段 19 最终验证记录

- 日期：2026-11-09
- 分支：`main`
- 验证范围：waky3、waky4、waky6 真实只读仓库
- 执行方式：固定 Q1–Q8 使用 `use_ai=false`；四道阶段 18 回归同样关闭 AI 叙写
- 当前状态：代码、文档和真实任务验证完成，未提交，等待用户审阅

## 1. 验收目标

本轮不是为预设问题补充答案，而是先固定只依据目录名、类名和文件名生成的问题，运行系统后
再独立回查源码，并根据发现的通用缺陷修改实现。验收要求如下：

- 不写入“完整问题 → 类/方法/表/答案”的硬编码映射；
- 能定位显式类、排除其他模块同名类并读取真实条件、配置和动作；
- 目标类把实现委托给直接基类时，能够补充真正相关的父类证据；
- 父类通用访问器、生命周期函数和实例缓存代码不能污染业务事实；
- 证据不足时必须返回零证据和明确缺口，不能用泛词拼答案；
- 阶段 18 的提现、飞船碎片、风控负向和 waky4 钻石展示回归不能退化。

## 2. 最终任务结果

| 编号 | 最终任务 ID | 模式 | 证据 | 事实 | 批量检索 |
|---|---|---|---:|---:|---|
| Q1 | `diag_6c568b812fcf4db49a440494c3e5fe9c` | `semantic_discovery` | 64 | 12 | 28 词 / 359 候选 / 3652.10 ms |
| Q2 | `diag_2e10e79b3fc043c986b7a41540a843d7` | `semantic_discovery` | 64 | 12 | 27 词 / 500 候选 / 4262.58 ms |
| Q3 | `diag_fd62f293378f40aab572322286b6ac94` | `semantic_discovery` | 64 | 12 | 29 词 / 500 候选 / 4001.27 ms |
| Q4 | `diag_acb36319247f4b4eb3292bda2de66740` | `semantic_discovery` | 64 | 12 | 28 词 / 500 候选 / 4455.01 ms |
| Q5 | `diag_8eff414495074de3a7ca566229c7ad52` | `semantic_discovery` | 64 | 12 | 30 词 / 500 候选 / 3985.98 ms |
| Q6 | `diag_56a81bef02394c7fb31ffb3359c19595` | `semantic_discovery` | 64 | 5 | 27 词 / 140 候选 / 4989.58 ms |
| Q7 | `diag_0361cbaa7abd48d38021158449fd14b8` | `semantic_discovery` | 64 | 12 | 28 词 / 481 候选 / 4043.50 ms |
| Q8 | `diag_a072b525016048278002d231548c87e3` | `insufficient_support` | 0 | 0 | 1 词 / 0 候选 / 3617.61 ms |
| R1 | `diag_f327fd0138ad4bb2a4722657ddd85b5f` | `semantic_discovery` | 64 | 4 | 30 词 / 500 候选 / 2937.93 ms |
| R2 | `diag_8c52412d33d747c2aff88cb5553b95e6` | `semantic_discovery` | 64 | 8 | 28 词 / 167 候选 / 4434.56 ms |
| R3 | `diag_1cf8522f6e224decb2552e3006e770a9` | `insufficient_support` | 0 | 0 | 1 词 / 0 候选 / 2602.29 ms |
| R4 | `diag_4fe4614aa9b6495fb7924039446b0516` | `semantic_discovery` | 64 | 4 | 26 词 / 500 候选 / 4263.60 ms |

所有任务状态均为 `COMPLETED`。语义题证据上限保持 64，没有把更多检索词直接放大到证据上下文。

## 3. 逐题源码复核

### Q1：个人资产等级

- 系统输出：资产通过 `hIncrBy` 累计；配置按 `asset_levels.limit` 排序；达到门槛后发奖，
  `hSetNx` 保证同一用户同一等级只发一次。
- 源码复核：`PersonalAssetLevelService::init()` 读取 `asset_levels` 并按 limit 排序；
  `addAsset()` 累计资产，`newAsset < cfg['limit']` 时跳过，否则写奖励锁并发送奖励。
- 修复结果：第一轮误把 `ServiceHelper::getType()` 当成计算规则；最终任务父类事实引用为 0。

### Q2：家族奖励队列

- 系统输出：荣誉奖励和等级奖励分别进入 Redis 队列；入队使用 `lpush`，消费使用 `brPop`，
  奖励继续进入奖品队列或发送消息。
- 源码复核：`AwardQueueService::pushHonor/pushLevel` 入队，`popHonor/popLevel` 消费；
  最低贡献默认 10000，退出家族用户不发放等级奖励。
- 结论边界：队列消费是常驻循环，实际吞吐和运行态积压未在本次代码证据中验证。

### Q3：PatronCharge 结算

- 系统输出：遍历当前活动；日/周周期分支进入不同结算路径；积分经 `realScore`，
  档位奖励调用 `grantPeriodEnd`，排名奖励交给 dispatcher。
- 源码复核：`SettlementService::run()` 读取当前活动；`settleLevelField()` 与
  `settleRankField()` 分别调用积分、档位奖励和排名奖励组件；日结有 Redis 锁。
- 修复结果：第一轮误用 `BaseCrontab::__destruct()` 的耗时开关作为时间边界；最终父类事实为 0。

### Q4：周星礼物结算

- 系统输出：使用 `tm-600` 查询活动；只有 `etime+300 < tm < etime+1200` 才结算；
  使用 `send_lock` 和数据库记录防止重复。
- 源码复核：`settleByTime()`、`settle()` 和 `settleRank()` 与输出一致；排名奖励通过
  `addPrizeQueue()` 发放。
- 结论边界：活动配置的当前线上值可能来自数据库或运营配置，源码只能确认读取和判断方式。

### Q5：旧 Redis Key 清理

- 系统输出：每天 `Hi == '0000'` 触发；通过 `tm-600` 取最大 vol；扫描 `_{vol}_*`；
  默认只清理 global，保留最近 10 期，超期 key 设置 1–3600 秒随机过期。
- 源码复核：子类 `check()`、`getVol()`、`eachAllRedisKey()`；父类
  `ClearActivityService::removeRedisData()` 执行 `expire`，`getExpiredVol()` 使用
  `currentVol - reserveVolNum`。
- 继承验证：这是最终结果中唯一实际使用直接基类事实的题目，说明门禁不是简单禁用继承。

### Q6：用户积分累计与消耗

- 系统输出：`getGiftScore` 计算礼物积分；分数大于 0 时给发送者 `addScore`，并完成双方任务。
- 源码复核：`UserScoreService::handleSendGift()` 与该路径一致；目标类当前证据窗口没有
  积分扣减或消耗语句。
- 修复结果：第一轮误带 `ServiceHelper::getInstance/getType/getId/getCfg()`；最终父类事实为 0，
  并明确保留“消耗动作未观察到”的不确定性。

### Q7：美元熔断恢复

- 系统输出：全局恢复锁 Key 为 `fuse_recover_all`，有效期 10 秒；排除当前记录后，
  若措施仍被其他熔断记录引用，只计入共享数量，不进入可恢复列表。
- 源码复核：`FuseRecoveryService::acquireRecoveryLock()` 与 `buildRecoveryPlan()` 的门禁、
  排除和共享计数逻辑一致。
- 结论边界：只证明恢复计算服务，不证明后台按钮、操作权限或具体调用调度已经配置。

### Q8：负向控制

- 系统输出：`insufficient_support`，0 证据、0 事实，并要求补充具体业务对象、页面入口、
  类方法或配置键。
- 源码复核：当前三个仓库目录未观察到“晨曦通行证 / 量子积分”的可相互印证业务对象。
- 结果意义：验证系统不会用“每日、积分、上限”等泛词拼造配置值。

## 4. 阶段 18 回归

| 编号 | 问题 | 结果 |
|---|---|---|
| R1 | 钻石提现最小兑换金额与手续费 | 4 条事实：非土耳其代理美元约 400,000 钻石，土耳其约 600,000，兑金币约 400；当前链路未发现独立平台费，但不能证明线下渠道无费用 |
| R2 | 2025 年度金主飞船商品碎片分配 | 8 条事实：等级总量、固定榜、60% 保底/40% 惊喜、惊喜随机与叠加、四级以上模板 |
| R3 | 用户风控冻结阈值 | 负向控制，0 证据、0 事实 |
| R4 | waky4 钱包页钻石展示 | 4 条事实：登录入口、`Prop.getMyRebateCard.is_anchor` 显隐、钱包表余额来源和响应字段 |

R1 已独立回查 `AdvanceServices::checkAnchorCanWithdraw()`、`anchor.php` 换算配置和
`decrDiamond/addProxyUSD` 扣款链路；R2 回查年度金主配置、分配服务和惊喜奖逻辑；R4 回查
`DiamondControl`、`PropControl` 和 `DiamondModel`。回归结果与阶段 18 一致或更明确。

## 5. 本轮修复的通用问题

第一轮 Q1/Q3/Q6 暴露直接基类事实污染。修复没有加入项目类名或完整问题映射，而是增加通用门禁：

1. 根据问题中的累计、消耗、清理、写入、更新动作检查目标类代码信号；
2. 目标类证据充足且没有缺失动作时，不把父类事实并入结论；
3. 目标类缺少明确动作时，只选择真正覆盖该动作的直接基类；
4. 无明确动作但需要父类时，父类必须与问题业务锚点或语义概念相关；
5. 结论范围按最终事实实际引用的文件计算，不再把“已验证但未使用”的父类写成事实来源。

新增回归测试覆盖 ServiceHelper 访问器、BaseCrontab 析构函数、父类不包含消耗动作和无显式类名
时的结论措辞。测试只验证通用代码结构和业务语义门禁，不包含三个项目的固定类名映射。

## 6. 性能与索引

- 最终语义任务的项目索引全部命中 `redis_hit`，等待和构建耗时均为 0；
- 批量精确检索耗时约 2.9–5.0 秒，替代此前每个检索词单独全仓扫描；
- 当前三个项目完整索引规模分别约为 waky3 41,217、waky4 40,986、waky6 41,434 个符号；
- 相同代码快照不再由每个任务重复写 PostgreSQL 四万级符号；
- 负向题不请求符号索引（`cache_status=not_requested`），在支撑门禁处停止。

## 7. 最终门禁

```text
.venv/bin/pytest -q                         267 passed
python3 scripts/check_comment_coverage.py   comment coverage: ok
python3 -m compileall -q app tests scripts  ok
node scripts/check_frontend_syntax.js       inline script syntax: ok
docker compose config --quiet               ok
git diff --check                            ok
docker compose up -d --build api worker     ok
```

## 8. 已知边界与下一步

- 当前仍以 PHP 正则索引为主，动态调用、魔术方法、反射和跨仓异步链路可能漏检；
- 中文完全同义词若与源码命名差异过大，仍可能出现无支撑结果，应通过可解释的语义召回改进，
  不能为单个问句增加完整映射；
- 当前未读取生产数据库或线上日志，结果只证明代码规则和当前证据窗口；
- 正式生产交付仍受阶段 10 的域名、TLS、Secret Manager、托管数据库/Redis、监控、镜像仓库和
  上线签字阻塞；
- 下一步等待用户审阅本轮未提交改动；审阅通过后再使用中文提交说明提交。
