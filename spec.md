# 阶段 41 规格：可审计的反馈活动别名表

## 一、背景与目标

阶段 39/40 已把“问题 → 活动”的实体链接统一收敛到活动知识库，别名只允许来自
仓库真实数据：目录名、配置中文文案、语言包值。这条约束避免了“题目 → 答案”硬编码，
但也留下一个真实缺口：

- B3「鉴定大师活动四个等级的中文名称和门槛分别是什么？」在 waky3 仓库的配置、
  语言包、后台和 SQL 中都不存在“鉴定大师”文案，知识库无法派生别名，只能返回缺口；
- 业务人员明确知道该活动对应仓库中的某个活动目录（例如 `gemMaster`），但这种
  人工知识没有可审计的落库位置，只能靠改代码或改提示词，无法评审、无法回滚。

本阶段新增“可审计的人工反馈别名表”：由授权用户把“问题中的中文叫法”显式绑定到
一个真实存在的活动目录，写入独立数据表，作为活动链接的确定性输入参与诊断；所有
记录保留操作人、说明、时间与状态，并可停用。别名只来自人工登记，不写入代码常量。

### 目标

1. 新增 `project_activity_aliases` 表与迁移，按项目隔离，记录别名、目标活动目录、
   状态、来源、说明、创建/更新操作人和时间。
2. 新增存储模块 `app/activity_aliases.py`：别名规范化与校验、登记（幂等覆盖）、
   列表、启用/停用、读取已启用别名映射；数据库异常一律降级为空结果。
3. 活动链接接入：`link_activity` 接受已启用别名映射，问题中出现该别名时把映射的
   活动作为确定性链接结果；映射目标在当前知识库不存在时忽略该别名，回退启发式。
4. API：新增登记、列表、状态变更和候选活动查询接口，全部校验项目访问权限，
   登记时校验目标活动目录确实存在于当前知识库。
5. 前端：在“项目知识库”面板增加“人工反馈别名”区块，支持查询候选活动、登记别名、
   查看已登记别名并启用/停用；不改变现有诊断交互。
6. 验收：登记“鉴定大师 → gemMaster”后，24 题验收中 B3 从缺口转为可答；
   其余 23 题不回归；全部单测与质量脚本通过。

### 边界

- 别名只允许中文业务叫法（2 至 40 字），拒绝通用问句词（活动、等级、门槛等），
  避免把任意问题强行绑定到某个活动；
- 目标活动目录必须能在当前项目活动知识库中找到；知识库不可用时拒绝登记并返回
  503，不做“先写入再校验”；
- 别名映射只影响“链接到哪个活动”，不改变活动内部事实的解析来源，结论仍必须由
  源码证据生成；
- 停用（`rejected`）的别名不参与链接，但保留记录用于审计；
- 不新增任何活动中文名硬编码或“题目 → 答案”映射；
- 不主动 commit，阶段完成后等待用户审阅。

## 二、数据模型（`migrations/026_create_project_activity_aliases.sql`）

```sql
CREATE TABLE IF NOT EXISTS project_activity_aliases (
    id BIGSERIAL PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    alias VARCHAR(40) NOT NULL,
    activity_id VARCHAR(100) NOT NULL,
    status VARCHAR(16) NOT NULL CHECK (status IN ('approved', 'rejected')),
    source VARCHAR(16) NOT NULL DEFAULT 'feedback' CHECK (source IN ('feedback')),
    evidence TEXT,
    created_by VARCHAR(100),
    updated_by VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_project_activity_alias UNIQUE (project, alias)
);
```

- `ux (project, alias)` 保证同一叫法在项目内只能指向一个活动；重复登记按更新处理，
  并在 `updated_by/updated_at` 留下审计痕迹；
- `status` 只有启用/停用两态，停用即回滚，不做物理删除；
- 按 `(project, status)` 建索引，供诊断每次读取已启用映射。

## 三、存储模块（`app/activity_aliases.py`）

### 3.1 常量与校验

