# 阶段 39 规格：项目活动知识库与实体链接

## 一、背景与目标

当前配置问答在“中文活动名 → 真实活动目录”这一步依赖若干分散的补丁式启发式：
`_activity_alias_score` 的中文片段打分、`_activity_structure_score` 的结构加分、
`_full_config_window_files` 的按题排序，以及散落在 `config_graph` 里的活动名推断。
这些逻辑彼此重复、相互覆盖，无法解释“为什么选中这个活动”，也无法保证未知活动
得到明确缺口。

本阶段把“活动”变成一等知识对象：离线从仓库数据构建活动知识库，在线只做一件事——
把问题链接到唯一活动；随后证据窗口优先该活动的配置与相关实现文件，事实仍由现有
确定性配置解析器读取源码字面值产生。

### 目标

1. 新增全仓活动知识库，从真实数据提取：活动目录、配置文件、中文展示名/别名、期次
   时间窗、等级表数量与字段、名次/类型/惊喜/跳转等结构标记、同活动实现文件。
2. 新增实体链接器：问题 → 唯一活动（或明确未链接），不写“中文名 → 目录”固定表，
   不写“题目 → 答案”映射。
3. 链接成功后：该活动 `config.php` 强制进入完整配置窗口，同活动入口/服务文件进入
   证据预算，供现有分析器产生事实。
4. 链接失败且问题显式点名某个中文活动时：返回明确证据缺口，禁止用其他活动数值
   抢答。
5. 24 题验收题库重跑，除“仓库中不存在中文别名”的数据缺口外全部通过。

### 边界

- 知识库只读仓库源码与语言包，不读业务数据库、Redis、日志或线上服务。
- 别名只能来自数据：目录名、配置文件中文文案（`name.zh`、`title`、`pushmsg`）、
  `php/lang/zh/*.php` 对应语言包、中文注释；不得新增活动专属硬编码字典。
- 结论只输出源码字面值；不输出密钥、令牌、URL、内网地址、文件路径或翻译键。
- 不主动 commit，阶段完成后等待用户审阅。

## 二、验收口径与已知数据缺口

24 题题库为 `tests/diagnosis-acceptance-round2.md`，验收脚本为 `/tmp/run_acceptance.py`。

- A/B/C/D 类：结论非空、无缺口、期望关键数值/日期/币种全部出现；不得出现其他活动
  的数值。
- E 类：必须返回缺口（`gap=true`）。
- **已知数据缺口 B3**：`鉴定大师` 在 `waky3` 仓库中不存在任何中文文案（已全仓检索
  `鉴定大师`、`鉴定`、`Appraiser`、`gemMaster` 的 lang/admin/SQL 均无中文名），
  因此知识库无法从数据建立该别名。本阶段要求 B3 返回明确缺口，禁止答成其他活动；
  不要求 B3 通过。目标通过数 ≥ 23/24，且唯一失败项必须是 B3 且为缺口。
- 该缺口的后续解法（不在本阶段）：用户确认反馈后把“问题中文片段 → 活动”写回知识库，
  形成可审计的学习型别名，而非代码硬编码。

## 三、数据模型

新增 `app/domain/diagnosis/activity_kb.py`，模块职责：离线构建/缓存活动记录、在线
实体链接、为链接结果选择相关实现文件。所有解析只接受注入的文件读取函数，便于单测
使用构造源码。

### `ActivityRecord`（frozen dataclass）

| 字段 | 含义 | 来源 |
| --- | --- | --- |
| `activity_id` | 活动目录名或全局配置文件名干 | 真实路径 |
| `marker` | `Logic` / `crontab` / `action_activity` / `config` / `conf` | 真实路径 |
| `config_file` | 项目内相对路径 | 真实路径 |
| `aliases_zh` | 高置信中文展示词（`title`、`name.zh`、`【】`/`#…#` 文案） | 配置正文 |
| `context_zh` | 低置信中文片段（注释与普通文案），最多 200 个 | 配置正文 |
| `aliases_en` | 目录 CamelCase 词根、英文标题词 | 目录名/配置正文 |
| `level_names` | 等级中文/英文名称 | `name.zh` / `name.en` |
| `level_count` | 等级条目数量 | `'limit'`、`LEVEL_* => [` 计数 |
| `level_key` | 等级数组键名（`level` / `levels`） | 配置正文 |
| `level_field` | 等级门槛字段（`score` / `limit` / `value`） | 配置正文 |
| `stime` / `etime` | 起止时间字面量（`YYYY-MM-DD HH:MM:SS`） | `modify()` / `getTimestamp()` |
| `has_rank_prizes` | 是否含 `rank_prizes`/`top_prizes` | 配置正文 |
| `has_allow_types` | 是否含 `allow_types` | 配置正文 |
| `has_surprise` | 是否含 `surprise` 数组 | 配置正文 |
| `has_jump_switch` | 是否含 `is_can_jump_x` | 配置正文 |
| `env_pair_count` | `getByTestMode(` 成对配置数量 | 配置正文 |
| `source_refs` | `文件:行号` 审计引用，最多 6 条 | 配置正文 |

