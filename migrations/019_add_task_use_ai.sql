-- 允许每个诊断任务显式选择是否调用外部 LLM。
ALTER TABLE diagnostic_tasks ADD COLUMN IF NOT EXISTS use_ai BOOLEAN;

COMMENT ON COLUMN diagnostic_tasks.use_ai IS '任务级 AI 开关：true 调用、false 跳过、NULL 沿用全局配置';
