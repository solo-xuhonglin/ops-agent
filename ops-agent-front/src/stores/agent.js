// AI Agent 多轮会话状态：会话列表⇄聊天两视图、SSE 流式（80ms 节流）、建议审批闭环、plan 卡片
// 时间线模型：messages[] 是按时间序的扁平数组，按 kind 分发渲染：
//   USER / ASSISTANT —— 对话气泡
//   TOOL_CALL —— 工具调用独立成行（callId 唯一标识；同 call 后续 result 落同行的 toolSummary）
//   APPROVAL —— 建议审批独立成行（payload_json 含 suggestionId；同 suggestionId upsert 累计状态）
// 端上 streaming 期间按 SSE 事件实时 append/upsert 消息行，落库由 server 同步写入，
// refreshMessages 时按 (taskId+toolCallId) 或 (payload.suggestionId) 去重与合并。
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

/**
 * 从服务端行/前端临时行中归一化 kind（兼容历史老消息只有 role 字段）。
 * 解析 service 字段（payload_json 字符串）→ 对象，便于模板使用。
 */
function normalizeMessage(m) {
  if (!m) return m
  let kind = m.kind
  if (!kind) {
    if (m.role === 'user') kind = 'USER'
    else if (m.role === 'assistant') kind = 'ASSISTANT'
    else if (m.role === 'tool') kind = 'TOOL_CALL'
    else if (m.role === 'approval') kind = 'APPROVAL'
    else kind = 'ASSISTANT'
  }
  let payload = m.payload
  if (m.payloadJson && !payload) {
    try { payload = JSON.parse(m.payloadJson) } catch (e) { payload = null }
  }
  return { ...m, kind, payload }
}