### `ActivityLink`（frozen dataclass）

`record`、`score`、`matched_terms`、`ambiguous`（是否因分差不足放弃链接）。

## 四、代码变更清单

### 4.1 新增 `app/domain/diagnosis/activity_kb.py`

公共函数：

```python
def build_activity_knowledge(
    root: str,
    project: str,
    *,
    read_text: Optional[Callable[[str], str]] = None,
    max_activities: int = 240,
) -> Tuple[ActivityRecord, ...]

def load_activity_knowledge(
    root: str,
    project: str,
    fingerprint: str = "",
    *,
    read_text: Optional[Callable[[str], str]] = None,
) -> Tuple[ActivityRecord, ...]

def link_activity(
    question: str,
    records: Sequence[ActivityRecord],
    *,
    min_score: int = 12,
    min_margin: float = 1.3,
) -> Optional[ActivityLink]

def select_related_files(
    record: ActivityRecord,
    known_files: Sequence[str],
    *,
    limit: int = 6,
) -> List[str]
```

实现要点：

1. **发现范围**：项目根 + 一级子项目；marker 为 `Logic`、`crontab`、
   `action_activity` 下含 `config.php` 的活动目录；另含 `php/config/*.php`、
   `php/conf/*.php` 全局配置（`marker=config/conf`，`activity_id` 为文件名干）。
   跳过 `vendor`、`lib`、`tests`、`.git` 目录。
2. **读取注入**：`read_text(relative_path)` 返回 UTF-8 文本；默认实现按
   `os.path.join(root, project, *path.split("/"))` 读取，文件超过 400KB 时跳过。
3. **别名提取**：
   - 高置信：`'title'\s*=>\s*'…'`、`'app_nick'`、`'zh'\s*=>\s*'…'`、
     `【[^】]{2,20}】`、`#[^#]{2,20}#`；
   - 低置信：所有 2–40 字中文连续片段（注释/文案），上限 200；
   - 英文：目录 CamelCase 词根（长度 ≥ 4）、ASCII `title` 词；
   - 统一去掉 `%s`、`#`、空白与首尾标点；排除含 `http`、`://`、`.png`、`@` 的片段。
4. **全局配置语言包**：对 `php/config/noble.php` 这类文件，额外读取同名的
   `php/lang/zh/<stem>.php`，把中文值（如 `VIP 1`）并入 `aliases_en` 与
   `aliases_zh`，使 `VIP` 由数据建立，不写死。
5. **时间窗**：正则捕获 `'stime'`/`'etime'` 后最近一次
   `modify('YYYY-MM-DD HH:MM:SS')` 或 `getTimestamp('…')`。
6. **等级结构**：`level_count` 统计 `'limit'\s*=>` 与
   `\\Model\\\w+::LEVEL_\w+\s*=>\s*\[`；`level_key` 优先 `level`（含 `score`/`value`），
   否则 `levels`；`level_field` 优先 `score`、`value`、`limit`。
7. **缓存**：模块级 `Dict[Tuple[str, str], Tuple[str, Tuple[ActivityRecord, ...]]]`；
   `load_activity_knowledge` 仅在 `fingerprint` 非空且与缓存一致时复用，否则重建。
   单进程缓存，不写数据库。
