# waky6 2025 年度金主飞船商品碎片分配证据

## 诊断信息

- 诊断项目：`waky6`
- 诊断问题：`waky6 25年年度金主活动，飞船商品碎片是如何分配的？`
- 真实任务 ID：`diag_e96896c3399541d2ae5a13ddae150de2`
- 任务状态：`COMPLETED`
- 诊断时间：2026-11-06
- AI 设置：关闭 AI 叙写，仅使用源码确定性分析
- 检索入口：`waky6_miyouactivity/crontab/ceremony2025GoldOwner/`
- 最终证据：80 条；其中 `SurprisePrize.php` 完整读取 1-183 行，`SpaceshipOrdinaryPrize.php` 完整读取 1-106 行

## 结论

飞船普通商品碎片先按飞船等级确定该等级总碎片，并优先发放固定排名奖励。固定榜发放完成后，以剩余碎片为基数，按 60% 进入保底池、40% 进入惊喜池；保底池由固定榜之后的剩余用户分配，惊喜池由从该等级全部用户中随机、不重复抽取的用户分配。

固定榜奖励与保底奖励互斥；惊喜奖励单独追加，因此固定榜用户或保底用户都可能同时获得惊喜碎片。

## 等级配置与固定榜

| 飞船等级 | 普通商品碎片总量 | 固定排名奖励 | 固定榜合计 | 扣除固定榜后的剩余基数 | 60% 保底池 | 40% 惊喜池 |
|---:|---:|---|---:|---:|---:|---:|
| 1 | 4,000 | 1-5 名每人 100 | 500 | 3,500 | 2,100 | 1,400 |
| 2 | 8,000 | 1-5 名每人 150 | 750 | 7,250 | 4,350 | 2,900 |
| 3 | 12,000 | 1-5 名每人 200；6-10 名每人 100 | 1,500 | 10,500 | 6,300 | 4,200 |
| 4 | 16,000 | 1-5 名每人 250；6-10 名每人 150 | 2,000 | 14,000 | 8,400 | 5,600 |

表中保底池和惊喜池是配置文件比例乘以剩余基数后的理论池大小。实际人均分配还会受参与人数和整数除法余数影响。

证据：

- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/config.php:174`：等级 1 普通商品碎片总量为 4,000；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/config.php:177`：等级 1 的 1-5 名每人 100；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/config.php:212`：等级 2 普通商品碎片总量为 8,000；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/config.php:215`：等级 2 的 1-5 名每人 150；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/config.php:250`：等级 3 普通商品碎片总量为 12,000；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/config.php:253-254`：等级 3 的 1-5 名每人 200，6-10 名每人 100；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/config.php:289`：等级 4 普通商品碎片总量为 16,000；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/config.php:292-293`：等级 4 的 1-5 名每人 250，6-10 名每人 150。

## 固定榜基数和剩余碎片

`SpaceshipService::ordinaryTopFixed()` 对每个固定排名区段计算：

```text
区段固定碎片 = (结束名次 - 开始名次 + 1) × 每名碎片
固定榜总量 = 各区段固定碎片之和
```

随后在 `SpaceshipService::ordinaryPrizes()` 中计算：

```text
剩余基数 = max(0, 等级总碎片 - 固定榜总量)
```

证据：

- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/SpaceshipService.php:445`：固定档按人数乘以每人碎片计算；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/SpaceshipService.php:513`：从等级总碎片中扣除固定榜总量。

## 保底池

保底池计算与发放顺序如下：

```text
保底池 = intval(剩余基数 × 60 / 100)
剩余用户数 = 该等级总用户数 - 固定榜最后一名
理论人均保底 = intval(保底池 / 剩余用户数)
```

- 理论人均保底大于 0 时，按理论人均向下取整分配，整数除法余数并入惊喜池；
- 理论人均保底小于或等于 0 时，改为每人 1 个碎片，按保底池数量发完即止；
- 保底只面向固定榜最后一名之后的用户。

证据：

- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/SpaceshipService.php:522`：按 60% 计算保底池；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/SpaceshipService.php:527`：保底池按剩余用户数计算人均值；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/SpaceshipService.php:529-536`：人均不足 1 时改为每人 1，否则计算取整余数并转入惊喜池。

## 惊喜池

```text
惊喜池初值 = intval(剩余基数 × 40 / 100)
惊喜池最终值 = 惊喜池初值 + 前序取整余数 + 保底取整余数
```

惊喜配置在三个等级段中完全相同：

| 惊喜档 | 用户比例 | 碎片比例 | 本档人数 |
|---|---:|---:|---|
| 1 | 10% | 30% | `intval(10 × 总用户数 / 100)` |
| 2 | 20% | 50% | `intval(20 × 总用户数 / 100)` |
| 3 | 30% | 最后一档剩余碎片 | `intval(30 × 总用户数 / 100)` |

每档先向下取整计算人均碎片；档内有差额时，前若干名补 1，本档最后一名收口剩余碎片。

证据：

- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/SpaceshipService.php:547-551`：按 40% 计算惊喜池并吸收剩余碎片；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/SpaceshipService.php:562-569`：计算每档用户数、碎片数和人均值；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/config.php:184-186`：等级 1 的三档比例；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/config.php:222-224`：等级 2 的三档比例；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/config.php:261-263`：等级 3 的三档比例；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/config.php:300-302`：等级 4 的三档比例。

## 随机不重复发放

惊喜用户不是按排名顺序发放。实现先从 `1..总用户数` 生成完整名次序列，执行 `shuffle()` 打乱，再截取本等级需要的惊喜用户数量；档内再按顺序写入随机名次。

```php
$indexes = range(1, $totalUsers);
shuffle($indexes);
return array_slice($indexes, 0, $count);
```

证据：

- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/SurprisePrize.php:96`：生成本等级全部惊喜档所需的随机名次；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/SurprisePrize.php:127`：把碎片数据绑定到随机名次；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/SurprisePrize.php:162-164`：`range + shuffle + array_slice` 保证抽取名次不重复。