- `MIN_ALIAS_LENGTH = 2`、`MAX_ALIAS_LENGTH = 40`；
- `normalize_alias(value)`：去首尾空白、压缩空白，保留中文/字母/数字，拒绝含 URL、
  图片路径、邮箱、翻译键的值；必须含中文；长度必须在区间内；命中通用问句词
  （复用 `activity_kb.GENERIC_ZH_TERMS`）返回空字符串；
- `normalize_activity_id(value)`：去空白，长度 1 至 100，只允许字母、数字、
  `_ . -`，防止把路径或注入内容写进库。

### 3.2 存储函数

| 函数 | 职责 | 失败语义 |
| --- | --- | --- |
| `upsert_activity_alias(...)` | 登记或更新别名，状态置为 approved | 返回行字典；异常向上抛出，由 API 转 503 |
| `list_activity_aliases(pool, project, status, limit)` | 按状态列出别名记录 | 时间字段由调用方序列化 |
| `set_activity_alias_status(...)` | 启用/停用指定记录 | 记录不存在返回 None |
| `load_approved_activity_aliases(pool, project)` | 返回 `别名 → 活动目录` 映射 | 数据库异常记录日志并返回空字典，不中断诊断 |

`load_approved_activity_aliases` 只返回 `status='approved'` 的记录；同名冲突由唯一
约束保证不会出现。

## 四、活动链接接入（`app/domain/diagnosis/activity_kb.py`）

### 4.1 `ActivityLink` 扩展

新增字段 `feedback_alias: str = ""`，记录本次链接是由哪条人工登记别名触发的，
供 `retrieval_plan.activity_link` 审计；无别名时为空。

### 4.2 `link_activity` 新增参数

```python
def link_activity(
    question: str,
    records: Sequence[ActivityRecord],
    *,
    min_score: int = 12,
    min_margin: float = 1.3,
    approved_aliases: Optional[Mapping[str, str]] = None,
) -> Optional[ActivityLink]:
```

处理顺序：

1. 先做人工别名匹配：把问题去掉空白后，查找命中的已启用别名，取最长者；
2. 最长别名唯一指向一个活动且该活动在 `records` 中存在 → 直接返回
   `ActivityLink(score=200+len(alias)*8, matched_terms=(alias,), feedback_alias=alias)`，
   不再参与启发式分差判定；
3. 最长别名同时指向多个不同活动（长度相同）→ 返回第一名记录但
   `ambiguous=True`，调用方必须按未链接处理；
4. 映射目标不在当前知识库（活动已删除或目录改名）→ 忽略该别名，继续原有启发式；
5. 没有任何别名命中 → 完全保持阶段 40 的打分、分差和歧义逻辑。

## 五、工作流接入（`app/diagnosis_workflow.py`）

在活动知识库链接前读取已启用别名：

```python
try:
    approved_aliases = await load_approved_activity_aliases(pool, current["project"])
except Exception as exc:  # 存储函数内部已降级，此处只作兜底
    approved_aliases = {}
    warnings.append(f"活动别名降级: {type(exc).__name__}")
linked = link_activity(
    current["question"],
    activity_records,
    approved_aliases=approved_aliases,
)
```

`activity_link_payload` 新增 `feedback_alias` 字段（无则为空字符串）；其余结构
化事实、证据预算和业务分支保持不变。

## 六、API（扩展 `app/knowledge_api.py`）

路由前缀沿用 `/api/knowledge`：

| 方法与路径 | 职责 |
| --- | --- |
| `POST /{project}/activity-aliases` | 登记别名；校验别名格式、目标活动存在与项目权限；201 返回记录 |
| `GET /{project}/activity-aliases?status=&limit=` | 列出别名记录，默认全部状态 |
| `POST /{project}/activity-aliases/{alias_id}/status` | 启用/停用；body 只接受 approved/rejected |
| `GET /{project}/activity-candidates?q=&limit=` | 按关键字列出候选活动目录、配置路径与前几个中文别名，供登记时选择 |

登记校验流程：

