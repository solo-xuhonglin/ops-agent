// AI Agent 多轮会话状态：会话列表⇄聊天两视图、SSE 流式回显、建议授权闭环
import { defineStore } from 'pinia'
import * as agentApi from '../api/agent'

// 优先级排序权重（PENDING 置顶，其次 HIGH > NORMAL > LOW）
const PRIORITY_RANK = { HIGH: 0, NORMAL: 1, LOW: 2 }
const STATUS_RANK = { PENDING: 0, APPROVED: 1, EXECUTING: 2 }

function sortSuggestions(list) {
  return [...list].sort((a, b) => {
    const sa = STATUS_RANK[a.status] ?? 9
    const sb = STATUS_RANK[b.status] ?? 9
    if (sa !== sb) return sa - sb
    return (PRIORITY_RANK[a.priority] ?? 1) - (PRIORITY_RANK[b.priority] ?? 1)
  })
}

export const useAgentStore = defineStore('agent', {
  state: () => ({
    drawerOpen: false,
    activeView: 'chat',            // chat | list（会话列表）| suggestions（处置建议）
    conversations: [],
    totalConversations: 0,
    currentConversation: null,
    messages: [],                  // 当前会话消息（含流式中临时消息）
    streaming: false,              // 是否有活跃 SSE 流
    currentTaskId: null,
    streamController: null,        // AbortController（停止生成）
    suggestions: [],
    pendingCount: 0,
    loading: false,
    error: null
  }),
  getters: {
    pendingSuggestions: (s) => s.suggestions.filter((x) => x.status === 'PENDING')
  },
  actions: {
    toggleDrawer() {
      this.drawerOpen ? this.closeDrawer() : this.openChat()
    },
    openChat() {
      this.activeView = 'chat'
      this.drawerOpen = true
      if (!this.currentConversation) {
        this.fetchConversations()
      }
    },
    openList() {
      this.activeView = 'list'
      this.drawerOpen = true
      this.fetchConversations()
    },
    openSuggestions() {
      this.activeView = 'suggestions'
      this.drawerOpen = true
      this.fetchSuggestions()
    },
    closeDrawer() {
      this.stopStream()
      this.drawerOpen = false
    },

    // ==================== 会话 ====================

    async fetchConversations() {
      try {
        const { data } = await agentApi.listConversations()
        this.conversations = data.data.content
        this.totalConversations = data.data.totalElements
      } catch (e) {
        this.error = e.response?.data?.message || '会话列表加载失败'
      }
    },

    async createConversation() {
      const { data } = await agentApi.createConversation()
      const conv = data.data
      this.currentConversation = conv
      this.messages = []
      this.currentTaskId = null
      this.error = null
      await this.fetchConversations()
      return conv
    },

    /** 切换到某会话：先停当前流再加载历史 */
    async selectConversation(conversationId) {
      this.stopStream()
      this.activeView = 'chat'
      this.error = null
      this.currentTaskId = null
      try {
        const { data } = await agentApi.getConversationMessages(conversationId)
        this.messages = data.data || []
        this.currentConversation =
          this.conversations.find((c) => c.conversationId === conversationId) || null
      } catch (e) {
        this.error = e.response?.data?.message || '会话加载失败'
      }
    },

    async deleteConversation(conversationId) {
      await agentApi.deleteConversation(conversationId)
      if (this.currentConversation?.conversationId === conversationId) {
        this.currentConversation = null
        this.messages = []
        this.currentTaskId = null
        this.stopStream()
      }
      await this.fetchConversations()
    },

    // ==================== 发消息 + 流式 ====================

    /** 自然语言问询 / 列表页诊断：落到当前会话（无会话则新建），返回 {messageId, taskId}。 */
    async dispatch({ query = '', taskType, targetType, targetId } = {}) {
      if (this.streaming) return null
      if (!this.currentConversation) {
        await this.createConversation()
      }
      const text = query.trim()
      const payload = { query: text, taskType, targetType, targetId }

      // 本地先行渲染 user 消息（乐观更新）：诊断入口（无 query）显示目标描述兜底
      const localText = text || (targetType ? `诊断 ${targetType}#${targetId}` : '')
      const localUserMsg = {
        messageId: `local-${Date.now()}`,
        role: 'user',
        content: localText,
        status: 'completed',
        createdAt: new Date().toISOString()
      }
      this.messages.push(localUserMsg)

      // 占位 assistant 流式消息
      const streamingMsg = {
        messageId: `stream-${Date.now()}`,
        role: 'assistant',
        content: '',
        reasoning: '',
        status: 'streaming',
        toolCalls: [],          // [{name, args, summary}] 工具时间线
        createdAt: new Date().toISOString()
      }
      this.messages.push(streamingMsg)

      this.error = null
      try {
        const { data } = await agentApi.sendMessage(this.currentConversation.conversationId, payload)
        const { taskId } = data.data
        this.currentTaskId = taskId
        this.streaming = true
        this.openStream(this.currentConversation.conversationId, taskId, streamingMsg)
        // 首条消息后刷新会话列表（标题/时间更新）
        this.fetchConversations()
        return { taskId }
      } catch (e) {
        this.error = e.response?.data?.message || '消息发送失败'
        streamingMsg.status = 'failed'
        streamingMsg.content = '发送失败：' + this.error
        this.streaming = false
        throw e
      }
    },

    /** 启动 SSE 流：增量更新 streamingMsg；done/error 收尾（done 后拉任务建议）。 */
    openStream(conversationId, taskId, streamingMsg) {
      this.streamController = agentApi.streamConversation(conversationId, taskId, (event, data) => {
        switch (event) {
          case 'thinking':
            streamingMsg.reasoning += data?.delta || ''
            break
          case 'delta':
            streamingMsg.content += data?.delta || ''
            break
          case 'tool_call': {
            const item = {
              name: data?.name || 'tool',
              args: data?.args,
              summary: '',
              status: 'running'
            }
            streamingMsg.toolCalls.push(item)
            break
          }
          case 'tool_result': {
            const item = streamingMsg.toolCalls.find((t) => t.name === data?.name)
            if (item) {
              item.summary = data?.summary || ''
              item.status = 'done'
            }
            break
          }
          case 'done':
            streamingMsg.status = data?.status === 'failed' ? 'failed' : 'completed'
            if (data?.content) streamingMsg.content = data.content
            if (data?.reasoning) streamingMsg.reasoning = data.reasoning
            this.streaming = false
            this.streamController = null
            // 拉取该轮建议（授权卡）+ 任务最终态
            this.attachSuggestions(taskId)
            this.fetchConversations()
            break
          case 'error':
            streamingMsg.status = 'failed'
            streamingMsg.error = data?.message || '流式错误'
            this.streaming = false
            this.streamController = null
            break
        }
      })
    },

    /** 任务结束：拉该轮任务的建议（approve/reject 授权卡数据挂在消息上）。 */
    async attachSuggestions(taskId) {
      if (!taskId) return
      try {
        const { data } = await agentApi.listSuggestions({ page: 0, size: 100 })
        const suggestions = (data.data.content || []).filter((s) => s.taskId === taskId)
        const last = [...this.messages].reverse().find((m) => m.role === 'assistant')
        if (last && (last.status === 'completed' || last.status === 'failed')) {
          last.suggestions = suggestions
        }
      } catch (e) {
        // 建议拉取失败不阻塞 UI
      }
    },

    stopStream() {
      if (this.streamController) {
        this.streamController.abort()
        this.streamController = null
      }
      this.streaming = false
      // 未收尾的流式消息标记为中断
      this.messages.forEach((m) => {
        if (m.role === 'assistant' && m.status === 'streaming') {
          m.status = 'failed'
          m.error = m.error || '已停止'
        }
      })
    },

    // ==================== 建议授权 ====================

    async fetchSuggestions() {
      try {
        const { data } = await agentApi.listSuggestions()
        this.suggestions = sortSuggestions(data.data.content)
        this.pendingCount = this.suggestions.filter((s) => s.status === 'PENDING').length
      } catch (e) {
        // 忽略：抽屉打开时随会话刷新
      }
    },

    async approve(id) {
      const { data } = await agentApi.approveSuggestion(id)
      await this.fetchSuggestions()
      return data.data
    },

    async reject(id) {
      const { data } = await agentApi.rejectSuggestion(id)
      await this.fetchSuggestions()
      return data.data
    }
  }
})