8. **实体链接评分**（只用于选择活动，不生成事实）：
   - 目录/配置名全词命中问题：`+120`；
   - 高置信中文别名与问题的最长公共片段：`6 × 长度`（≥2 字）；
   - 低置信中文片段：`2 × 长度`（≥3 字）；
   - 英文词根命中：`+25`（长度 ≥ 4）；
   - 等级名称命中：`+40`；
   - 结构意图：等级意图且 `level_count>0` `+20`；环境意图且
     `env_pair_count>0` `+25`；时间意图且 `stime` 非空 `+30`；
     名次意图且 `has_rank_prizes` `+30`；类型意图且 `has_allow_types` `+25`；
     惊喜/区间意图且 `has_surprise` `+25`；跳转意图且 `has_jump_switch` `+25`。
   - 通用词过滤：`活动/等级/门槛/奖励/配置/时间/开始/结束/什么/多少/分别/哪些/
     所有/每个/阶段/档位/排名/名次/金币/美元/测试/正式/环境` 不参与中文别名得分。
   - 选择：`top >= min_score` 且 `top >= second * min_margin`；否则返回
     `None`，并标记 `ambiguous=True` 供审计。**分差不足必须放弃，不得猜。**
9. **相关实现文件**：
   - 活动目录：只取该目录及其 `service/`、`controller/`、`services/` 下的 `.php`，
     优先级 `*Boot.php` > `*Control.php` > `service/*` > 其他，`config.php` 排除。
   - 全局配置：文件名（basename）包含配置名干的文件，优先级
     `*Service.php` > `*Control.php` > `*Model.php` > 其他。
   - 统一排除 `vendor`、`lib`、`tests`、`phpunit` 路径。

### 4.2 `app/diagnosis_workflow.py`

1. 在 `_select_diagnostic_evidence`（约 L1512）新增两个独立预算：
   - `type == "activity_kb_config"`：最多 3 条；
   - `type == "activity_kb_entry"`：最多 6 条。
   两者均先于通用预算追加，避免占位证据被 64 条上限挤出。
2. 在 `_full_config_window_files` 调用（约 L3080）：
   - 先计算 `linked_activity`；
   - `full_config_files.add(linked_activity.config_file.casefold())`；
   - 证据读取条件（约 L3133）增加：
     `or item.get("type") == "activity_kb_config"`、
     `or item.get("type") == "activity_kb_entry"`（后者整文件读到 1001 行）。
3. 在证据后处理前（约 L3041，`annotate_hits_with_concepts` 之前）：
   - 调用 `load_activity_knowledge(settings.code_repositories_root, current["project"],
     index_prepare_summary.get("fingerprint") or "")`；
   - `link_activity(current["question"], records)`；
   - 链接成功时追加：
     - 一条 `type="activity_kb_config"`、`file=config_file`、`line=1`、
       `content="<?php"`、`score=0.9`；
     - 若干 `type="activity_kb_entry"`（`select_related_files`，最多 6 个），
       `content="<?php"`；
   - 把链接摘要写入 `report_result["retrieval_plan"]["activity_link"]`：
     `{activity_id, config_file, score, matched_terms, ambiguous, related_files}`。
4. 构建流程用 `try/except Exception` 包裹知识库构建；失败时记录
   `warnings.append("活动知识库降级: …")`，不得中断诊断。

### 4.3 `app/domain/diagnosis/analyzers/configuration.py`

新增 `activity_decision_source_facts(lines, question, activity_link)`：

- 触发词：`判定`、`逻辑`、`按什么字段`、`怎么算`、`如何计算`、`入口`、`在哪`。
- 输入：`activity_link` 提供 `activity_id`、`level_key`、`level_field`、
  `config_file`、`related_files`。
- 逻辑：在链接到的相关实现文件证据行中查找
  `Config::get('<activity_id>.<level_key>')`、`-><level_key>`、
  `$v['<level_field>']` 等读取点；取同文件最近的前置 `function <name>` 与
  `class <name>`，生成一条事实：
  `"等级判定入口为 `<Class>::<method>`，按配置 `<activity_id>.<level_key>` 的
  `<level_field>` 门槛计算。"`
- 证据引用包含读取点、方法声明、类声明三个 `文件:行号`。
- 找不到类/方法时返回空列表，由上层报告缺口；不得用其他活动的服务文件代替。

### 4.4 `app/business_analysis.py`

在 `build_deterministic_findings` 中：

1. 读取 `retrieval_plan.get("activity_link") or {}`。
2. 若 `activity_decision_source_facts` 返回事实，则作为最高优先结论分支
   （`topic="等级判定入口"`，`explicit_topic=True`）。
