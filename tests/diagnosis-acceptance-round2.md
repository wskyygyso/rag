# 诊断验收题库（2026-11-15 重新生成）

本批题目全部指向 `waky3` 仓库中的真实配置与活动知识库，用于验收“等级门槛可回答、
结论格式可读、结构化事实可答、无证据不乱答”四类能力。提交诊断时使用
`use_ai=false`，只校验确定性结论；AI 补充单独观察是否在缺口场景被跳过。

题库按 2026-11-15 的源码与活动知识库重新生成：

- A–E 类 24 题由 `/tmp/run_acceptance.py` 自动核对关键数值，当日结果 `24/24`；
- F 类 4 题为知识库结构化事实，逐题手工提交并核对结论与审计字段；
- G 类为人工别名审计检查，不依赖自动数值比对。

## 前置数据

B3 使用的“鉴定大师”在 waky3 仓库中没有中文文案，知识库无法从源码派生别名。
验收前必须通过项目知识库接口或前端登记一条人工反馈别名：

```bash
curl -s -X POST http://127.0.0.1:8000/api/knowledge/waky3/activity-aliases \
  -H 'Content-Type: application/json' \
  -d '{"alias":"鉴定大师","activity_id":"gemMaster","comment":"验收前置：业务确认"}'
```

该别名属于业务数据，不会随迁移或代码自动带入生产；未登记时 B3 必须按缺口返回，
不能猜测为其他活动。登记后可通过
`retrieval_plan.activity_link.feedback_alias` 确认本次链接由该别名触发。

## 通用验收规则

1. 结论必须逐条编号，每个等级一行，包含等级名称、门槛、奖励；
2. 不得出现 `id=`、`num=`、`step=`、`is_daily=`、`gid=`、`type=`、`en=`、`in=`、
   `zh=` 等原始键值倾倒，也不得输出翻译键、URL、路径或密钥；
3. 同一等级的多语言名称只保留中文可读名或活动原始名，不重复输出六种语言；
4. E 类问题必须返回明确证据缺口，结论中不得出现任何其他活动的门槛或奖励数值；
5. 缺口场景下不得继续输出“AI 补充”结论。

## A. VIP 升级活动（专项验收）

| 编号 | 问题 | 期望结论要点 | 证据位置 |
| --- | --- | --- | --- |
| A1 | VIP 升级活动每个等级的门槛是多少？测试环境和正式环境分别是多少？ | 输出 VIP 1 至 VIP 12 共 12 个等级，每级同时给出测试值/正式值：1000/1000000、2000/2000000、3000/5000000、4000/10000000、5000/20000000、6000/30000000、7000/40000000、8000/60000000、9000/100000000、10000/140000000、11000/180000000、12000/240000000 | `waky3_miyouphp/php/config/noble.php` 的 `level[*].score` |
| A2 | VIP 3 的升级门槛在测试环境和正式环境分别是多少？ | 测试 3000，正式 5000000 | `noble.php:134` |
| A3 | VIP 10 需要多少 VIP 值才能升级？ | 测试 10000，正式 140000000；结论明确说明两个环境 | `noble.php:263` |
| A4 | VIP 升级的等级判定逻辑在哪里、按什么字段计算？ | 等级由 `noble.level` 的 `score` 门槛计算，入口为 `NewNobleScoreServices::getLevel`；不能拿 `newNobleLv.prize`（升级奖励）当门槛 | `NewNobleScoreServices.php` 与 `noble.php` |
| A5 | VIP 等级名称用的是什么类型标识？ | 12 个等级依次为 ranger、knight、viscount、earl、marquis、duke、king、lv8、lv9、lv10、lv11、lv12，展示名对应 VIP 1 至 VIP 12 | `php/lang/zh/noble.php` |

## B. 等级/档位枚举（新活动，验证通用性）

