"""生成文档索引测试语料。

模块职责：
- 以固定模板生成一批中文业务 Markdown 文档，供文档分块、索引、检索和诊断证据链测试；
- 支持指定目标根目录和项目名，重复执行结果完全一致，便于回归对比；
- 仅覆盖带本脚本标记的文件，不删除用户或其他测试写入的文档。

边界与安全约束：
- 文档内容全部为合成示例，不包含真实密钥、用户数据、生产地址或业务答案映射；
- 只写入显式指定的目标目录，不扫描、不修改待诊断仓库；
- 生成的文件统一带标记注释，清理时只按标记识别，避免误删人工文档。
"""

import argparse
from pathlib import Path
from typing import List, Tuple

# 生成标记：用于识别本脚本产出的文档，清理旧语料时只处理带该标记的文件。
CORPUS_MARKER = "<!-- ragent-document-test-corpus: generated -->"
# 默认输出目录：放在测试夹具下，方便手工查看和本地索引演练。
DEFAULT_TARGET_ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def _document(title: str, keywords: str, body: str) -> str:
    """拼装一篇带标记的测试文档。

    参数:
        - `title`：文档标题，类型为 `str`，用于分块和人工识别。
        - `keywords`：检索测试关键词，类型为 `str`；中英文混合，覆盖分词场景。
        - `body`：文档正文，类型为 `str`；只包含合成示例内容。

    Returns:
        带生成标记、标题、关键词行和正文的 Markdown 文本，末尾保留换行。
    """
    return (
        f"{CORPUS_MARKER}\n"
        f"# {title}\n\n"
        f"> 检索关键词：{keywords}\n\n"
        f"{body.strip()}\n"
    )