因为随机池包含该等级全部用户，固定榜用户也可能同时被抽中惊喜奖。

## 固定榜、保底与惊喜的叠加关系

`SpaceshipOrdinaryPrize::getPrizes()` 先读取固定榜碎片；只有固定榜碎片小于或等于 0 时才读取保底碎片，最后无条件读取惊喜碎片。

因此：

- 固定榜奖励与保底奖励互斥；
- 惊喜奖励独立追加；
- 固定榜用户、保底用户都可能再获得惊喜碎片。

证据：

- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/SpaceshipOrdinaryPrize.php:98`：先读取固定榜碎片；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/SpaceshipOrdinaryPrize.php:99-101`：固定榜大于 0 时跳过保底；
- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/SpaceshipOrdinaryPrize.php:103`：惊喜碎片单独计算并追加。

## 四级以上飞船

配置显式列出等级 1 至 4。超过最高等级时，读取等级 4 的完整配置并复制，之后只改写等级 ID 和升级所需燃料值。因此四级以上沿用四级普通商品碎片模板，不会继续增加普通商品碎片总量。

证据：

- `waky6_miyouactivity/crontab/ceremony2025GoldOwner/SpaceshipConfig.php:134-136`：超过最高等级时复制等级 4 配置。

## RAgent 无法回答的根因与修复

### 根因

1. 原检索把中文问题中的业务名词和动作词拆得不够稳定，部分长片段与“已分配”等语法词混合，削弱了业务召回；
2. 项目权重只识别通用 `php/` 目录，未识别真实生产活动目录 `waky6_miyouactivity/crontab/`；
3. 首次真实修复后，配置和计算公式已经召回，但证据上限内只命中了 `ceremony2025GoldOwnerBoot.php` 与 `require_file.php` 中的类名引用；
4. 类声明同时被文本搜索和符号索引命中时，旧去重逻辑先保留普通文本项，随后同名引用文件按路径排序靠前，最终挤掉了 `SurprisePrize.php` 和 `SpaceshipOrdinaryPrize.php` 的完整类定义；
5. 缺少这两个类体后，确定性分析无法证明随机发奖和固定/保底互斥，答案只能停在等级总量与 60/40 公式。

### 修复

- `app/code_tools.py`：归一化 `25年`、拆分中文业务子句、生成“年度金主活动 / 飞船商品碎片 / 分配”检索片段，并提升 `miyouactivity` 活动目录权重；
- `app/retrieval_planner.py`：识别活动目录，优先发现同目录真实类，过滤嵌套泛词和低价值重复命中；
- `app/worker.py`：增加通用召回预算、方法体扩展和精确类定义证据；同一文件行去重时优先保留 `symbol`，最终每个查询词优先选择类/方法声明；
- `app/business_analysis.py`：新增飞船普通商品碎片确定性分析，覆盖等级配置、固定榜、保底、惊喜、随机抽取、互斥叠加和高等级模板；
- `tests/test_code_tools.py`、`tests/test_retrieval_planner.py`、`tests/test_worker_governance.py`、`tests/test_business_analysis.py`：增加真实问题分词、活动目录发现、类定义证据、方法体扩展和完整业务事实回归。

## 验证结果

- 修复前真实任务：仅回答等级总量、60/40 拆分、保底和惊喜比例，缺少“随机不重复”和“固定/保底互斥、惊喜可叠加”；
- 修复后真实任务 `diag_e96896c3399541d2ae5a13ddae150de2`：返回 8 类确定性事实，其中包括“惊喜随机发放”和“领奖叠加规则”；
- 真实证据确认：
  - `SurprisePrize.php`：证据类型 `symbol`，读取 1-183 行；
  - `SpaceshipOrdinaryPrize.php`：证据类型 `symbol`，读取 1-106 行；
- 全量测试：`202 passed`；
- 注释覆盖、Python 编译、前端 JavaScript 语法、Compose 配置解析和 `git diff --check` 均通过；
- 数据库、配置和日志运行态验证未请求，因此报告置信度为中；碎片规则本身由源码证据确认，具体个人所得仍取决于实时参与人数和随机结果。

## 保留的不确定性

- 本结论回答的是规则，不是某个用户在某一轮的固定所得；个人所得需要该等级实际参与人数、固定榜范围、整数余数和随机中签名次；
- 四级以上使用四级普通奖励模板，不会因为飞船等级继续提高而增加普通商品碎片总量；
- 当前诊断没有连接 waky6 生产业务库或线上 Redis，因此不证明本轮活动实际参与人数和历史发奖记录。
