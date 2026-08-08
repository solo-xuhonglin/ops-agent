// AI Agent 全局助手状态：抽屉常驻跨路由、对话/历史视图、建议与任务数据
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
    activeView: 'chat',          // chat | history
    suggestions: [],
    pendingCount: 0,
    tasks: [],
    totalTasks: 0,
    currentTask: null,           // 对话视图选中的任务
    events: [],                  // 当前任务事件流
    loadingTasks: false,
    loadingSuggestions: false,
    taskError: null
  }),
  getters: {
    pendingSuggestions: (s) => s.suggestions.filter((x) => x.status === 'PENDING')
  },
  actions: {
    toggleDrawer() {
      this.drawerOpen ? this.closeDrawer() : this.openChat()
    },
    openChat(taskId) {
      this.activeView = 'chat'
      this.drawerOpen = true
      if (taskId) this.selectTask(taskId)
      else this.refreshAll()
    },
    openHistory() {
      this.activeView = 'history'
      this.drawerOpen = true
      this.refreshAll()
    },
    setView(view) {
      this.activeView = view
      if (view === 'history') this.refreshAll()
    },
    closeDrawer() {
      this.drawerOpen = false
    },

    // 自然语言问询：派发 question 任务并进入对话视图跟踪
    async dispatchQuestion(query) {
      const { data } = await agentApi.dispatchTask({ taskType: 'question', query })
      const taskId = data.data.taskId
      this.drawerOpen = true
      this.activeView = 'chat'
      await this.selectTask(taskId)
      return taskId
    },

    // 详情页"分析"按钮：派发诊断任务并跟踪
    async dispatchDiagnose({ taskType, targetType, targetId }) {
      const { data } = await agentApi.dispatchTask({ taskType, targetType, targetId })
      const taskId = data.data.taskId
      this.drawerOpen = true
      this.activeView = 'chat'
      await this.selectTask(taskId)
      return taskId
    },

    // 选中任务（对话视图）：拉详情 + 事件流
    async selectTask(taskId) {
      this.activeView = 'chat'
      this.taskError = null
      try {
        const { data } = await agentApi.getTask(taskId)
        this.currentTask = data.data.task
        this.events = data.data.events || []
      } catch (e) {
        this.taskError = e.response?.data?.message || '任务加载失败'
      }
      return this.currentTask
    },

    async fetchTasks() {
      this.loadingTasks = true
      try {
        const { data } = await agentApi.listTasks()
        this.tasks = data.data.content
        this.totalTasks = data.data.totalElements
      } finally {
        this.loadingTasks = false
      }
    },

    async fetchSuggestions() {
      this.loadingSuggestions = true
      try {
        const { data } = await agentApi.listSuggestions()
        this.suggestions = sortSuggestions(data.data.content)
        this.pendingCount = this.suggestions.filter((s) => s.status === 'PENDING').length
      } finally {
        this.loadingSuggestions = false
      }
    },

    // 轮询刷新区（抽屉打开时每 3s 调用）
    async refreshAll() {
      await Promise.allSettled([this.fetchTasks(), this.fetchSuggestions()])
    },
    async refreshCurrentTask() {
      if (this.activeView === 'chat' && this.currentTask) {
        const s = this.currentTask.status
        if (['DISPATCHED', 'RUNNING'].includes(s)) {
          await this.selectTask(this.currentTask.taskId)
        }
      }
    },

    // 确认建议：签发 grantKey + 派发执行任务
    async approve(id) {
      const { data } = await agentApi.approveSuggestion(id)
      await this.fetchSuggestions()
      return data.data
    },

    // 忽略建议
    async reject(id) {
      const { data } = await agentApi.rejectSuggestion(id)
      await this.fetchSuggestions()
      return data.data
    }
  }
})
