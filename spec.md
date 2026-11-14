# 阶段 37 规格：配置问答长尾修复与题库第一轮全绿

## 一、目标与边界

### 目标

以 `tests/diagnosis-test-questions.md` 的 A/D/H 共 45 题（`use_ai=false`）为验收集，
把当前 29/45 的通过数提升到全绿，且修复必须是可复用的通用能力：

1. 活动配置文件被选入证据后，必须读取完整文件窗口，不能只看命中行附近。
2. 中文营销名无法在源码中找到同名目录时，仍能按“问题中文片段 + 活动展示词 + 英文目录语义”
   选择正确活动，不新增“中文名 → 目录”硬编码表。
3. 配置数组选择必须按问题意图区分：监听类型、第 N 名、个人等级、送礼阶段、奖励引用、
   布尔开关过滤，不能用同文件名称最多或数字最多的数组抢答。
4. 奖励 `id` 必须解析到同文件奖品定义表后再渲染，不能只输出 id 或渲染无关等级。

### 边界

- 结论只输出源码字面值；不输出密钥、令牌、密码、URL、内网地址、文件路径或翻译键。
- 不写题目 → 答案映射，不写活动中文名 → 目录的固定字典；只允许通用双语词根
  （`新版 → new`、`旧版 → old`）和通用配置键别名。
- 不提交 Git，阶段完成后等待用户审阅。

## 二、已确认根因（来自 `/tmp/diag-round1.json` 与源码核对）

1. **完整配置窗口未生效（首要根因）**
   `app/diagnosis_workflow.py` 的 `_full_config_window_files()` 返回原始大小写路径，
   而调用侧用 `evidence_key = file.casefold()` 做成员判断。驼峰目录
   （`gameRanking`、`giftWeekStarNew`、`lostCivilization`、`guildTreasureHunt`）
   因此拿到 `config.php:1-1` 的占位证据，`allow_types`、`rank_prizes`、`levels`、
   `is_can_jump_x`、`year` 都不在窗口内。直接导致 A-12、A-13、D-7、D-8、D-12、
   H-2、H-3、H-9、H-11 失败。
2. **同目录配置占位证据没有独立预算**
   `_sibling_config_evidence()` 只产生 `line=1`、`content="<?php"` 的占位项；
   即使进入完整窗口逻辑，也容易被 `_select_diagnostic_evidence()` 的通用预算挤掉。
3. **活动根选择缺少展示词信号**
   `scoped_config_evidence()` 只用中文锚点、概念词、术语和检索相关度评分。
   中文营销名（游戏榜、公会寻宝）在活动 `config.php` 中没有同名文本时，多个活动的
   配置会打平，被行数更多的错误活动抢占。
4. **枚举/取值选择未覆盖问法**
   - “监听哪些……类型”没有进入计数/枚举分支，落到配置取值，返回 `stime/test`。
   - “第 1 名 / 第 6 名”未命中 `rank_prizes`，被 `extra`、`prizes` 数组抢占。
   - “第 3 个个人等级门槛”被 `day_rank_top_prizes(rank=3)` 抢占。
   - `rank_prizes` 中的奖品 `id` 未解析到 `prizes[id]` 定义。
   - “哪些阶段支持跳转 X”未按 `is_can_jump_x=1` 过滤。
5. **活动服务/数据库证据缺失**
   A-18 问期数来源、A-19 问结算表与防重，当前证据没有
   `Logic/giftWeekStarNew/service/GiftWeekStarSettlementService.php`，导致空结论。

## 三、代码变更清单

### 3.1 `app/diagnosis_workflow.py`

- `_full_config_window_files(question, evidence)`
  - 返回值统一归一化：`path.replace("\\", "/").casefold()`，与调用侧
    `evidence_key` 同一口径；保留最多 3 个文件，优先问题英文标识命中的活动。
  - 逻辑变化：先按 `_activity_root` 分组配置，再按“问题中英文标识命中目录名 >
    证据相关度总和 > 文件数量”排序取前 3；不再无条件返回全部配置。