3. 新增“未链接活动门控”：当 `activity_link` 为空或 `ambiguous=True`，且问题命中
   `([\u4e00-\u9fff]{2,12})活动` 时，直接返回：
   - `conclusion=""`
   - `facts=[]`
   - `uncertainties=["未在项目知识库中定位到活动「X」，已停止用其他活动配置推断。"]`
   - `gap=true`
   该门控保证 B3 返回缺口而不是其他活动的等级表。
4. 门控只作用于显式点名“X活动”的问题，不影响普通代码/流程问题。

### 4.5 `tests/test_activity_kb.py`（新增）

全部使用构造源码与注入读取函数，不依赖 waky3 私有数据：

1. **别名只来自数据**：`title`、`name.zh`、`【】`、`#…#` 能生成别名；
   `鉴定大师` 这类未出现在数据中的词不会凭空产生映射。
2. **链接命中**：问题 `心动的旋律五个等级的门槛是什么？` 在两条记录中只命中含
   `#心动的旋律#` 的记录。
3. **分差不足放弃**：两条记录都有 `等级` 结构但无中文别名时返回 `None`。
4. **全局配置语言包别名**：`php/config/noble.php` + `php/lang/zh/noble.php`
   使 `VIP 3 的升级门槛` 链接到 `noble`。
5. **时间窗**：`stime`/`etime` 从 `modify('…')` 正确提取。
6. **相关文件选择**：活动目录只返回同目录 Boot/Control/service；全局配置按
   `Service > Control > Model` 排序且排除 `vendor/lib/tests`。
7. **确定性决策事实**：构造 `class S { function getLevel(){ Config::get('noble.level'); $v['score']; } }`
   证据，断言输出包含 `S::getLevel` 与 `noble.level`。

同时更新 `tests/test_generic_config_engine.py` 中受 `_full_config_window_files`
签名或行为变化影响的断言，保持既有 389 项测试全绿。

## 五、验收标准

1. `python3 -m pytest -q` 全绿（含新增 7 项知识库测试）。
2. `python3 scripts/check_comment_coverage.py` 通过。
3. `node scripts/check_frontend_syntax.js` 通过。
4. `git diff --check` 通过。
5. 重建并重启 `rag-api`、`rag-worker` 后，`python3 /tmp/run_acceptance.py`：
   - 通过数 ≥ 23/24；
   - 唯一失败项为 B3，且 `gap=true`，结论不出现任何其他活动数值；
   - A1/A3/A4/A5/A7、B1/B2/B5/B6/B7/B9、C1–C3、D1–D3、E1–E3 全部通过；
   - 不得有题目出现“期望有答案但返回缺口”（B3 除外）。
6. `progress.md` 顶部新增“2026-11-15：阶段 39 项目活动知识库与实体链接”记录，
   包含完成范围、验证结果、B3 数据缺口与下一步。

## 六、验证命令

```bash
python3 -m pytest -q
python3 scripts/check_comment_coverage.py
node scripts/check_frontend_syntax.js
git diff --check
docker compose build api worker
docker compose up -d api worker
python3 /tmp/run_acceptance.py
```

## 七、明确不做

- 不新增“中文活动名 → 目录”固定映射表；
- 不为任何单题写 if/else 或预期值；
- 不把 `_activity_alias_score` 的分数直接复制进知识库，知识库只保存数据事实；
- 不在本阶段实现“用户反馈驱动的别名学习”（B3 数据缺口的后续阶段）；
- 不删除既有分析器，只让知识库链接优先于分散的目录猜测；
- 不主动 commit。

## 八、风险与回滚

- 知识库全仓扫描只在缓存未命中时发生；单次最多 240 个活动、单文件 400KB，
  避免拖慢首个诊断请求。若耗时异常，可调低 `max_activities` 并保留缓存。
- 链接采用“最低分 + 分差”双门控，误链接风险低于现有逐题打分；出现误链接时
  可直接回退 4.2 的“证据追加 + 完整窗口”两步，不影响既有分析器。
- 决策事实分析器只在问题含判定/逻辑意图且链接成功时触发，普通配置问答行为不变。
- 所有改动集中在诊断分析层，不修改任务队列、数据库迁移与前端；按文件粒度回滚。

## 九、需确认事项

- 本轮接受 B3（`鉴定大师`）作为“仓库无中文别名的数据缺口”返回缺口，而不是
  用硬编码或猜测答成 `gemMaster`；若要求 B3 必须通过，需要额外确认是否允许
  引入“反馈别名表”或 LLM 实体链接。
