# 阶段 40 规格：扩展活动知识库结构化事实

## 一、背景与目标

阶段 39 已把“问题 → 活动”的实体链接收敛到知识库，但知识库当前只保存“结构标记”：
等级数量、等级键、门槛字段、起止时间、是否有名次奖励表。链接成功后，具体数值仍完全
依赖 `config_graph` / 配置字面量分析器重新解析证据窗口。这带来三个问题：

1. **期次不可答**：活动配置里真实存在 `'vol' => 3`、`'year' => 2023`，但知识库没有
   保存，问“这是第几期 / 期次是多少”只能碰运气命中 `vol` 字面量。
2. **等级奖励引用不可读**：`levels[].prizes` 只是 `['id' => 10]` 或 `[110,120]`，
   指向同文件顶层 `prizes[110].name`。现有配置图对等级组不解析 `id` 引用
   （`allow_id_reference` 仅对奖品/名次组为 `True`），知识库也没有保存引用关系，
   因此“某等级奖励是什么”只能依赖证据窗口内恰好同时出现奖品定义表。
3. **后台登记缺失**：后台活动后台（`waky3_miyouadmin/admin/html/appConfig/activityBackend/
   index.php`）按固定规则解析活动：先找 `activity_php/crontab/<activityName>/config.php`，
   再找 `activity_php/Logic/<activityName>/boot/config.php`。知识库目前只扫描活动目录下的
   `config.php`，既不覆盖 `Logic/<name>/boot/config.php`，也没有“是否在后台登记”的事实。

本阶段把知识库从“结构标记”扩展为“结构化事实快照”：期次/年份、等级表（等级序号、
门槛、名称、奖励引用）、奖励定义表（id、名称、类型、数量、天数）、后台登记状态。
这些事实只来自仓库源码；在线问答仍只做“链接 → 读取该活动事实”，不新增活动名映射。

### 目标

1. 扩展 `ActivityRecord`，保存期次、年份、结构化等级表、奖励定义表、后台登记信息。
2. 支持 `Logic/<name>/boot/config.php` 作为活动配置来源，与 `crontab/<name>/config.php`
   按后台优先级去重，避免同名活动重复进入链接候选。
3. 新增确定性事实函数，把上述结构化事实变成带 `文件:行号` 引用的事实：
   - 期次：`第 N 期` + 配置年份；
   - 等级表：逐级输出名称、门槛、奖励名称（奖励引用解析到 `prizes` 定义）；
   - 后台登记：后台活动名与后台读取配置路径。
4. 问答接入：期次/后台登记问题优先使用知识库事实；等级表问题在现有配置枚举
   解析为空时，用知识库等级表兜底；不改变现有枚举通过路径。
5. 24 题验收题库保持 `≥ 23/24`，唯一失败项仍为 B3 数据缺口，不引入其他回归。

### 边界

- 只读活动 `config.php`、`php/config/*.php`、`php/conf/*.php` 与同名中文语言包；
  不读数据库、Redis、日志或线上服务。
- 不新增“中文活动名 → 目录”“题目 → 答案”硬编码；所有事实字段都来自源码字面量。
- 事实输出不包含 `gid`、`num`、`step`、`type=`、翻译键、URL、路径或密钥；
  奖励只展示定义表中的可读 `name`（没有名称时省略该奖励，不倾倒内部 id）。
- 不主动 commit；阶段完成后等待用户审阅。

## 二、数据模型变更（`app/domain/diagnosis/activity_kb.py`）

### 2.1 新增 frozen dataclass

```python
@dataclass(frozen=True)
class ActivityLevel:
    ordinal: int                 # 等级序号：优先 `id`，缺失时按数组声明顺序
    threshold: str               # 门槛展示值：`100000` 或 `测试 1000 / 正式 1000000`
    name: str                    # 等级名称：`name.zh` 或字符串 `name`，无则空
    prize_ids: Tuple[str, ...]   # 该等级引用的奖励 id（`prizes`/`top1_prizes`）
    source_ref: str              # 等级条目起始行的 `文件:行号`
```

```python
@dataclass(frozen=True)
class ActivityPrize:
    prize_id: str                # 顶层 `prizes` 映射键，同时也是其 `id`
    name: str                    # 奖励名称（配置中的 `name`）
    type: str                    # 奖励类型（仅内部保存，不进入结论）
    num: str                     # 数量（仅内部保存，不进入结论）
    days: str                    # 有效期天数（仅内部保存，不进入结论）
    source_ref: str              # 奖励定义起始行的 `文件:行号`
```