- `_sibling_config_evidence(...)`
  - 占位项补 `line_start=1`、`line_end=1`，保留 `type="sibling_config"` 标记；
    仅在活动目录确实存在 `config.php` 时加入。
- `_select_diagnostic_evidence(...)`
  - 新增 `sibling_config` 独立预算（最多 6 条），在通用预算之前保留，
    避免占位配置被 64 条上限挤掉。
- 新增 `_activity_service_evidence(root, project, question, evidence, limit=3)`
  - 当问题命中活动目录、且证据里只有 `config.php` 时，按真实目录补入
    `service/*.php`、`controller/*.php`、`*Boot.php`（最多 3 个），供 A-18/A-19
    读取数据库表名、`existsByVol` 等确定性事实；不读取数据库内容。

### 3.2 `app/domain/diagnosis/analyzers/configuration.py`

- `_scope_identifier(question, lines)`
  - 保持现有英文目录匹配；新增：当问题含“新版/旧版”时，把 `新版→new`、`旧版→old`
    作为通用目录语义提示，参与目录匹配打分。
- 新增 `_CHINESE_DIRECTORY_HINTS`（仅 `新版/new`、`旧版/old` 两组通用词根）。
- 新增 `_activity_display_terms(lines) -> Dict[str, Tuple[str, ...]]`
  - 从每个活动根的证据行中抽取面向用户的展示词：`title`、`app_nick`、`pushmsg`
    中文串、`name.zh`、同行/上一行中文注释；只保留 2-40 字中文片段，过滤 URL、
    路径、翻译键、密钥。用于把“公会寻宝”匹配到含“寻宝/公会夺宝赛”的活动。
- 新增 `_activity_alias_score(question, root_lines) -> int`
  - 用问题中文双向片段（2-6 字）与活动展示词、目录名、英文配置键做重合打分；
    四字以上完整片段权重更高；不做任何活动专属映射。
- `scoped_config_evidence(...)`
  - 评分改为 `anchor_score + alias_score + concepts + config/other 完整度 +
    term_hit + relevance`；`alias_score` 仅用于业务相关度，不直接生成事实。
  - 保留 `qualified` 规则：只有配置文件的目录必须命中中文展示词或术语。
- `_dominant_activity_lines(...)`
  - 同步加入 `_activity_alias_score`，与 `scoped_config_evidence` 同一口径。

### 3.3 `app/domain/diagnosis/config_graph.py`

- `_KEY_INTENT_ALIASES` 增补通用别名：
  - `类型/监听/发奖 → type/types/allow_types/allow`
  - `等级/档位/阶段/遗迹 → levels/stage/stages/level`
  - `跳转 → is_can_jump_x/jump/jump_x`
  - `奖励/奖品/名次` 保持既有别名，并补 `rank_prizes`。
- `enumeration_facts(question)`
  - 计数/枚举门控加入“哪些、哪几、有哪几种、监听哪些”；不再要求出现数字或结构词。
  - 候选排序新增：
    - 问题意图键命中数组内键或组注释 `+30`；
    - 问题“第 N 名”命中数字键 `+25`；
    - 问题“等级/档”且组内含 `limit`、不含 `rank/top_prizes` 时 `+20`；
    - 组内成员数等于问题数量约束时 `+25`。
  - 新增引用解析 `_resolve_prize_references(entries, graph)`：
    当枚举项含 `id` 且同文件存在以该 id 为键的奖品定义表（`prizes`、`prize`、
    `goods` 等）时，把 `type/num/name/days/gid` 合并进事实；不写死表名。
- `_enumeration_candidates(...)`
  - “第 N 名”使用正则 `第\s*[一二三四五六七八九十\d]+\s*名`，且仅对
    `rank/top/prizes` 前缀组生效；`第 N 档/级/个` 仅对匿名 `levels` 组生效。
  - 问题不含“榜/排名/名次”时，排除 `day_rank*`、`top_prizes` 组。
