-- ============================================================
-- ops-agent RBAC 种子数据一次性清理（2026-08-08）
-- 目标：切换为新的角色体系后，清掉旧绑定关系，让 DataInitializer 重建：
--   ADMIN    = 全部权限
--   OPERATOR = 业务读写（dataset/model/training/serving），无后台管理
--   READONLY = 业务只读（原 USER 角色改名）
--   演示用户 admin -> ADMIN，user -> OPERATOR
-- 用法：手动执行一次（docker exec ops-agent-postgres psql ...），然后部署 admin
-- 幂等：可重复执行
-- ============================================================

-- 1) 清空角色-权限、用户-角色绑定（DataInitializer 会按新常量收敛/重建）
DELETE FROM user_roles;
DELETE FROM role_permissions;

-- 2) 删除旧 USER 角色（新代码将重建为 READONLY）
DELETE FROM roles WHERE name = 'USER';

-- 3) 删除演示用户前，先把业务表里的操作人引用置空（用户 id 重建后会变化）
UPDATE model_versions   SET trained_by = NULL;
UPDATE training_jobs    SET triggered_by = NULL;
UPDATE serving_endpoints SET deployed_by = NULL;

-- 4) 删除演示用户（DataInitializer 下次启动按新角色体系重建 admin/user）
DELETE FROM users WHERE username IN ('admin', 'user');

-- 5) 仅保留 ADMIN / OPERATOR 角色（其余角色若存在一并清理，如旧的 USER 已在上方删除）
DELETE FROM roles WHERE name NOT IN ('ADMIN', 'OPERATOR');

-- 6) 校验（执行后应只剩 2 个角色；部署重启后 DataInitializer 会补上 READONLY 与两个演示用户）
SELECT name, description FROM roles ORDER BY name;