### 2.2 `ActivityRecord` 新增字段（保持现有字段不变）

| 字段 | 含义 | 来源 |
| --- | --- | --- |
| `volume` | 活动期次字面值，如 `3` | 顶层 `'vol' => N` |
| `year` | 活动年份字面值，如 `2023` | 顶层 `'year' => N` |
| `vol_ref` | `vol` 所在行 `文件:行号` | 配置正文 |
| `year_ref` | `year` 所在行 `文件:行号` | 配置正文 |
| `levels` | 结构化等级表，最多 40 条 | `'levels' => [ ... ]` / `'level' => [ ... ]` |
| `prizes` | 奖励定义表，最多 120 条 | 顶层 `'prizes' => [ id => [...] ]` |
| `backend_registered` | 是否命中后台活动解析路径 | 路径规则（见 2.4） |
| `backend_config_file` | 后台读取的配置路径；未登记为空 | 路径规则（见 2.4） |

### 2.3 解析规则（`_extract_alias_data` 内新增，纯字符扫描、不执行 PHP）

1. **期次/年份**：正则 `'vol' => 数字`、`'year' => 数字`，只接受纯整数/字符串数字；
   记录行号。只取首次顶层命中，避免把日志数组里的 `vol` 当作期次。
2. **等级表**（`levels` 数组）：
   - 用括号配对把顶层数组切成条目块（`[` 深度 1 为界），逐块解析；
   - 序号：`'id' => N`，缺失时按声明顺序 1..n；
   - 门槛：按顺序匹配 `'limit'|'score'|'value'|'need'|'require'|'gift_value'`，
     值为数字；`getByTestMode(测试, 正式)` 输出 `测试 X / 正式 Y`；
   - 名称：`'name' => '文本'` 或 `'name' => ['zh' => '文本']`，经
     `_normalize_zh_alias` 规范化；英文名保留原文本（如 `Falcon egg`）；
   - 奖励引用：条目内 `'prizes' => [...]`、`'top1_prizes' => [...]` 的所有数字，
     支持 `[110,120]` 裸数字与 `[['id' => 10]]` 两种写法；
   - 每条等级最多 12 个奖励引用。
3. **等级表**（`level` 常量键映射，贵族配置）：
   - 匹配 `::LEVEL_XXX => [ ... ]` 条目块，序号按声明顺序补 1..n；
   - 门槛取 `score|value|limit`，支持 `getByTestMode` 成对值；
   - 奖励引用同样从条目内 `prizes` 块提取。
4. **奖励定义表**：`_array_block(text, "prizes")` 后按顶层 `N => [` 切块，提取
   `id`（缺省用键）、`name`（字符串或 `name.zh`）、`type`、`num`、`days` 与起始行号；
   最多 120 条；只保存展示需要的 `name` 和审计引用，`type/num/days` 供后续扩展，
   但本阶段事实语句不输出它们。

### 2.4 后台活动登记判定（构建阶段）

后台真实规则（来自 `activityBackend/index.php#getActivityConfigPath`）：
`crontab/<activityName>/config.php` 优先，其次 `Logic/<activityName>/boot/config.php`。

- 构建时新增对 `Logic/<name>/boot/config.php` 的枚举；
- 每条记录的 `backend_registered` = 配置路径匹配
  `crontab/<activity_id>/config.php`（大小写不敏感）或
  `Logic/<activity_id>/boot/config.php`；
- 命中时 `backend_config_file = config_file`，否则为空字符串；
- **同名去重**：同一 `activity_id` 只保留优先级最高的一条记录：
  `crontab` > `Logic` > `action_activity` > `config` > `conf`。这样后台优先读取的
  `crontab` 配置不会被 `Logic/boot` 副本顶掉，也避免重复记录造成链接歧义。

### 2.5 关联文件选择

`select_related_files` 逻辑保持；对 `Logic` 来源记录，`config_file` 位于
`<activity>/boot/config.php`，现有“同目录或文件包含活动名”的规则仍然成立，
不需要额外改动。

## 三、工作流接入（`app/diagnosis_workflow.py`）

在 `activity_link_payload`（约 L2908）新增 JSON 安全字段：