- `_count_facts(question)`
  - “哪些/监听哪些”输出数量 + 成员取值（数字或名称，≤12 个）；
    A-12 输出 `11` 与 `allow_types` 的 11 个类型编码。
- `_render_levels(...)`
  - 支持布尔字段过滤：问题含“跳转/支持”，只渲染 `is_can_jump_x=1` 的等级，
    并输出 `name.zh`；问题含“奖励”，优先解析 `prizes` 引用。
- `parse_config_graph(...)`
  - 为匿名 `levels` 列表补充成员总数；保持 1 起连续编号派生逻辑。

### 3.4 `app/question_type.py`

- `classify_question(...)`：
  - “哪些/哪几/有哪几种/监听哪些”统一视为 `CONFIG_ENUMERATION`；
  - “第 N 名”视为枚举；点号键、反引号键仍视为 `CONFIG_LOOKUP`。

### 3.5 `tests/test_generic_config_engine.py`

新增回归（全部使用构造源码，不依赖 waky3 私有数据）：

1. 驼峰目录 `gameRanking/config.php` 完整窗口能枚举 `allow_types` 11 项。
2. `rank_prizes` + `prizes[id]` 引用解析：第 1 名 100 美元、第 6 名 80000 金币。
3. 匿名 `levels` 第 3 档 `limit` 不被 `day_rank_top_prizes(rank=3)` 抢占。
4. `is_can_jump_x=1` 只返回 3 个支持跳转阶段。
5. `giftWeekStarNew` 与 `giftWeekStar` 同时出现时，“新版”选中 `year=2026`。
6. 活动展示词评分：中文名只出现在 pushmsg 时仍能选中正确目录。

## 四、验收标准

- `/tmp/run_diag_round1.py` 重跑后：
  - A/D/H 45 题通过数 ≥ 42，并且以下题必须通过：
    A-12（11 与 allow_types）、A-13（100/80000/60000）、D-2（0.05）、
    D-7（100）、D-8（80000）、D-12（2026 与 ACTIVITY_GIFT_WEEK_STAR）、
    D-14（1500000）、H-2（200000/1000000/2000000/4000000）、
    H-3（Bubble/Room background/Entrance show）、H-9（五阶段门槛）、
    H-11（佩特拉古城/长城/吴哥窟）。
  - 剩余未通过题必须给出明确证据缺口说明，不能出现错误数值。
- `.venv/bin/python -m pytest -q` 全绿。
- `python3 scripts/check_comment_coverage.py` 通过。
- `node scripts/check_frontend_syntax.js` 通过。
- `git diff --check` 通过。

## 五、验证命令

```bash
.venv/bin/python -m pytest -q
python3 scripts/check_comment_coverage.py
node scripts/check_frontend_syntax.js
git diff --check
docker compose build api worker
docker compose up -d api worker
python3 /tmp/run_diag_round1.py
```

H-7 单独重跑并记录耗时，确认是队列超时还是配置枚举缺陷；若为超时，调整该题轮询
与队列并发，不改业务结论。

## 六、明确不做

- 不新增“活动中文名 → 目录”固定映射表。
- 不为任何单题写 if/else 或预期值。
- 不把注释中的百分比当作可执行事实。
- 不输出密钥、URL、路径、翻译键。
- 不主动 commit。

## 七、风险与回滚

- 完整配置窗口上限从“全部”收敛到 3 个，避免证据和耗时膨胀；若误伤，可回退
  `_full_config_window_files` 的排序部分，保留大小写归一化。
- `util` 新增展示词评分只在多活动并列时起作用，单活动路径行为不变。
- 所有改动集中在诊断分析层，不影响任务队列、数据库和前端；回滚按文件粒度即可。
