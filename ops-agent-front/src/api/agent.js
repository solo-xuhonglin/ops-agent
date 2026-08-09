// AI Agent 管理面 API（人用）：多轮会话 CRUD/发消息/SSE 流式、任务查看、处置建议确认/忽略
import api from '../plugins/axios'

// ==================== 多轮会话 ====================

// 创建会话 → conversation
export function createConversation() {
  return api.post('/agent/conversations')
}

// 会话列表（分页，新→旧）
export function listConversations(params = {}) {
  return api.get('/agent/conversations', { params: { page: 0, size: 20, ...params } })
}

// 历史恢复：完整消息流（时间升序）
export function getConversationMessages(conversationId) {
  return api.get(`/agent/conversations/${conversationId}/messages`)
}

// 删除会话（连带消息）
export function deleteConversation(conversationId) {
  return api.delete(`/agent/conversations/${conversationId}`)
}

// 发消息：{ query?, taskType?, targetType?, targetId?, reasoning? } → { messageId, taskId, status }
// reasoning=true 走思考模式（默认）；false 走 fast 非思考模式
export function sendMessage(conversationId, payload) {
  return api.post(`/agent/conversations/${conversationId}/messages`, payload)
}

/**
 * SSE 流式回显：POST + fetch stream（EventSource 不支持 POST 与自定义 header）。
 * 解析 SSE 文本行（event: xxx \n data: {...}），把事件回调给 onEvent(event, data)。
 * 返回 AbortController 供「停止」时中断连接。
 */
export function streamConversation(conversationId, taskId, onEvent) {
  const controller = new AbortController()
  const token = localStorage.getItem('token')
  const url = `/api/agent/conversations/${conversationId}/stream${taskId ? `?taskId=${taskId}` : ''}`
  let settled = false // 已收到 done/error 收尾事件：后续连接关闭属正常收尾，不再视为错误

  fetch(url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'text/event-stream'
    },
    signal: controller.signal
  })
    .then(async (resp) => {
      if (!resp.ok || !resp.body) {
        throw new Error(`stream http ${resp.status}`)
      }
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let event = null
      const dataLines = []
      const flush = () => {
        if (!event) return
        let payload = dataLines.join('\n')
        if (payload.startsWith('data:')) payload = payload.slice(5).trim()
        try {
          onEvent(event, JSON.parse(payload))
        } catch (e) {
          onEvent(event, payload)
        }
        // 收尾事件：主动关闭连接（服务端 complete 的时序可能让浏览器把正常关闭
        // 误报为 NetworkError；本地 abort 后 read() 抛 AbortError，被 catch 静默吞掉）
        if (event === 'done' || event === 'error') {
          settled = true
          controller.abort()
        }
        event = null
        dataLines.length = 0
      }
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const parts = buffer.split('\n\n')
        buffer = parts.pop()
        for (const part of parts) {
          for (const line of part.split('\n')) {
            if (line.startsWith('event:')) event = line.slice(6).trim()
            else if (line.startsWith('data:')) dataLines.push(line)
            else if (line === '') flush()
          }
          flush()
        }
      }
      flush()
    })
    .catch((e) => {
      if (e.name !== 'AbortError' && !settled) {
        onEvent('error', { message: e.message || '流式连接失败' })
      }
    })
  return controller
}

// ==================== 任务（内部载体，授权闭环查看） ====================

// 任务详情
export function getTask(taskId) {
  return api.get(`/agent/tasks/${taskId}`)
}

// 取消任务（前端「停止」）：worker 自治置 CANCELLED
export function cancelTask(taskId) {
  return api.post(`/agent/tasks/${taskId}/cancel`)
}

// 处置建议列表（分页）
export function listSuggestions(params = {}) {
  return api.get('/agent/suggestions', { params: { page: 0, size: 50, ...params } })
}

// 确认建议：签发 grantKey 并派发 execute 任务（按 suggestionId UUID）
export function approveSuggestion(suggestionId) {
  return api.post(`/agent/suggestions/${suggestionId}/approve`)
}

// 忽略建议
export function rejectSuggestion(suggestionId) {
  return api.post(`/agent/suggestions/${suggestionId}/reject`)
}

// ==================== 规划（plan 卡片） ====================

// 会话的规划列表（新→旧）
export function listPlans(conversationId) {
  return api.get('/agent/plans', { params: { conversationId } })
}

// 单个 plan + 步骤（suggestions 按 step_no 升序）
export function getPlan(planId) {
  return api.get(`/agent/plans/${planId}`)
}

// 工具注册表（人用）：列表 + 启停（能力=数据，改库即生效，下次注册下发）
export function listTools() {
  return api.get('/agent/tools')
}

export function setToolEnabled(id, enabled) {
  return api.put(`/agent/tools/${id}/enabled`, { enabled })
}
