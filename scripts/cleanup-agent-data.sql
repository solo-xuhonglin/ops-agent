-- ============================================================
-- ops-agent AI Agent 数据一次性清理（2026-08-09）
-- 目标：清掉 Agent 联调/演示期间产生的任务、事件、处置建议，
--       让 agent_tasks / agent_events / agent_suggestions 回到空表。
-- 保留：agent_tools（系统必需种子，AgentToolSeeder 自动维护；
--       如需重置工具 schema 见文末"可选"段）。
-- 用法：需要时手动执行一次：
--   docker exec -i ops-agent-postgres psql -U opsagent -d ops_agent < scripts/cleanup-agent-data.sql
--   无需重启服务；agent 重连后正常。
-- 幂等：可重复执行
-- ============================================================

-- 1) 处置建议（先删，引用 task_id）
DELETE FROM agent_suggestions;

-- 2) 任务事件流
DELETE FROM agent_events;

-- 3) 任务记录
DELETE FROM agent_tasks;

-- 4) 校验（执行后应全部为 0）
SELECT 'agent_suggestions' AS table_name, count(*) AS rows FROM agent_suggestions
UNION ALL SELECT 'agent_events',       count(*) FROM agent_events
UNION ALL SELECT 'agent_tasks',        count(*) FROM agent_tasks;

-- ============================================================
-- 可选：重置 agent_tools 为种子初始状态
-- 场景：工具 schema 有变更、或 DB 中工具行与 SEED 不一致时，
--       清空后由 AgentToolSeeder 在下次 admin 启动时按 SEED 重建。
-- 默认不执行（注释状态）。
-- ============================================================
-- DELETE FROM agent_tools;