1. `require_project_access`；
2. `normalize_alias` / `normalize_activity_id`，非法输入返回 422；
3. 读取项目符号索引指纹，调用 `load_activity_knowledge(root, project, fingerprint)`；
4. 知识库为空返回 503（无法校验目标活动）；目标活动不在记录中返回 422，
   detail 列出该项目的活动数量，避免把拼错目录写进库；
5. 写入数据库并返回记录。

## 七、前端（`static/index.html`）

在“项目知识库”折叠面板下新增 `人工反馈别名` 子区块：

- 输入框：问题中的叫法、目标活动目录、反馈说明；
- 按钮：`查询候选活动`、`登记别名`、`刷新`；
- 候选列表：活动目录、配置路径、前几个中文别名，每行可一键填入目标目录；
- 已登记列表：别名叫法、目标活动、状态、更新人/时间，提供`启用`/`停用`按钮；
- 项目切换和 `refreshProjectData` 时自动刷新别名列表与摘要。

所有新增 JavaScript 函数使用 JSDoc 说明职责、参数业务含义与返回值；不得破坏
现有内联脚本语法。

## 八、测试

### 8.1 `tests/test_activity_aliases.py`

全部使用构造数据和内存连接池替身，不访问真实仓库或数据库：

1. `normalize_alias`：合法中文别名通过；含通用词（`活动`、`等级`）、含 URL、
   空串、超长、纯英文被拒绝；
2. `normalize_activity_id`：合法目录通过；含 `/`、空格、`..`、超长被拒绝；
3. `load_approved_activity_aliases`：只返回 approved，数据库异常返回空字典并记录；
4. `upsert` / `list` / `set_status` 的 SQL 参数与返回值（用记录型替身断言）；
5. API：登记成功、非法别名 422、目标活动不存在 422、停用后可查询、项目权限
   拒绝（`require_project_access` 已有测试模式沿用）。

### 8.2 `tests/test_activity_kb.py` 扩展

1. 已启用别名命中问题 → 链接到映射活动，`feedback_alias` 等于别名，
   `ambiguous=False`；
2. 别名目标不在知识库 → 忽略别名，回退到原有逻辑（无其他信号时返回 None）；
3. 两个别名同长度指向不同活动 → 返回 `ambiguous=True`；
4. 未传 `approved_aliases` 时行为与阶段 40 完全一致。

### 8.3 真实仓库验收

1. 通过 API 登记 `鉴定大师 → gemMaster`；
2. 重跑 24 题验收，B3 通过；其余题目保持通过，总数 `24/24`；
3. 抽查 `retrieval_plan.activity_link.feedback_alias` 等于 `鉴定大师`。

## 九、验收标准

1. `.venv/bin/python -m pytest -q` 全绿；
2. `python3 scripts/check_comment_coverage.py` 通过；
3. `node scripts/check_frontend_syntax.js` 通过；
4. `git diff --check` 通过；
5. 迁移 026 纳入 `tests/test_migrations.py` 的版本连续性检查；
6. 24 题验收 `24/24`；若本地服务不可用，记录原因并用 API/单测/真实知识库抽样
   组合验收；
7. 更新 `progress.md`；不主动 commit。

## 十、明确不做

- 不允许别名指向不存在的活动目录，不做“模糊目录猜测”；
- 不从诊断反馈评论文本自动抽取别名（避免把自然语言噪声写入链接输入）；
- 不让别名覆盖活动配置事实或结论数值；
- 不改变阶段 39/40 的启发式打分、双门控和结构化事实输出；
- 不提交代码，等待用户审阅。

## 十一、风险与回滚

- 人工别名可能指向错误活动，造成“答得很快但答案错”。缓解：登记前强制校验活动
  存在、前端展示候选活动与配置路径、保留操作人和说明、可一键停用。
- 数据库不可用时诊断不读取别名，行为退回阶段 40（B3 缺口），不会把旧别名当事实。
- 回滚：停用全部别名记录即可恢复原有链接行为；删除迁移 026 不涉及其他表。
