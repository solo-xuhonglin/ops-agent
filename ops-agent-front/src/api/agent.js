// AI Agent 管理面 API（人用）：任务派发/查看、处置建议确认/忽略
import api from '../plugins/axios'

// 派发任务：{ taskType?, targetType?, targetId?, query? } → { taskId, status }
export function dispatchTask(payload) {
  return api.post('/agent/tasks', payload)
}

// 任务列表（分页，新→旧）
export function listTasks(params = {}) {
  return api.get('/agent/tasks', { params: { page: 0, size: 20, ...params } })
}

// 任务详情（含事件流）
export function getTask(taskId) {
  return api.get(`/agent/tasks/${taskId}`)
}

// 处置建议列表（分页）
export function listSuggestions(params = {}) {
  return api.get('/agent/suggestions', { params: { page: 0, size: 50, ...params } })
}

// 确认建议：签发 grantKey 推 agent 并派发执行任务
export function approveSuggestion(id) {
  return api.post(`/agent/suggestions/${id}/approve`)
}

// 忽略建议
export function rejectSuggestion(id) {
  return api.post(`/agent/suggestions/${id}/reject`)
}

// 工具注册表（人用）：列表 + 启停（能力=数据，改库即生效，下次注册下发）
export function listTools() {
  return api.get('/agent/tools')
}

export function setToolEnabled(id, enabled) {
  return api.put(`/agent/tools/${id}/enabled`, { enabled })
}
