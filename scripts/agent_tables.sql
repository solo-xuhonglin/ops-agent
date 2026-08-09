-- Agent 模块 v3 表结构（2026-08-09 重构）
-- 用途：数据库表已被用户删除后的一次性重建；此后结构由 admin JPA ddl-auto 维护（DDL 归 admin）。
-- 表名统一 agent_ 前缀；worker（ops-agent-core）经 asyncpg 直连读写业务行。
-- 幂等：DROP + CREATE，仅应在迁移窗口执行。

DROP TABLE IF EXISTS agent_suggestions;
DROP TABLE IF EXISTS agent_plans;
DROP TABLE IF EXISTS agent_tasks;
DROP TABLE IF EXISTS agent_events;          -- 废弃（事件只转发不落库）
DROP TABLE IF EXISTS task_plans;            -- 旧模型废弃

-- ===== 一次规划（意图）：worker 写 =====
CREATE TABLE agent_plans (
  id              BIGSERIAL PRIMARY KEY,
  plan_id         VARCHAR(64)  NOT NULL UNIQUE,
  conversation_id VARCHAR(64)  NOT NULL,
  summary         VARCHAR(255),
  steps           TEXT,                          -- JSON 数组：步骤清单备忘（模型掌舵；每步含 status/note）
  status          VARCHAR(16)  NOT NULL DEFAULT 'PLANNED', -- PLANNED/RUNNING/DONE/FAILED/CANCELLED
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_plans_conversation ON agent_plans(conversation_id);

-- ===== 待审批写操作建议（plan 的步骤 + 审批对象）：worker 写业务，admin 写审批动作 =====
CREATE TABLE agent_suggestions (
  id              BIGSERIAL PRIMARY KEY,
  suggestion_id   VARCHAR(64)  NOT NULL UNIQUE,
  plan_id         VARCHAR(64),
  step_no         INT,
  source_task_id  VARCHAR(64),
  conversation_id VARCHAR(64)  NOT NULL,
  action_type     VARCHAR(32)  NOT NULL,
  target_type     VARCHAR(32),
  target_id       BIGINT,
  params          TEXT,
  reason          TEXT,
  priority        VARCHAR(8)   DEFAULT 'NORMAL',
  status          VARCHAR(16)  NOT NULL DEFAULT 'PENDING',
                  -- PENDING/APPROVED/REJECTED/EXECUTING/EXECUTED/FAILED/EXPIRED/CANCELLED
  retry_of        VARCHAR(64),                  -- 重试来源建议（决策轮 retry 时挂）
  grant_key       VARCHAR(128),
  confirmed_by    BIGINT,
  confirmed_at    TIMESTAMPTZ,
  executed_at     TIMESTAMPTZ,
  result          TEXT,
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_sug_conversation ON agent_suggestions(conversation_id);
CREATE INDEX idx_agent_sug_plan         ON agent_suggestions(plan_id);
CREATE INDEX idx_agent_sug_status       ON agent_suggestions(status);

-- ===== 执行记录（chat 轮 / execute 轮）：worker 写 =====
CREATE TABLE agent_tasks (
  id              BIGSERIAL PRIMARY KEY,
  task_id         VARCHAR(64)  NOT NULL UNIQUE,
  task_type       VARCHAR(24)  NOT NULL,       -- chat | execute
  plan_id         VARCHAR(64),
  suggestion_id   VARCHAR(64),
  conversation_id VARCHAR(64),
  query           TEXT,
  status          VARCHAR(16)  NOT NULL DEFAULT 'DISPATCHED', -- DISPATCHED/RUNNING/SUCCEEDED/FAILED/CANCELLED
  worker_id       VARCHAR(64),
  conclusion      TEXT,
  reasoning       TEXT,
  started_at      TIMESTAMPTZ,
  finished_at     TIMESTAMPTZ,
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_tasks_conversation ON agent_tasks(conversation_id);
CREATE INDEX idx_agent_tasks_status        ON agent_tasks(status);
CREATE INDEX idx_agent_tasks_plan          ON agent_tasks(plan_id);
CREATE INDEX idx_agent_tasks_suggestion    ON agent_tasks(suggestion_id);

-- conversations / conversation_messages 前缀迁移（旧名 → agent_ 前缀，容错：存在才改）
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_tables WHERE schemaname='public' AND tablename='conversations')
     AND NOT EXISTS (SELECT FROM pg_tables WHERE schemaname='public' AND tablename='agent_conversations') THEN
    ALTER TABLE conversations RENAME TO agent_conversations;
  END IF;
  IF EXISTS (SELECT FROM pg_tables WHERE schemaname='public' AND tablename='conversation_messages')
     AND NOT EXISTS (SELECT FROM pg_tables WHERE schemaname='public' AND tablename='agent_conversation_messages') THEN
    ALTER TABLE conversation_messages RENAME TO agent_conversation_messages;
  END IF;
END $$;