DOCUMENTS: Tuple[Tuple[str, str], ...] = (
    (
        "README.md",
        _document(
            "测试语料说明",
            "测试语料 corpus 文档索引 document index",
            """
本目录由 `scripts/generate_document_test_corpus.py` 生成，只用于本地测试。
文档主题覆盖活动规则、VIP 等级、提现流程、设备注册、配置字段、故障排查、
部署回滚、数据字典和安全基线，便于验证 Markdown 分块、中文全文检索和向量召回。

重新生成：

```bash
.venv/bin/python scripts/generate_document_test_corpus.py
```

生成结果只包含合成数据，禁止把本目录内容当作真实业务配置或线上答案。
""",
        ),
    ),
    (
        "activity/activity-rules.md",
        _document(
            "活动周期与等级奖励规则",
            "活动期次 volume year 等级 levels 门槛 limit 奖励 prizes",
            """
## 活动配置结构

活动配置位于 `crontab/<activityName>/config.php`，顶层字段 `stime`、`etime`
描述活动起止时间，`vol` 和 `year` 描述期次与年份。等级表使用 `levels` 数组，
每个等级包含 `id`、`limit`、`name.zh` 和 `prizes` 引用。

## 示例规则

- 第 1 档：门槛为 80000，奖励引用 `[110]`；
- 第 2 档：门槛为 200000，奖励引用 `[110, 120]`；
- 第 3 档：门槛为 1000000，奖励引用 `[130]`；
- 第 4 档：门槛为 3000000，奖励引用 `[140]`。

奖励定义位于同文件顶层 `prizes`，通过 `id` 引用，不要在等级条目内重复写名称。
""",
        ),
    ),
    (
        "activity/vip-levels.md",
        _document(
            "VIP 等级与升级门槛",
            "VIP 贵族 noble level score 门槛 升级",
            """
## 贵族等级配置

全局配置 `php/config/noble.php` 使用常量键映射等级，例如
`\\Model\\UserNobleModel::LEVEL_1 => [...]`。门槛字段为 `score`，
测试环境与正式环境可能通过 `getByTestMode(测试值, 正式值)` 给出成对数值。

## 查询建议

问题涉及“升级门槛”“等级奖励”时，优先读取配置中的等级键和 `score` 字段；
如果配置表无法逐级枚举，应明确返回缺口，不能回退到其他活动或奖品表的数值。

## 测试数据

等级名称示例：业余、通晓、资深人士、鉴宝专家。名称只用于展示，不参与门槛计算。
""",
        ),
    ),
    (
        "payment/withdrawal-flow.md",
        _document(
            "钻石提现流程",
            "钻石 提现 withdraw diamond 审核 到账",
            """
## 流程步骤

1. 用户提交提现申请，系统校验实名信息和当日次数；
2. 申请进入审核队列，审核通过后生成转账单；
3. 支付渠道回调成功后更新提现状态；
4. 失败时回滚冻结金额并记录失败原因。

## 关键字段

- `withdraw_id`：提现单号；
- `status`：申请、审核、转账、成功、失败；
- `diamond_amount`：申请钻石数量；
- `reviewer`：审核人标识。

## 边界说明

提现次数和金额上限由后台配置控制，测试文档不包含真实额度。排查问题时应读取
实际配置、审核日志和支付回执，不能仅凭本文档推断线上值。
""",
        ),
    ),
    (
        "payment/coin-account.md",
        _document(
            "金币与美元兑换",
            "金币 coin 美元 usd 兑换 比例 门槛",
            """
## 兑换规则

金币账户与美元账户分离记账，兑换比例由活动配置定义。常见字段包括
`rate`、`min_amount`、`max_amount` 和 `fee`。

## 一致性要求

- 兑换前后必须保持用户总资产守恒；
- 失败时冻结金额需回滚；
- 比例和门槛只以当前配置和代码字面值为准。

## 测试建议

构造小额兑换、超过上限、重复请求和渠道失败四类用例，检查幂等键与回滚记录。
""",
        ),
    ),
    (
        "device/device-registration.md",
        _document(
            "设备注册与风控",
            "设备注册 device register 设备号 风控 限频",
            """
## 注册链路

客户端启动后上报设备指纹，服务端按设备号和账号维度登记。重复注册返回已有记录，
设备号冲突时进入人工审核。

## 限频策略

同一设备号在时间窗口内最多登记若干次，超过阈值返回限流错误。限流计数存放在
Redis，异常时降级为数据库计数，不能直接放行。

## 排查要点

- 确认请求是否携带稳定设备号；
- 检查 Redis 与数据库计数是否一致；
- 检查是否存在代理或模拟器特征；
- 不要通过重置计数绕过风控。
""",
        ),
    ),
    (
        "config/config-fields.md",
        _document(
            "配置文件字段说明",
            "配置 config stime etime limit prizes level score",
            """
## 常见字段

| 字段 | 含义 | 类型 |
| --- | --- | --- |
| `stime` | 活动开始时间 | 时间戳 |
| `etime` | 活动结束时间 | 时间戳 |
| `vol` | 活动期次 | 整数 |
| `year` | 活动年份 | 整数 |
| `limit` | 等级门槛 | 数字或成对值 |
| `prizes` | 奖励引用 | 数组 |
| `score` | 贵族升级积分 | 数字或成对值 |

## 读取原则

配置可能位于 `php/config`、`php/conf`、`action_activity` 或活动目录的
`config.php`；同名字段按实际加载顺序覆盖。排查时先确认入口文件，再读取最终生效值。
""",
        ),
    ),
    (
        "support/troubleshooting.md",
        _document(
            "常见故障排查",
            "故障 排查 error timeout 队列 worker 重试",
            """
## 诊断任务长时间未完成

1. 检查 `diagnosis` 队列是否有积压；
2. 检查 Worker 健康检查和日志；
3. 检查模型或 embedding 超时配置；
4. 必要时取消任务后重试，不要重复创建大量任务。

## 索引任务失败

1. 检查仓库挂载是否只读且可访问；
2. 检查 `document_index_runs` 的失败原因；
3. 确认索引版本和 embedding 模型一致；
4. 修复后重新执行索引，旧运行记录会保留审计。

## 接口 5xx

先查看请求 ID 和结构化日志，再核对数据库连接、迁移漂移和只读验证库状态。
""",
        ),
    ),
    (
        "support/faq.md",
        _document(
            "业务常见问题",
            "FAQ 常见问题 活动时间 奖励 领取 查询",
            """
## 活动什么时候开始和结束？

以活动配置中的 `stime` 和 `etime` 代码字面值为准，不以页面文案或历史公告为准。

## 等级奖励在哪里配置？

等级表在活动配置的 `levels` 中，奖励名称在同文件顶层 `prizes` 中定义，通过 `id`
引用。没有名称的奖励不会展示内部编号。

## 为什么回答返回“证据不足”？

当问题点名的活动不存在、配置缺少对应字段，或证据窗口无法定位到真实源码时，
系统会返回缺口而不是猜测答案。

## 可以手工登记活动叫法吗？

可以。项目知识库提供人工反馈别名，把问题中的中文叫法与真实活动目录绑定，
登记会保留操作人和时间，并可随时停用。
""",
        ),
    ),
    (
        "ops/deployment.md",
        _document(
            "部署与回滚",
            "部署 deploy 回滚 rollback 迁移 migration 健康检查",
            """
## 部署顺序

1. 完成备份并校验；
2. 执行迁移检查，确认无 pending 和 drifted；
3. 应用迁移；
4. 启动 API 与 Worker；
5. 执行 readiness、烟雾测试和性能基线；
6. 观察告警和队列指标。

## 回滚原则

应用回滚使用上一个已验证镜像 digest。数据库回滚不等于应用回滚，优先以前向兼容
修复处理；确需恢复数据时，只在隔离库恢复并验证后再切换流量。

## 禁止事项

- 禁止使用 `latest` 镜像；
- 禁止修改已发布迁移文件；
- 禁止把生产 Secret 写入仓库或日志。
""",
        ),
    ),
    (
        "data/data-dictionary.md",
        _document(
            "数据字典",
            "数据字典 schema 表 字段 任务 证据 索引",
            """
## 核心表

- `diagnostic_tasks`：诊断任务、状态、问题摘要和结果引用；
- `diagnostic_evidence`：证据文件、行号和摘要；
- `document_chunks`：统一分块、哈希、向量和全文索引；
- `document_index_runs`：索引运行状态、版本和分块数量；
- `diagnosis_trace_spans`：诊断阶段耗时和状态；
- `project_activity_aliases`：人工反馈的活动叫法映射。

## 约束

- 任务和证据只保存脱敏摘要，不保存完整源码；
- 向量必须记录模型和维度，跨模型召回需标记不兼容；
- 人工别名只允许指向真实存在的活动目录。
""",
        ),
    ),
    (
        "security/security-baseline.md",
        _document(
            "安全基线",
            "安全 security 认证 API Key 只读 审计 限流",
            """
## 认证与授权

生产必须启用 API Key 认证，按项目和 `read`/`write` scope 最小授权。密钥由
Secret Manager 注入，任何出现在日志或会话中的旧密钥都必须轮换。

## 数据隔离

- 待诊断仓库只读挂载，运行用户无写权限；
- 目标业务库使用独立只读账号；
- 元数据库只保存 RAgent 自身数据；
- 日志不得包含完整密钥、用户隐私或未脱敏问题文本。

## 审计与限流

验证接口使用 Redis 共享限流；安全审计表记录认证失败、越权访问和敏感操作，
保留期限按生产配置执行。
""",
        ),
    ),
    (
        "release/release-notes.md",
        _document(
            "版本与变更记录",
            "版本 release 变更 迁移 验收 兼容",
            """
## 2026-11 文档测试批次

- 新增项目活动知识库结构化事实，支持期次、等级表和奖励引用；
- 新增人工反馈活动别名表，别名登记可审计、可停用；
- 生产预检纳入诊断追踪、项目知识库和人工别名表；
- 全新空库迁移、严格预检、烟雾测试、性能基线和备份恢复演练通过。

## 兼容说明

迁移只能前向追加，不修改已发布文件。升级前先执行迁移检查，确认待执行版本和
校验和，再按发布手册执行备份、迁移和验收。
""",
        ),
    ),
)


