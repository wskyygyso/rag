<!-- ragent-document-test-corpus: generated -->
# 活动周期与等级奖励规则

> 检索关键词：活动期次 volume year 等级 levels 门槛 limit 奖励 prizes

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