export const useAgentStore = defineStore('agent', {
  state: () => ({
    drawerOpen: false,
    activeView: 'chat',            // chat | list（会话列表）| suggestions（处置建议）
    conversations: [],
    totalConversations: 0,
    currentConversation: null,
    messages: [],                  // 当前会话消息流（kind 字段统一，按时间序扁平）
    streaming: false,              // 是否有活跃 SSE 流
    currentTaskId: null,
    streamController: null,        // AbortController（停止生成）
    executeController: null,       // AbortController（approve 后监听 execute 实时事件）
    suggestions: [],
    pendingCount: 0,
    plans: [],                     // 当前会话的规划（plan 卡片）
    activePlan: null,              // 活跃 plan（含 steps）
    _streamTimer: null,            // 节流定时器
    _streamStartedAt: 0,           // 当前流式开始时间（dispatch 入口用 stale check）
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
      this.activeView = 'chat'
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
        this.messages = (data.data || []).map((m) => normalizeMessage({ ...m, _thinkingOpen: true }))
        this.currentConversation =
          this.conversations.find((c) => c.conversationId === conversationId) || null
        this.fetchPlans(conversationId)
        this.fetchSuggestions()
      } catch (e) {
        this.error = e.response?.data?.message || '会话加载失败'
      }
    },

    /** 刷新当前会话消息 + 建议 + plan（approve/reject 后拉取 execute 结果消息）。
     *  与本地流式占位行（status='streaming'）合并，跳过 SSE 已 append 但服务器尚未落库的 TOOL_CALL 重复行（callId 一致则用服务器行覆盖）。 */
    async refreshMessages(conversationId) {
      const cid = conversationId || this.currentConversation?.conversationId
      if (!cid) return
      try {
        const { data } = await agentApi.getConversationMessages(cid)
        const serverMsgs = (data.data || []).map((m) => normalizeMessage({ ...m, _thinkingOpen: true }))

        // 流式中占位行：保留，refresh 后由 done 后的落库消息替代
        const pending = this.messages.filter((m) => m.status === 'streaming' || m.status === 'executing')
        // 去重：本地已有的 TOOL_CALL/APPROVAL 与服务器返回同一行（同 taskId+callId / payload.suggestionId）以服务器行为准
        const seenCalls = new Set(serverMsgs.filter((m) => m.kind === 'TOOL_CALL').map((m) => `${m.taskId}:${m.toolCallId}`))
        const seenApprovals = new Set(serverMsgs.filter((m) => m.kind === 'APPROVAL').map((m) => m.payload?.suggestionId).filter(Boolean))
        const localOnly = this.messages.filter((m) => {
          if (m.status === 'streaming' || m.status === 'executing') return true // 占位行保留
          if (m.kind === 'TOOL_CALL') return !seenCalls.has(`${m.taskId}:${m.toolCallId}`)
          if (m.kind === 'APPROVAL' && m.payload?.suggestionId) return !seenApprovals.has(m.payload.suggestionId)
          return false
        })
        this.messages = [...serverMsgs, ...localOnly].sort((a, b) => (a.id || 0) - (b.id || 0))
        this.fetchSuggestions()
        this.fetchPlans(cid)
      } catch (e) {
        // 刷新失败不阻塞
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

    // ==================== 工具/审批 消息行 upsert（仅前端，SSE 事件源） ====================

    /** 追加/更新一行 TOOL_CALL 消息（按 taskId+callId upsert）：
     *  - tool_call 事件：append 新行（status=running）
     *  - tool_result 事件：find existing, fill toolSummary + status=completed */
    upsertToolCallRow({ taskId, callId, name, args, summary, isResult }) {
      const idx = this.messages.findIndex(
        (m) => m.kind === 'TOOL_CALL' && m.taskId === taskId && m.toolCallId === callId
      )
      if (idx >= 0) {
        const row = this.messages[idx]
        if (isResult) {
          row.toolSummary = summary || ''
          row.status = 'completed'
        }
        return
      }
      this.messages.push({
        messageId: `local-tc-${callId || Date.now()}`,
        kind: 'TOOL_CALL',
        taskId,
        toolCallId: callId,
        toolName: name || 'tool',
        toolArgs: isResult ? null : (typeof args === 'string' ? args : JSON.stringify(args || {})),
        toolSummary: isResult ? (summary || '') : '',
        status: isResult ? 'completed' : 'running',
        createdAt: new Date().toISOString()
      })
    },

    /** 追加/更新一行 APPROVAL 消息（按 payload.suggestionId upsert）：
     *  - PENDING 创建（approve_* 工具调用截图过来）：insert 新行
     *  - 后续 approve/reject/execute 变更：在原行 payload.decision 上更新 */
    upsertApprovalRow({ suggestionId, actionType, targetType, targetId, params, reason, priority,
                        planId, stepNo, retryOf, decision, confirmedBy, confirmedAt, executedAt, result }) {
      if (!suggestionId) return
      const existing = this.messages.find((m) => m.kind === 'APPROVAL' && m.payload?.suggestionId === suggestionId)
      const payload = {
        suggestionId, actionType, targetType, targetId, params, reason, priority,
        planId, stepNo, retryOf, decision, confirmedBy, confirmedAt, executedAt, result
      }
      if (existing) {
        existing.payload = { ...(existing.payload || {}), ...payload }
        existing.decision = decision || existing.decision
        return
      }
      this.messages.push({
        messageId: `local-ap-${suggestionId}`,
        kind: 'APPROVAL',
        decision: decision || 'PENDING',
        payload,
        status: 'completed',
        createdAt: new Date().toISOString()
      })
    },

    // ==================== 发消息 + 流式 ====================

    /** 自然语言问询 / 列表页诊断：落到当前会话（无会话则新建），返回 {messageId, taskId}。 */
    async dispatch({ query = '', taskType, targetType, targetId } = {}) {
      // stale 兜底：streaming 状态卡 >120s（与前端 SSE 空闲超时一致）通常是 SSE 网络半关闭/
      // 服务端既没推 done 也没推 keepalive 事件；服务端已有 15s 心跳，正常慢思考不会触发。
      // 若命中则强制收尾，避免用户的"再次发消息"被静默吃掉
      if (this.streaming && this._streamStartedAt && Date.now() - this._streamStartedAt > 120_000) {
        if (this.streamController) this.streamController.abort()
        this.streamController = null
        this.streaming = false
      }
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
        kind: 'USER',
        content: localText,
        status: 'completed',
        createdAt: new Date().toISOString()
      }
      this.messages.push(localUserMsg)

      // 占位 assistant 流式消息（thinking 默认展开）；工具调用与审批不再挂在消息上，
      // 而是用 upsertToolCallRow / upsertApprovalRow 落到顶层 messages[]（按时间序）
      const streamingMsg = {
        messageId: `stream-${Date.now()}`,
        kind: 'ASSISTANT',
        content: '',
        reasoning: '',
        status: 'streaming',
        _thinkingOpen: true,
        createdAt: new Date().toISOString()
      }
      this.messages.push(streamingMsg)
      // 关键：必须用 push 后 Pinia 包出来的 reactive proxy 来更新流式内容
      // （直接改原始对象引用不会触发 Vue 渲染 —— 流式效果/中间步骤全靠这个 proxy）
      const streamingRef = this.messages[this.messages.length - 1]

      this.error = null
      this._streamStartedAt = Date.now()
      try {
        const { data } = await agentApi.sendMessage(this.currentConversation.conversationId, payload)
        const { taskId } = data.data
        this.currentTaskId = taskId
        this.streaming = true
        this.openStream(this.currentConversation.conversationId, taskId, streamingRef)
        // 首条消息后刷新会话列表（标题/时间更新）
        this.fetchConversations()
        return { taskId }
      } catch (e) {
        this.error = e.response?.data?.message || '消息发送失败'
        streamingRef.status = 'failed'
        streamingRef.content = '发送失败：' + this.error
        this.streaming = false
        throw e
      }
    },

    /**
     * 启动 SSE 流：delta/thinking 按 80ms 窗口节流合并后增量更新 streamingMsg；
     * tool_call/tool_result → upsertToolCallRow（同 callId 一行）；
     * approve_<写工具> 工具调用 → 衍生 APPROVAL 行（占位 PENDING，approve/reject 后由 fetchSuggestions 补 decision）；
     * done/error 收尾；plan_update 事件刷新 plan 卡片。
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
            const callId = data?.id || data?.callId || null
            const name = data?.name || 'tool'
            const args = data?.args
            this.upsertToolCallRow({ taskId, callId, name, args, isResult: false })
            // 同步从本地 store 的 suggestions 里尝试找到对应的 PENDING 项以预填 meta；
            // 同步的 fetchSuggestions 会异步拉 server 结果做最终落地
            if (name.startsWith('approve_')) {
              this._prefillApprovalFromTool(taskId, name, args, callId)
            }
            break
          }
          case 'tool_result': {
            flush()
            const callId = data?.id || data?.callId || null
            const name = data?.name || 'tool'
            const summary = data?.summary || ''
            this.upsertToolCallRow({ taskId, callId, name, summary, isResult: true })
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
            this._streamStartedAt = 0
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
            this._streamStartedAt = 0
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

    /** approve_<写工具> 工具调用：从前端本地 memory 里反查本轮已 fetch 的 PENDING 建议（PENDING 来自 SSE 早期，worker 也异步直写库）。
     *  若本地无对应 suggestionId，先 upsert 一个 PENDING 占位行；后续 fetchSuggestions 会用真实 suggestionId 刷新。
     */
    _prefillApprovalFromTool(taskId, name, args, callId) {
      // 从本地 store.suggestions 中找同 taskId 的 PENDING，且 actionType 匹配（args.action / args.plan 等）。
      const toolActionType = (name || '').replace(/^approve_/, '')
      const cand = this.suggestions.find(
        (s) => s.status === 'PENDING' && s.actionType === toolActionType
      )
      if (cand) {
        this.upsertApprovalRow({
          suggestionId: cand.suggestionId,
          actionType: cand.actionType,
          targetType: cand.targetType,
          targetId: cand.targetId,
          params: cand.params,
          reason: cand.reason,
          priority: cand.priority,
          planId: cand.planId,
          stepNo: cand.stepNo,
          retryOf: cand.retryOf,
          decision: 'PENDING'
        })
      }
    },

    /**
     * approve 后监听 execute 任务的实时事件（会话级流，不绑 taskId）：
     * - tool_call/tool_result → upsertToolCallRow（同一行累计）
     * - 占位消息每秒刷新已等待秒数（_elapsed），done/error 前一直可见
     * - done/error → 清计时 + 占位标记 completed/failed（refreshMessages 不再保留）→ 刷新（落库结果替代占位）
     * execute 通常秒级返回，流会很快收到 done 自动关闭；重复 approve 时旧的监听让位。
     */
    listenExecute(conversationId, label = '正在执行已审批的写操作…') {
      if (!conversationId) return
      if (this.executeController) {
        this.executeController.abort()
        this.executeController = null
      }
      // 本地占位：execute 结果消息落库前先展示执行状态（落库后 refreshMessages 会替换掉）
      this.messages.push({
        messageId: `exec-${Date.now()}`,
        kind: 'ASSISTANT',
        content: label,
        status: 'executing',
        _elapsed: 0,
        _thinkingOpen: true,
        createdAt: new Date().toISOString()
      })
      const proxy = this.messages[this.messages.length - 1]
      const startTs = Date.now()
      const timer = setInterval(() => {
        proxy._elapsed = Math.round((Date.now() - startTs) / 1000)
      }, 1000)
      this.executeController = agentApi.streamConversation(conversationId, null, (event, data) => {
        switch (event) {
          case 'tool_call':
            this.upsertToolCallRow({
              taskId: data?.taskId || null,
              callId: data?.id || data?.callId || null,
              name: data?.name || 'tool',
              args: data?.args,
              isResult: false
            })
            break
          case 'tool_result':
            this.upsertToolCallRow({
              taskId: data?.taskId || null,
              callId: data?.id || data?.callId || null,
              name: data?.name || 'tool',
              summary: data?.summary || '',
              isResult: true
            })
            break
          case 'done':
          case 'error': {
            clearInterval(timer)
            this.executeController = null
            proxy.status = event === 'done' ? 'completed' : 'failed'
            if (event === 'done' && data?.content) proxy.content = data.content
            this.refreshMessages(conversationId)
            break
          }
        }
      })
    },

    /** 任务结束：拉该轮任务的建议（approve/reject 授权卡数据挂在消息上）。
     *  同时把每条 PENDING 建议 upsert 成 APPROVAL 行（持 suggestionId + plan 关联）。 */
    async attachSuggestions(taskId) {
      if (!taskId) return
      try {
        const { data } = await agentApi.listSuggestions({ page: 0, size: 100 })
        const list = data.data.content || []
        for (const s of list.filter((x) => x.status === 'PENDING' && x.sourceTaskId === taskId)) {
          this.upsertApprovalRow({
            suggestionId: s.suggestionId,
            actionType: s.actionType,
            targetType: s.targetType,
            targetId: s.targetId,
            params: s.params,
            reason: s.reason,
            priority: s.priority,
            planId: s.planId,
            stepNo: s.stepNo,
            retryOf: s.retryOf,
            decision: s.status
          })
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
      this._streamStartedAt = 0
      // 未收尾的流式消息标记为中断
      this.messages.forEach((m) => {
        if (m.kind === 'ASSISTANT' && m.status === 'streaming') {
          m.status = 'failed'
          m.error = m.error || '已停止'
        }
      })
    },

    // ==================== 建议授权 ====================

    async fetchSuggestions() {
      try {
        const { data } = await agentApi.listSuggestions()
        const list = sortSuggestions(data.data.content || [])
        this.suggestions = list
        this.pendingCount = list.filter((s) => s.status === 'PENDING').length
        // 同步每个已知 suggestion 的最新决策到 APPROVAL 行（即便不是当前轮生成的）
        for (const s of list) {
          if (!s.suggestionId) continue
          this.upsertApprovalRow({
            suggestionId: s.suggestionId,
            actionType: s.actionType,
            targetType: s.targetType,
            targetId: s.targetId,
            params: s.params,
            reason: s.reason,
            priority: s.priority,
            planId: s.planId,
            stepNo: s.stepNo,
            retryOf: s.retryOf,
            decision: s.status,
            confirmedBy: s.confirmedBy,
            confirmedAt: s.confirmedAt,
            executedAt: s.executedAt,
            result: s.result
          })
        }
      } catch (e) {
        // 忽略：抽屉打开时随会话刷新
      }
    },

    async approve(suggestionId) {
      const { data } = await agentApi.approveSuggestion(suggestionId)
      // 即时乐观更新 APPROVAL 行：用户操作先可见，不等 server
      this.upsertApprovalRow({
        suggestionId,
        decision: 'APPROVED'
      })
      await this.fetchSuggestions()
      return data.data
    },

    async reject(suggestionId) {
      const { data } = await agentApi.rejectSuggestion(suggestionId)
      this.upsertApprovalRow({
        suggestionId,
        decision: 'REJECTED'
      })
      await this.fetchSuggestions()
      return data.data
    }
  }
})