```python
{
  ...,
  "volume": linked.record.volume,
  "year": linked.record.year,
  "vol_ref": linked.record.vol_ref,
  "year_ref": linked.record.year_ref,
  "levels": [
    {"ordinal": l.ordinal, "threshold": l.threshold, "name": l.name,
     "prize_ids": list(l.prize_ids), "ref": l.source_ref}
    for l in linked.record.levels
  ],
  "prizes": [
    {"id": p.prize_id, "name": p.name, "ref": p.source_ref}
    for p in linked.record.prizes
  ],
  "backend_registered": linked.record.backend_registered,
  "backend_config_file": linked.record.backend_config_file,
}
```

链接歧义时仍写入这些字段，但 `ambiguous=True`，业务层不得使用。

## 四、确定性事实（新增 `app/domain/diagnosis/analyzers/activity_kb_facts.py`）

模块职责：把 `retrieval_plan.activity_link` 中的结构化快照转成带引用的确定性事实。
纯函数、无 IO、无模型调用。

### 4.1 `activity_structured_facts(activity_link, question)`

触发条件（全部满足）：

- `activity_link` 非空、`activity_id` 非空、`ambiguous` 为 False、`available` 为 True；
- 问题命中期次意图：`第\s*\d+\s*期`、`期次`、`期号`、`第几期`、`vol` 字面量；
  或后台意图：`后台`、`登记`、`后台活动`、`后台配置`。

输出：

1. 期次意图且 `volume` 非空：
   `活动「<activity_id>」为第 <volume> 期（配置 vol=<volume>[，配置年份 <year>]）。`
   refs: `[vol_ref, year_ref]`（去空）。
2. 后台意图：
   - 已登记：`该活动已在后台活动登记中：后台活动名为「<activity_id>」，后台读取配置
     <backend_config_file>。`
   - 未登记：`该活动不在后台活动登记路径（crontab/<activity_id>/config.php 或
     Logic/<activity_id>/boot/config.php）中。`
   refs: `[backend_config_file 的首行引用或 config_file:1]`（使用记录已有 ref，
   不生成新路径常量）。

### 4.2 `activity_level_table_facts(activity_link, question)`

触发条件：链接有效、`levels` 非空，且问题含等级/档位意图
（`等级`、`档位`、`级别`、`阶段`、`门槛`、`level`）。

输出逐级事实（每级一条，最多 20 条）：

```
等级 <ordinal>（<name>）：门槛 <threshold>；奖励 <奖励名>、<奖励名>。
```

- 名称缺失时省略括号；门槛为空时省略“门槛”；两者都为空时不输出该等级；
- 奖励按 `prize_ids` 在 `prizes` 快照中查名，跳过无名奖励；全部无名时省略奖励段；
- refs 合并等级行与奖励定义行，去重后最多 6 条；
- 问题点名“第 N 档/等级”时只输出该序号（与现有枚举行为一致）；
- 问题同时含“奖励”意图且知识库没有奖励引用时，仍输出门槛事实，不编造奖励。

### 4.3 与现有枚举的关系

- 知识库等级事实**只作兜底**：`configuration_enumeration_facts` 有结果时继续用现有
  配置图事实（保证 B1/B7 等既有通过格式不变）；
- 现有流程的“等级门槛解析为空 → 缺口”判断改为：知识库等级事实存在时不返回缺口，
  改用知识库事实作答；否则维持原缺口。

## 五、`app/business_analysis.py` 接入

在 `build_deterministic_findings`：

1. `activity_link` 与 `linked_activity_resolved` 计算之后、历史事实分支之前，
   调用 `activity_structured_facts`；非空时作为独立分支
   （`topic="活动结构化事实"`，`explicit_topic=True`）。
2. 等级枚举段：
   - `enumeration_facts` 为空且 `linked_activity_resolved` 时计算
     `level_table_facts = activity_level_table_facts(...)`；
   - “门槛未解析出逐级配置”的缺口门控增加 `and not level_table_facts`；
   - 分支顺序：`enumeration_facts` → `level_table_facts`（`topic="活动结构化事实"`）
     → `execution_facts` → `env_value_facts` → `configuration_facts` → 分析器。
3. 结论：`topic == "活动结构化事实"` 时直接拼接事实 `statement`（去重），
   以“。”收尾；不追加其他活动的推断。
4. 保持“未链接活动”缺口门控和判定入口分支优先级不变；期次/后台事实仅对已链接
   活动生效，避免跨活动抢答。

## 六、测试（`tests/test_activity_kb.py` 扩展）

全部使用临时目录与构造源码，不依赖 waky3：

1. **期次与年份**：顶层 `'vol' => 2, 'year' => 2026` 解析到 `volume/year` 与行号；
   注释中的 `// 'vol' => 1` 不得被当作期次（只匹配带引号键且非注释行需要按首次
   非注释命中，测例用注释在前、真实值在后的配置验证）。