def generate_corpus(target_root: str, project: str = "document-test-repo") -> List[Path]:
    """生成一批文档测试语料。

    参数:
        - `target_root`：语料写入的根目录，类型为 `str`；脚本会在其下创建项目
          子目录，只写入显式文档路径。
        - `project`：项目子目录名，类型为 `str`；必须是单层目录名，不能包含
          路径分隔符或上跳片段。

    Returns:
        本次写入的文档绝对路径列表。重复执行会覆盖同名生成文件，内容完全一致。

    Raises:
        ValueError：项目名包含路径分隔符、为空或试图越界时抛出，避免写到根目录外。
    """
    cleaned_project = str(project or "").strip()
    if not cleaned_project or "/" in cleaned_project or "\\" in cleaned_project or ".." in cleaned_project:
        raise ValueError("项目名必须是单层目录名")
    root = Path(target_root).resolve() / cleaned_project
    written: List[Path] = []
    for relative, content in DOCUMENTS:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        written.append(destination)
    return written


def cleanup_stale_documents(target_root: str, project: str = "document-test-repo") -> List[Path]:
    """删除本次生成范围之外的旧测试文档。

    参数:
        - `target_root`：语料根目录，类型为 `str`。
        - `project`：项目子目录名，类型为 `str`；与生成时保持一致。

    Returns:
        被删除的旧文档路径列表。只删除带 `CORPUS_MARKER` 且不在当前文档清单中的
        Markdown 文件，不触碰人工文档或其他测试数据。

    Raises:
        ValueError：项目名非法时抛出，避免清理到根目录之外。
    """
    cleaned_project = str(project or "").strip()
    if not cleaned_project or "/" in cleaned_project or "\\" in cleaned_project or ".." in cleaned_project:
        raise ValueError("项目名必须是单层目录名")
    root = Path(target_root).resolve() / cleaned_project
    expected = {str(item[0]) for item in DOCUMENTS}
    removed: List[Path] = []
    if not root.exists():
        return removed
    for path in sorted(root.rglob("*.md")):
        relative = str(path.relative_to(root))
        if relative in expected:
            continue
        try:
            first_line = path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
        except IndexError:
            continue
        if first_line.strip() == CORPUS_MARKER:
            path.unlink()
            removed.append(path)
    return removed


def main() -> None:
    """解析命令行参数并生成或清理文档测试语料。"""
    parser = argparse.ArgumentParser(description="生成文档索引测试语料")
    parser.add_argument(
        "target_root",
        nargs="?",
        default=str(DEFAULT_TARGET_ROOT),
        help="语料根目录，默认写入 tests/fixtures",
    )
    parser.add_argument(
        "--project",
        default="document-test-repo",
        help="项目子目录名，默认 document-test-repo",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="生成前清理带标记但不在当前清单中的旧文档",
    )
    args = parser.parse_args()
    removed = cleanup_stale_documents(args.target_root, args.project) if args.clean else []
    written = generate_corpus(args.target_root, args.project)
    print(f"已生成 {len(written)} 篇文档，清理 {len(removed)} 篇旧文档")
    print(f"输出目录：{Path(args.target_root).resolve() / args.project}")


if __name__ == "__main__":
    main()