| 编号 | 问题 | 期望结论要点 | 证据位置 |
| --- | --- | --- | --- |
| B1 | 魔法炼金屋任务送礼阶段共几档？每档门槛和奖励是什么？ | 共 3 档：100000 → Shining dusk*7 days；550000 → Shining Venus*7 days、King style*7 days；1500000 → Magical Alchemy House*10 days、Purple crown*10 days | `crontab/magicAlchemyHouse/config.php` |
| B2 | 魔法炼金屋第 3 档需要多少贡献值？ | 1500000 | 同上 |
| B3 | 鉴定大师活动四个等级的中文名称和门槛分别是什么？ | 业余 80000；通晓 200000；资深人士 1000000；鉴宝专家 3000000（以当前 `gemMaster/config.php` 的 `levels[*].limit` 代码字面值为准） | `crontab/gemMaster/config.php` 的 `levels` |
| B4 | 鉴定大师第 4 档奖励是什么？ | Lv.4 Appraiser - Avatar Frame (10 day) | `gemMaster/config.php:55` |
| B5 | 苍穹霸主五个等级的英文名和门槛是什么？ | Falcon egg 20000；Young falcon 150000；Mature falcon 400000；Elite falcon 1400000；Desert King 3000000 | `crontab/skyDominator/config.php:66-72` |
| B6 | 苍穹霸主升到 Desert King 能拿哪些奖励？ | LV5 Eagle - chat box(10 days)、Little Eagle-Medal(10 days)、Sky Overlord-Entrance show(10 days)、Flame Motorcycle-car(10 days) | `skyDominator/config.php` 的 `prizes[500-503]` |
| B7 | 心动的旋律五个等级的门槛和奖励是什么？ | 100000 bubble box*5 days；600000 Entrance show*5 days；1200000 Avatar frame*7 days（两个性别各一份）；2000000 Room background*7 days；3000000 Car*10 days | `crontab/loveBall/config.php` 的 `levels` |
| B8 | 心动的旋律第 3 档为什么有两个奖励？ | 该档按性别分别配置 `sex=1` 和 `sex=0` 两个奖励，并列出对应名称 | `loveBall/config.php:67` |
| B9 | 家族城堡五个等级的建设门槛和等级奖励是什么？ | 0 无奖励；1000000 → LV.2 Castle *7 days；3000000 → LV.3 Castle *7 days；6000000 → LV.4 Castle *10 days；10000000 → LV.5 Castle *10 days | `crontab/familyCastle/config.php:43-49` |
| B10 | 家族城堡第 5 档的 top1 奖励是什么？ | Car *10 days（`top1_prizes` 引用 151） | `familyCastle/config.php:48` |

## C. 名次奖励（修复回归）

| 编号 | 问题 | 期望结论要点 | 证据位置 |
| --- | --- | --- | --- |
| C1 | 游戏榜第 6 名奖励多少金币？ | 80000 金币 | `crontab/gameRanking/config.php:56` 与 `prizes[105]` |
| C2 | 游戏榜前 3 名分别奖励什么？ | 第 1 名 100 美元；第 2 名 60 美元；第 3 名 40 美元 | `gameRanking/config.php` 的 `rank_prizes` 与 `prizes` |
| C3 | 游戏榜第 10 名奖励多少金币？ | 30000 金币 | `gameRanking/config.php` 的 `prizes[109]` |

## D. 起止时间

| 编号 | 问题 | 期望结论要点 | 证据位置 |
| --- | --- | --- | --- |
| D1 | 魔法炼金屋活动什么时候开始、什么时候结束？ | 2026-10-02 05:00:00 至 2026-10-11 23:59:59 | `magicAlchemyHouse/config.php:9-10` |
| D2 | 苍穹霸主活动什么时候开始、什么时候结束？ | 2026-08-11 12:30:00 至 2026-08-19 23:59:59 | `skyDominator/config.php:11-12` |
| D3 | 心动的旋律活动什么时候开始、什么时候结束？ | 2026-05-26 10:30:00 至 2026-06-03 23:59:59 | `loveBall/config.php:11-12` |

> 注：D1 期望值按当前 `magicAlchemyHouse/config.php` 中 `stime`/`etime` 的代码
> 字面值给出（文件头注释仍保留旧日期）；验收以代码字面值为准。

## E. 缺口与不乱答（关键验收）

| 编号 | 问题 | 期望结论要点 |
| --- | --- | --- |
| E1 | 家族城堡第 99 级的建设门槛是多少？ | 返回“未找到等级 99 的配置”缺口，不得回退到整张 1-5 级表或其他活动数值，且不产生 AI 补充 |
| E2 | VIP 等级 99 的门槛是多少？ | 返回“未找到等级 99 的配置”缺口，不得回退到 VIP 1 或其他活动门槛 |
| E3 | 游戏榜第 99 名奖励多少金币？ | 返回“未找到第 99 名奖励配置”缺口，不得输出 100 美元、80000 金币等其他名次数值 |