2. **等级表与奖励引用**：`levels` 含 `id/limit/name/prizes`，顶层 `prizes` 含
   `name`，断言 `levels[i].prize_ids` 与 `prizes[i].name`，并校验 `getByTestMode`
   门槛渲染为 `测试 X / 正式 Y`。
3. **等级表兜底事实**：调用 `activity_level_table_facts`，断言输出包含等级名称、
   门槛与奖励名称，且 `evidence_refs` 指向配置行。
4. **期次/后台事实**：`activity_structured_facts` 分别对“第几期”和“后台配置在哪”
   生成事实；未登记活动输出未登记语句。
5. **后台登记与去重**：同一 `activity_id` 同时存在
   `crontab/<name>/config.php` 与 `Logic/<name>/boot/config.php` 时，只保留
   `crontab` 记录且 `backend_registered=True`；仅 `Logic/boot` 时也能构建记录且
   `backend_registered=True`；全局配置 `noble` 的 `backend_registered=False`。
6. **缺失数据降级**：无 `vol/year/levels/prizes` 的配置构建不抛异常，字段为空；
   事实函数对空快照返回空列表。
7. 既有 7 项测试保持通过。

## 七、验收标准

1. `.venv/bin/python -m pytest -q` 全绿（393 + 新增测试）。
2. `python3 scripts/check_comment_coverage.py` 通过。
3. `node scripts/check_frontend_syntax.js` 通过。
4. `git diff --check` 通过。
5. 真实仓库抽样（Python 直接调用 `build_activity_knowledge`，不启服务）：
   - `loveBall`：`volume=3`、`year=2023`、`levels` 5 条（100000..3000000）、
     奖励引用可解析为 `bubble box*5 days` 等名称；
   - `gemMaster`：4 条等级（含中文名称与 80000/200000/1000000/3000000 门槛）；
     该活动仍不会通过“鉴定大师”链接（别名数据缺口不变）；
   - `noble`：`level` 12 条、`level_field=score`、门槛含测试/正式成对值；
   - `backend_registered` 对 `crontab` 活动为 True、对 `noble` 为 False。
6. 24 题验收：若本地 `rag-api`/`rag-worker` 可用，重跑 `/tmp/run_acceptance.py`，
   通过数 `≥ 23/24` 且唯一失败项为 B3；若服务不可用，用 5 中的确定性抽样 +
   全量单测作为本阶段验收，并在 `progress.md` 明确记录未跑 E2E 的原因。
7. 更新 `progress.md`：新增阶段 40 记录、阶段表与总体状态；不主动 commit。

## 八、验证命令

```bash
.venv/bin/python -m pytest -q
python3 scripts/check_comment_coverage.py
node scripts/check_frontend_syntax.js
git diff --check
# 真实仓库抽样（只读，不启服务）
.venv/bin/python - <<'PY'
from app.domain.diagnosis.activity_kb import build_activity_knowledge
records = build_activity_knowledge('/Users/wskyygyso/work/www/waky3', '')
for aid in ('loveBall', 'gemMaster', 'noble'):
    rec = next((r for r in records if r.activity_id == aid), None)
    print(aid, bool(rec), getattr(rec, 'volume', ''), len(getattr(rec, 'levels', ()) or ()), getattr(rec, 'backend_registered', ''))
PY
```

## 九、明确不做

- 不新增活动中文名硬编码或“题目 → 答案”映射；
- 不改变阶段 39 的链接打分与双门控；
- 不替换现有配置枚举渲染（知识库等级表只作兜底）；
- 不实现奖励 `gid/num/type` 的业务解释，只解析并保存展示所需字段；
- 不处理 B3 数据缺口（仍按缺口返回）；
- 不主动 commit。

## 十、风险与回滚

- `Logic/<name>/boot/config.php` 会扩大构建集合；同名去重与 `MAX_ACTIVITIES=400`
  控制规模。若出现新活动误链接，可回退 2.4 的 `Logic` 枚举，其他字段不受影响。
- 结构化事实只在已链接活动且意图命中时触发；配置枚举解析正常的问题路径不变，
  B 类验收风险低。
- 解析为纯字符扫描，异常配置缺字段时返回空值/跳过条目，不会中断构建。
- 改动按文件可独立回退：`activity_kb.py`、`activity_kb_facts.py`、
  `diagnosis_workflow.py`、`business_analysis.py`、测试与 `progress.md`。
