<!-- ragent-document-test-corpus: generated -->
# 配置文件字段说明

> 检索关键词：配置 config stime etime limit prizes level score

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
