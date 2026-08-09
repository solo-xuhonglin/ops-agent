// AI Agent 多轮会话状态：会话列表⇄聊天两视图、SSE 流式（80ms 节流）、建议审批闭环、plan 卡片
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

// 流式渲染节流：SSE 事件按 80ms 窗口合并，避免每次 delta 全量重渲染
const STREAM_FLUSH_MS = 80

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
    plans: [],                     // 当前会话的规划（plan 卡片）
    activePlan: null,              // 活跃 plan（含 steps）
    _streamTimer: null,            // 节流定时器
    loading: false,
    error: null,
    reasoningEnabled: localStorage.getItem('agentReasoning') !== 'off'  // 「深度思考」开关（默认开，持久化）
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
      this.plans = []
      this.activePlan = null
      this.error = null
      await this.fetchConversations()
      return conv
    },

    /** 切换到某会话：先停当前流再加载历史 + plan */
    async selectConversation(conversationId) {
      this.stopStream()
      this.activeView = 'chat'
      this.error = null
      this.currentTaskId = null
      try {
        const { data } = await agentApi.getConversationMessages(conversationId)
        this.messages = (data.data || []).map((m) => ({ ...m, _thinkingOpen: true }))
        this.currentConversation =
          this.conversations.find((c) => c.conversationId === conversationId) || null
        this.fetchPlans(conversationId)
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
        this.plans = []
        this.activePlan = null
        this.stopStream()
      }
      await this.fetchConversations()
    },

    // ==================== 规划（plan 卡片） ====================

    /** 拉取会话规划列表 + 活跃 plan 详情（含步骤进度）。 */
    async fetchPlans(conversationId) {
      if (!conversationId) return
      try {
        const { data } = await agentApi.listPlans(conversationId)
        this.plans = data.data || []
        const active = this.plans.find((p) => p.status === 'RUNNING' || p.status === 'PLANNED')
        if (active) {
          const { data: detail } = await agentApi.getPlan(active.planId)
          this.activePlan = detail.data
        } else {
          this.activePlan = this.plans[0] ? (await agentApi.getPlan(this.plans[0].planId)).data.data : null
        }
      } catch (e) {
        // 忽略：无 plan 时正常
      }
    },

    // ==================== 发消息 + 流式 ====================

    /** 自然语言问询 / 列表页诊断：落到当前会话（无会话则新建），返回 {messageId, taskId}。 */
    async dispatch({ query = '', taskType, targetType, targetId } = {}) {
      if (this.streaming) return null
      if (!this.currentConversation) {
        await this.createConversation()
      }
      const text = query.trim()
      const payload = { query: text, taskType, targetType, targetId, reasoning: this.reasoningEnabled }

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

      // 占位 assistant 流式消息（thinking 默认展开）
      const streamingMsg = {
        messageId: `stream-${Date.now()}`,
        role: 'assistant',
        content: '',
        reasoning: '',
        status: 'streaming',
        _thinkingOpen: true,
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

    /**
     * 启动 SSE 流：delta/thinking 按 80ms 窗口节流合并后增量更新 streamingMsg；
     * done/error 收尾（done 后拉建议与 plan）；plan_update 事件刷新 plan 卡片。
     */
    openStream(conversationId, taskId, streamingMsg) {
      let pending = { thinking: '', delta: '' }
      const flush = () => {
        if (pending.thinking) {
          streamingMsg.reasoning += pending.thinking
          pending.thinking = ''
        }
        if (pending.delta) {
          streamingMsg.content += pending.delta
          pending.delta = ''
        }
      }
      this._streamTimer = setInterval(flush, STREAM_FLUSH_MS)

      this.streamController = agentApi.streamConversation(conversationId, taskId, (event, data) => {
        switch (event) {
          case 'thinking':
            pending.thinking += data?.delta || ''
            break
          case 'delta':
            pending.delta += data?.delta || ''
            break
          case 'tool_call': {
            flush()
            streamingMsg.toolCalls.push({
              name: data?.name || 'tool',
              args: data?.args,
              summary: '',
              status: 'running'
            })
            break
          }
          case 'tool_result': {
            flush()
            const item = streamingMsg.toolCalls.find((t) => t.name === data?.name)
            if (item) {
              item.summary = data?.summary || ''
              item.status = 'done'
            }
            break
          }
          case 'plan_update': {
            flush()
            this.fetchPlans(conversationId)
            break
          }
          case 'done': {
            flush()
            this._clearStreamTimer()
            streamingMsg.status = data?.status === 'failed' ? 'failed' : 'completed'
            if (data?.content) streamingMsg.content = data.content
            if (data?.reasoning) streamingMsg.reasoning = data.reasoning
            this.streaming = false
            this.streamController = null
            // 拉取该轮建议（授权卡）+ 任务最终态 + plan 进度
            this.attachSuggestions(taskId)
            this.fetchPlans(conversationId)
            this.fetchConversations()
            break
          }
          case 'error': {
            flush()
            this._clearStreamTimer()
            streamingMsg.status = 'failed'
            streamingMsg.error = data?.message || '流式错误'
            this.streaming = false
            this.streamController = null
            break
          }
        }
      })
    },

    _clearStreamTimer() {
      if (this._streamTimer) {
        clearInterval(this._streamTimer)
        this._streamTimer = null
      }
    },

    /** 任务结束：拉该轮任务的建议（approve/reject 授权卡数据挂在消息上）。 */
    async attachSuggestions(taskId) {
      if (!taskId) return
      try {
        const { data } = await agentApi.listSuggestions({ page: 0, size: 100 })
        const suggestions = (data.data.content || []).filter((s) => s.sourceTaskId === taskId || s.taskId === taskId)
        const last = [...this.messages].reverse().find((m) => m.role === 'assistant')
        if (last && (last.status === 'completed' || last.status === 'failed')) {
          last.suggestions = suggestions
        }
      } catch (e) {
        // 建议拉取失败不阻塞 UI
      }
    },

    /** 停止生成：abort SSE + 调 admin cancel API（worker 真取消，不再只是断连接）。 */
    stopStream() {
      if (this.streamController) {
        const taskId = this.currentTaskId
        this.streamController.abort()
        this.streamController = null
        if (taskId) {
          agentApi.cancelTask(taskId).catch(() => { /* 任务可能已完成，取消失败无妨 */ })
        }
      }
      this._clearStreamTimer()
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

    async approve(suggestionId) {
      const { data } = await agentApi.approveSuggestion(suggestionId)
      await this.fetchSuggestions()
      return data.data
    },

    async reject(suggestionId) {
      const { data } = await agentApi.rejectSuggestion(suggestionId)
      await this.fetchSuggestions()
      return data.data
    }
  }
})