## F. 活动知识库结构化事实（手工验收）

F 类题目验证阶段 40/41 的知识库快照：期次、年份和后台活动登记。题目不依赖
配置证据窗口是否命中，而是直接读取链接活动的结构化事实，因此可区分
“配置没读到”和“知识库没建对”。

| 编号 | 问题 | 期望结论要点 | 证据位置 |
| --- | --- | --- | --- |
| F1 | 心动的旋律活动是第几期？配置年份是哪一年？ | 第 3 期，年份 2023 | `crontab/loveBall/config.php:8-9` 的 `year/vol` |
| F2 | gemMaster 活动是第几期？配置年份是哪一年？ | 第 4 期，年份 2022 | `crontab/gemMaster/config.php:8-9` 的 `year/vol` |
| F3 | gemMaster 活动是否已在后台活动登记？后台读取哪个配置文件？ | 已登记，后台读取 `waky3_miyouactivity/crontab/gemMaster/config.php` | 后台路径规则 `crontab/<activity>/config.php` |
| F4 | 贵族 noble 配置是否已在后台活动登记？ | 未登记；返回“不在后台活动登记路径（crontab/noble/config.php 或 Logic/noble/boot/config.php）中” | 全局配置不在活动后台目录 |

F 类判定要点：

- F1/F2 结论必须同时出现期次和年份，且期次与配置字面值一致；
- F3 必须明确“已登记”并给出后台读取路径，不能只回答“存在 config.php”；
- F4 必须明确“未登记”，不能因为 `php/config/noble.php` 存在就回答已登记；
- 结构事实来自知识库快照，禁止用其他活动的 `vol/year` 或后台路径兜底。

## G. 人工别名审计（手工验收）

| 编号 | 检查项 | 期望结果 |
| --- | --- | --- |
| G1 | 登记“鉴定大师 → gemMaster”后提交 B3，检查 `retrieval_plan.activity_link` | `activity_id=gemMaster`、`feedback_alias=鉴定大师`、`ambiguous=false`、`matched_terms` 包含别名 |
| G2 | 停用该别名后重复 B3 | 链接不再由别名触发，问题按缺口返回；重新启用后恢复 |
| G3 | 尝试登记目标不存在的活动目录 | 接口返回 422，且数据库中不新增记录 |
| G4 | 尝试登记通用问句词（如“活动等级”） | 接口返回 422，避免把任意问题强制绑定到某个活动 |

## 通过标准

- A、B、C、D 类：数值逐项与期望一致，且结论满足通用规则 1-3；
- E 类：必须满足通用规则 4-5；
- F 类：结论与期望事实一致，且不得出现其他活动的期次、年份或后台路径；
- G 类：审计字段、启停行为和拒绝条件均符合期望；
- 任一题出现“未找到”但期望有答案，或出现其他活动数值，判失败；
- 题目之间不得复用同一活动结论缓存，需每题独立提交。

## 本轮自测结果

2026-11-15，`use_ai=false`，waky3 已登记前置别名：

- A–E 类 24 题自动核对：`24/24` 通过；
- 唯一曾经失败的数据缺口 B3 已通过人工别名闭环转为可答；
- F 类手工核对：
  - F1 → `活动「loveBall」为第 3 期（配置 vol=3，配置年份 2023）`；
  - F2 → `活动「gemMaster」为第 4 期（配置 vol=4，配置年份 2022）`；
  - F3 → `该活动已在后台活动登记中：后台活动名为「gemMaster」，后台读取配置 waky3_miyouactivity/crontab/gemMaster/config.php`；
  - F4 → `该活动不在后台活动登记路径（crontab/noble/config.php 或 Logic/noble/boot/config.php）中`。

## 提交与回归命令

```bash
# 单题提交
curl -s -X POST http://127.0.0.1:8000/api/diagnoses \
  -H 'Content-Type: application/json' \
  -d '{"project":"waky3","question":"VIP 升级活动每个等级的门槛是多少？测试环境和正式环境分别是多少？","use_ai":false}'

# A-E 自动回归（脚本按本文件表格解析 A-E 编号，F/G 为手工项）
.venv/bin/python /tmp/run_acceptance.py
```

批量回归脚本当前只解析 `A-E` 编号；新增 F/G 类不会改变自动统计口径，
但必须按上表逐项手工核对并记录审计字段。
