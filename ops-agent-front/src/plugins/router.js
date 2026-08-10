import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes = [
  { path: '/login', name: 'login', component: () => import('../views/Login.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('../layouts/AdminLayout.vue'),
    redirect: '/dashboard',
    children: [
      { path: 'dashboard', name: 'dashboard', component: () => import('../views/Dashboard.vue'), meta: { title: '仪表盘' } },
      { path: 'users', name: 'users', component: () => import('../views/users/UserList.vue'), meta: { title: '用户管理', perm: 'user:read' } },
      { path: 'roles', name: 'roles', component: () => import('../views/roles/RoleList.vue'), meta: { title: '角色管理', perm: 'role:read' } },
      { path: 'permissions', name: 'permissions', component: () => import('../views/permissions/PermissionList.vue'), meta: { title: '权限管理', perm: 'permission:read' } },
      { path: 'datasets', name: 'datasets', component: () => import('../views/datasets/DatasetList.vue'), meta: { title: '数据集管理', perm: 'dataset:read' } },
      { path: 'training/jobs', name: 'training-jobs', component: () => import('../views/training/TrainingJobList.vue'), meta: { title: '训练任务', perm: 'training:read' } },
      { path: 'models', name: 'models', component: () => import('../views/models/ModelList.vue'), meta: { title: '模型管理', perm: 'model:read' } },
      { path: 'serving', name: 'serving', component: () => import('../views/serving/ServingList.vue'), meta: { title: '模型服务', perm: 'serving:read' } },
      { path: 'agent/tools', name: 'agent-tools', component: () => import('../views/agent/AgentTools.vue'), meta: { title: 'Agent 工具', perm: 'agent:read' } },
      { path: 'audit/logs', name: 'audit-logs', component: () => import('../views/audit/AuditLogList.vue'), meta: { title: '审计日志', perm: 'audit:read' } }
    ]
  },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('../views/NotFound.vue'), meta: { public: true } }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  const loggedIn = !!localStorage.getItem('token')
  if (!to.meta.public && !loggedIn) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && loggedIn) {
    return { name: 'dashboard' }
  }
  // 刷新后首次进入：用户信息未就绪时先拉取一次，
  // 避免 roles/permissions 为空导致角色丢失、权限校验误跳转。
  if (loggedIn && !auth.user) {
    try {
      await auth.fetchMe()
    } catch (e) {
      auth.logout()
      return { name: 'login', query: { redirect: to.fullPath } }
    }
  }
  // 权限校验：菜单权限
  if (to.meta.perm && !auth.hasPerm(to.meta.perm)) {
    return { name: 'dashboard' }
  }
  return true
})

export default router
