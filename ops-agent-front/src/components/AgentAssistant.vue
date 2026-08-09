<template>
  <!-- 全局 AI 助手浮窗：右下 FAB + 右侧抽屉，跨路由常驻（挂在 App.vue）。
       抽屉打开时 FAB 隐藏，避免遮挡抽屉内容 -->
  <div v-if="visible && !store.drawerOpen">
    <v-badge :content="store.pendingCount" :model-value="store.pendingCount > 0" color="error">
      <v-btn class="agent-fab" color="primary" size="large" elevation="4"
             icon="mdi-robot-outline"
             @click="store.toggleDrawer()" />
    </v-badge>
  </div>

  <v-navigation-drawer ref="drawerEl" v-if="visible" v-model="store.drawerOpen" location="right" temporary
                       :width="drawerWidth" class="agent-drawer">
    <!-- 左缘拖拽手柄：横向拖动调整抽屉宽度 -->
    <div class="drawer-resizer" @pointerdown="startResize" />
    <template #prepend>
      <div class="d-flex align-center px-4 py-3">
        <v-btn v-if="store.activeView === 'chat'" icon="mdi-arrow-left" variant="text" size="small"
               @click="store.openList()" />
        <v-icon color="primary">mdi-robot</v-icon>
        <span class="text-title-medium font-weight-bold ml-2">
          {{ viewTitle }}
        </span>
        <v-spacer />
        <v-tooltip text="处置建议" location="bottom">
          <template #activator="{ props }">
            <v-btn v-bind="props" icon="mdi-clipboard-text-outline" variant="text" size="small"
                   :color="store.pendingCount > 0 ? 'error' : ''" @click="store.openSuggestions()" />
          </template>
        </v-tooltip>
        <v-tooltip text="历史对话" location="bottom">
          <template #activator="{ props }">
            <v-btn v-bind="props" icon="mdi-history" variant="text" size="small"
                   :color="store.activeView === 'list' ? 'primary' : ''" @click="store.openList()" />
          </template>
        </v-tooltip>
        <v-btn icon="mdi-close" variant="text" size="small" @click="store.closeDrawer()" />
      </div>
      <v-divider />
    </template>

    <div class="d-flex flex-column fill-height">
      <!-- ============ 会话列表视图 ============ -->
      <div v-if="store.activeView === 'list'" class="d-flex flex-column fill-height">
        <div class="pa-3 pb-2">
          <v-btn block color="primary" prepend-icon="mdi-plus" @click="newChat">新建会话</v-btn>
        </div>
        <div class="flex-grow-1 overflow-y-auto px-2">
          <div v-for="c in store.conversations" :key="c.conversationId"
               class="history-item history-item--clickable mb-1"
               :class="{ 'history-item--active': store.currentConversation?.conversationId === c.conversationId }"
               @click="store.selectConversation(c.conversationId)">
            <div class="history-item__head">
              <v-avatar color="primary" size="24" variant="tonal">
                <v-icon size="14">mdi-forum-outline</v-icon>
              </v-avatar>
              <span class="history-item__title">{{ c.title || '新对话' }}</span>
              <span class="history-item__time">{{ fmtDateTime(c.updatedAt) }}</span>
              <v-btn icon="mdi-delete-outline" variant="text" size="x-small" density="compact"
                     @click.stop="removeConversation(c)" />
            </div>
          </div>
          <div v-if="!store.conversations.length" class="empty-hint">
            <v-icon icon="mdi-forum-outline" size="40" class="mb-2" />
            <div class="text-body-medium text-medium-emphasis">暂无会话，点击上方新建开始对话</div>
          </div>
        </div>
      </div>

      <!-- ============ 处置建议视图 ============ -->
      <div v-else-if="store.activeView === 'suggestions'" class="d-flex flex-column fill-height">
        <div class="flex-grow-1 overflow-y-auto pa-2">
          <div v-for="s in store.suggestions" :key="s.id"
               class="history-item mb-1" :class="{ 'history-item--pending': s.status === 'PENDING' }">
            <div class="history-item__head">
              <v-avatar :color="priorityColor(s.priority)" size="28" variant="tonal">
                <v-icon size="16" :color="priorityColor(s.priority)">{{ actionIcon(s.actionType) }}</v-icon>
              </v-avatar>
              <span class="history-item__title">{{ actionText(s.actionType) }}</span>
              <v-chip size="x-small" :color="sugColor(s.status)">{{ sugText(s.status) }}</v-chip>
              <span class="history-item__time">{{ fmtDateTime(s.createdAt) }}</span>
            </div>
            <div class="history-item__body text-body-small text-medium-emphasis">{{ targetText(s) }}</div>
            <div v-if="s.reason" class="history-item__body text-body-small text-medium-emphasis history-item__clamp-2" :title="s.reason">{{ s.reason }}</div>
            <div v-if="s.status === 'PENDING' && canWrite" class="history-item__body">
              <v-btn size="x-small" color="primary" variant="tonal" @click="approve(s)">确认</v-btn>
              <v-btn size="x-small" variant="text" class="ml-2" @click="reject(s)">忽略</v-btn>
            </div>
            <div v-else-if="s.status === 'EXECUTED' && s.result" class="history-item__body text-caption text-success">
              <div :class="['suggestion-result', { 'suggestion-result--collapsed': !resultExpanded[s.id] }]"
                   :title="s.result" v-html="renderMarkdown(s.result)" />
              <div class="suggestion-result__toggle" @click="toggleResult(s.id)">
                {{ resultExpanded[s.id] ? '收起' : '展开' }}
              </div>
            </div>
          </div>
          <div v-if="!store.suggestions.length" class="empty-hint">
            <v-icon icon="mdi-inbox-outline" size="40" class="mb-2" />
            <div class="text-body-medium text-medium-emphasis">暂无处置建议</div>
          </div>
        </div>
      </div>

      <!-- ============ 聊天视图 ============ -->
      <div v-else class="d-flex flex-column fill-height">
        <div class="flex-grow-1 overflow-y-auto pa-4" ref="scrollEl">
          <!-- plan 卡片：当前规划 + 步骤进度 -->
          <div v-if="store.activePlan" class="plan-card mb-3 pa-3">
            <div class="d-flex align-center mb-1">
              <v-icon size="16" class="mr-1" color="primary">mdi-format-list-checks</v-icon>
              <span class="text-body-small font-weight-bold">
                {{ store.activePlan.plan.summary || '执行计划' }}
              </span>
              <v-chip size="x-small" class="ml-2" :color="planStatusColor(store.activePlan.plan.status)">
                {{ planStatusText(store.activePlan.plan.status) }}
              </v-chip>
              <v-spacer />
              <v-btn size="x-small" variant="text" icon="mdi-refresh" density="compact"
                     @click="store.fetchPlans(store.currentConversation?.conversationId)" />
            </div>
            <div v-for="(st, i) in planSteps" :key="st.step_no" class="plan-step d-flex align-center">
              <v-icon size="13" class="mr-1" :color="stepStatusColor(st.displayStatus)">
                {{ stepStatusIcon(st.displayStatus) }}
              </v-icon>
              <span class="text-caption plan-step__text">{{ i + 1 }}. {{ actionText(st.action_type) }}</span>
              <span class="text-caption text-medium-emphasis ml-1">{{ planTargetText(st) }}</span>
              <v-spacer />
              <v-chip size="x-small" density="compact" :color="stepStatusColor(st.displayStatus)">
                {{ stepStatusText(st.displayStatus) }}
              </v-chip>
            </div>
          </div>

          <div v-if="!store.messages.length && !store.streaming" class="agent-welcome pa-2">
            <div class="agent-welcome__title">你好，我是 Agent 助手</div>

            <div class="agent-welcome__block">
              <div class="agent-welcome__block-title">我能做什么</div>
              <div class="text-body-small text-medium-emphasis">
                诊断排查训练任务、模型服务、数据集与模型；自动采集天气数据；发起训练与部署服务。
              </div>
            </div>

            <div class="agent-welcome__block">
              <div class="agent-welcome__block-title">开始使用</div>
              <div class="agent-welcome__chips">
                <v-btn v-for="q in quickPrompts" :key="q" variant="tonal" color="primary" size="small"
                       class="ma-1" :loading="sending" @click="sendQuick(q)">
                  {{ q }}
                </v-btn>
              </div>
            </div>
          </div>

          <div v-for="m in store.messages" :key="m.messageId" class="msg-row"
               :class="m.role === 'user' ? 'msg-row--user' : 'msg-row--assistant'">
            <!-- 用户消息：右对齐气泡 -->
            <div v-if="m.role === 'user'" class="msg-bubble msg-bubble--user">
              <div class="text-body-medium msg-text">{{ m.content }}</div>
            </div>

            <!-- assistant 消息：卡片 + 思考折叠 + markdown + 授权卡 -->
            <div v-else class="msg-bubble msg-bubble--assistant">
              <!-- 思考过程（推理链 + 工具时间线）折叠面板 -->
              <div v-if="(m.reasoning || m.toolCalls?.length)" class="thinking-box">
                <div class="thinking-head" @click="toggleThinking(m)">
                  <v-icon size="14" class="mr-1">mdi-brain</v-icon>
                  <span>思考过程</span>
                  <v-spacer />
                  <v-icon size="14">{{ m._thinkingOpen ? 'mdi-chevron-up' : 'mdi-chevron-down' }}</v-icon>
                </div>
                <div v-if="m._thinkingOpen" class="thinking-body">
                  <div v-if="m.reasoning" class="thinking-text">{{ m.reasoning }}</div>
                  <div v-for="(t, i) in m.toolCalls" :key="i" class="tool-item">
                    <v-icon size="14" :icon="t.status === 'done' ? 'mdi-check' : 'mdi-wrench'"
                            :color="t.status === 'done' ? 'success' : 'info'" class="mr-1" />
                    <span class="text-body-small font-weight-medium">{{ t.name }}</span>
                    <span v-if="t.args && Object.keys(t.args).length" class="tool-args">{{ prettyArgs(t.args) }}</span>
                    <div v-if="t.summary" class="tool-summary text-caption">{{ t.summary }}</div>
                  </div>
                </div>
              </div>

              <!-- 流式光标 -->
              <div v-if="m.status === 'streaming' && !m.content && !m.reasoning" class="msg-text">
                <span class="streaming-dots"><span>●</span><span>●</span><span>●</span></span>
              </div>

              <!-- 答复：流式中纯文本（pre-wrap，零 markdown 重渲染开销），完成后一次 markdown 渲染 -->
              <div v-if="m.content && (m.status === 'streaming' || m.status === 'executing')" class="msg-text">
                {{ m.content }}
                <span v-if="m.status === 'executing' && m._elapsed > 0" class="exec-wait">
                  （已等待 {{ m._elapsed }}s）
                </span>
              </div>
              <div v-else-if="m.content" class="markdown-body" v-html="renderMarkdown(m.content)" />

              <!-- 授权卡：该轮建议（approve/reject 闭环） -->
              <div v-if="m.suggestions?.length" class="mt-2">
                <v-card v-for="s in m.suggestions" :key="s.id" variant="outlined" class="suggestion-card mb-1">
                  <v-card-text class="py-3">
                    <!-- 标题行：操作图标 + 操作名 + 状态 + 目标 -->
                    <div class="d-flex align-center mb-1">
                      <v-icon size="22" :color="priorityColor(s.priority)" class="mr-1">{{ actionIcon(s.actionType) }}</v-icon>
                      <span class="text-title-medium font-weight-bold">{{ actionText(s.actionType) }}</span>
                      <v-chip :color="s.status === 'PENDING' ? 'warning' : sugColor(s.status)" size="x-small" class="ml-2">
                        {{ sugText(s.status) }}
                      </v-chip>
                      <v-spacer />
                      <span class="text-body-small text-medium-emphasis">{{ targetText(s) }}</span>
                    </div>
                    <!-- 原因 -->
                    <div v-if="s.reason" class="text-body-medium mt-1">{{ s.reason }}</div>
                    <!-- 业务参数 -->
                    <div v-if="paramsEntries(s).length" class="suggestion-params mt-2">
                      <div v-for="(p, i) in paramsEntries(s)" :key="i" class="d-flex align-start">
                        <span class="text-body-small text-medium-emphasis param-key">{{ p.k }}</span>
                        <span class="text-body-small param-val">{{ p.v }}</span>
                      </div>
                    </div>
                    <!-- 重试标记 -->
                    <div v-if="s.retryOf" class="text-body-small text-warning mt-1">
                      <v-icon size="14" class="mr-1">mdi-restart</v-icon>重试：原建议 {{ s.retryOf }}
                    </div>
                    <!-- 审批动作 / 结果 -->
                    <div v-if="s.status === 'PENDING' && canWrite" class="mt-2">
                      <v-btn size="small" color="primary" @click="approve(s)">确认执行</v-btn>
                      <v-btn size="small" variant="text" class="ml-2" @click="reject(s)">忽略</v-btn>
                    </div>
                    <div v-else-if="s.status === 'EXECUTED' && s.result"
                         class="text-caption text-success mt-1 suggestion-result"
                         v-html="renderMarkdown(s.result)" />
                  </v-card-text>
                </v-card>
              </div>

              <!-- 失败/错误 -->
              <div v-if="m.status === 'failed'" class="text-body-small text-error mt-1">
                <v-icon size="14" class="mr-1">mdi-alert-circle</v-icon>{{ m.error || '生成失败' }}
              </div>
            </div>
          </div>
        </div>

        <!-- 底部输入框：流式中禁用并显示停止 -->
        <div class="pa-3">
          <div class="d-flex align-center mb-1 px-1">
            <v-icon size="14" color="primary" class="mr-1">mdi-brain</v-icon>
            <span class="text-body-small font-weight-medium mr-2">深度思考</span>
            <v-switch v-model="store.reasoningEnabled" hide-details density="compact"
                       inset color="primary"
                       @update:model-value="persistReasoning" />
            <v-spacer />
            <span class="text-caption text-medium-emphasis">
              {{ store.reasoningEnabled ? '推理链 + 工具调用' : '快速回答' }}
            </span>
          </div>
          <div v-if="store.pendingSuggestions.length" class="mb-2">
            <v-card v-for="s in store.pendingSuggestions.slice(0, 2)" :key="s.id"
                    class="mb-1 suggestion-card" variant="outlined">
              <v-card-text class="py-2">
                <div class="d-flex align-center">
                  <v-chip :color="priorityColor(s.priority)" size="x-small">{{ priorityText(s.priority) }}</v-chip>
                  <span class="text-body-small font-weight-bold ml-2">{{ actionText(s.actionType) }}</span>
                  <v-spacer />
                  <v-btn v-if="canWrite" size="x-small" color="primary" @click="approve(s)">确认</v-btn>
                  <v-btn v-if="canWrite" size="x-small" variant="text" class="ml-1" @click="reject(s)">忽略</v-btn>
                </div>
              </v-card-text>
            </v-card>
          </div>
          <v-textarea v-model="input" density="compact" hide-details rows="1" max-rows="3"
                      auto-grow
                      :placeholder="store.streaming ? '正在生成回复…' : '询问系统状态，如：最近有哪些异常？'"
                      :disabled="store.streaming"
                      @keydown.enter.exact.prevent="send">
            <template #append-inner>
              <v-btn v-if="store.streaming" icon="mdi-stop" variant="text" size="small"
                     @click="store.stopStream()" />
              <v-btn v-else :icon="sending ? undefined : 'mdi-send'"
                     :loading="sending"
                     :disabled="!input.trim()"
                     variant="text" size="small" @click="send" />
            </template>
          </v-textarea>
          <div class="text-caption text-medium-emphasis mt-1 px-1">Enter 发送 · Shift+Enter 换行</div>
        </div>
      </div>
    </div>
  </v-navigation-drawer>
</template>

<script setup>
import { ref, computed, reactive, watch, nextTick, onUnmounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useAgentStore } from '../stores/agent'
import { useConfirm } from '../composables/useConfirm'
import { useNotify, errMsg } from '../composables/useNotify'
import { fmtDateTime } from '../utils/format'
import { renderMarkdown } from '../utils/markdown'

const auth = useAuthStore()
const store = useAgentStore()
const { confirmDialog } = useConfirm()
const { notifyError } = useNotify()

const visible = computed(() => auth.isLoggedIn)
const canWrite = computed(() => auth.hasPerm('agent:write'))

const input = ref('')
const sending = ref(false)
const scrollEl = ref(null)

// 头部标题：按当前视图显示
const viewTitle = computed(() => {
  if (store.activeView === 'list') return '历史对话'
  if (store.activeView === 'suggestions') return '处置建议'
  return store.currentConversation?.title || 'Agent 助手'
})

// ===== plan 卡片：plan.steps 骨架 + suggestions 审批状态合并 =====
const planSteps = computed(() => {
  const p = store.activePlan
  if (!p?.plan) return []
  let skeleton = []
  try { skeleton = JSON.parse(p.plan.steps || '[]') } catch { skeleton = [] }
  if (!skeleton.length) {
    // 兜底：无 steps 骨架时用 suggestions 渲染
    skeleton = (p.steps || []).map((s, i) => ({
      step_no: s.stepNo || i + 1,
      action_type: s.actionType,
      target_type: s.targetType,
      target_id: s.targetId
    }))
  }
  const sugs = p.steps || []
  return skeleton.map((st) => {
    const sug = sugs.find((s) => s.stepNo === st.step_no)
    let displayStatus = 'WAITING'
    if (sug && sug.status !== 'PENDING') displayStatus = sug.status
    else if (st.status === 'done') displayStatus = 'EXECUTED'
    else if (st.status === 'failed') displayStatus = 'FAILED'
    else if (st.status === 'cancelled') displayStatus = 'CANCELLED'
    else if (sug && sug.status === 'PENDING') displayStatus = 'PENDING'
    return { ...st, displayStatus }
  })
})

// ===== 抽屉宽度：左缘拖拽调整（320~760），持久化到 localStorage =====
const MIN_W = 320
const MAX_W = 760
const drawerEl = ref(null)
const drawerWidth = ref(Number(localStorage.getItem('agentDrawerWidth')) || 430)

function drawerDom() {
  return document.querySelector('.agent-drawer') || drawerEl.value?.$el || drawerEl.value
}

function startResize(e) {
  e.preventDefault()
  if (e.pointerId !== undefined && e.target?.setPointerCapture) {
    try { e.target.setPointerCapture(e.pointerId) } catch (err) { /* ignore */ }
  }
  const drawer = drawerDom()
  const startX = e.clientX
  const startW = drawer ? drawer.getBoundingClientRect().width : drawerWidth.value
  if (drawer) drawer.style.transition = 'none'
  const onMove = (ev) => {
    if (!drawer) return
    const w = Math.min(MAX_W, Math.max(MIN_W, startW + (startX - ev.clientX)))
    drawer.style.width = `${w}px`
    drawerWidth.value = w
  }
  const onUp = () => {
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
    window.removeEventListener('pointercancel', onUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    if (drawer) {
      const w = Math.round(drawer.getBoundingClientRect().width)
      drawer.style.transition = ''
      drawerWidth.value = Math.min(MAX_W, Math.max(MIN_W, w))
      localStorage.setItem('agentDrawerWidth', String(drawerWidth.value))
    }
  }
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
  window.addEventListener('pointercancel', onUp)
}

// ===== 自动滚动到底部：节流（200ms 内最多一次），避免流式高频触发 =====
let scrollTimer = null
watch(() => store.messages.map((m) => m.content + (m.reasoning || '')).join('|'), () => {
  if (scrollTimer) return
  scrollTimer = setTimeout(async () => {
    scrollTimer = null
    await nextTick()
    if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
  }, 200)
})

// 抽屉打开：刷新会话列表与建议；关闭时停流
watch(() => store.drawerOpen, async (open) => {
  if (open) {
    store.fetchConversations()
    store.fetchSuggestions()
  } else {
    store.stopStream()
  }
})
onUnmounted(() => store.stopStream())

function newChat() {
  store.createConversation()
}

// 「深度思考」开关持久化：仅影响后续发送（历史消息渲染不变）
function persistReasoning(v) {
  localStorage.setItem('agentReasoning', v ? 'on' : 'off')
}

function removeConversation(c) {
  confirmDialog({
    title: '删除会话',
    message: `确定删除会话「${c.title || '新对话'}」吗？将同时删除其中的消息记录。`,
    confirmText: '删除',
    danger: true
  }).then((ok) => {
    if (ok) store.deleteConversation(c.conversationId)
  })
}

async function send() {
  const q = input.value.trim()
  if (!q || store.streaming) return
  input.value = ''
  sending.value = true
  try {
    await store.dispatch({ query: q })
  } catch (e) {
    notifyError(errMsg(e, '发送失败'))
  } finally {
    sending.value = false
  }
}

// 新会话空状态快捷提示（样式 B）
const quickPrompts = [
  '诊断训练失败原因',
  '检查可部署模型',
  '采集杭州近 7 天天气数据',
  '训练并部署 LSTM 预测模型'
]
async function sendQuick(q) {
  if (store.streaming) return
  sending.value = true
  try {
    await store.dispatch({ query: q })
  } catch (e) {
    notifyError(errMsg(e, '发送失败'))
  } finally {
    sending.value = false
  }
}

async function approve(s) {
  const ok = await confirmDialog({
    title: '确认执行处置',
    message: `确认执行「${actionText(s.actionType)}」(${targetText(s)})？将向 agent 签发临时授权并自动执行，操作可审计。`,
    confirmText: '确认执行',
    danger: true
  })
  if (!ok) return
  try {
    await store.approve(s.suggestionId)
    // 实时监听 execute 事件（tool_call/tool_result/done），落库后轮询兜底刷新
    store.listenExecute(store.currentConversation?.conversationId,
      `正在执行「${actionText(s.actionType)}」…`)
    notifyExecuting()
  } catch (e) {
    notifyError(errMsg(e, '确认失败'))
  }
}

async function reject(s) {
  const ok = await confirmDialog({
    title: '忽略建议',
    message: `确定忽略「${actionText(s.actionType)}」(${targetText(s)}) 吗？`,
    confirmText: '忽略',
    danger: true
  })
  if (!ok) return
  try {
    await store.reject(s.suggestionId)
    store.refreshMessages()
  } catch (e) {
    notifyError(errMsg(e, '操作失败'))
  }
}

// approve 后：execute 是异步任务，延迟轮询拉取结果消息（建议状态 + 执行消息 + plan 进度）
// execute 通常秒级返回，但 worker 忙/写操作慢时可能更久，轮询覆盖到 15s+25s
function notifyExecuting() {
  const cid = store.currentConversation?.conversationId
  if (!cid) return
  ;[1200, 3500, 7000, 15000, 25000].forEach((ms, i) => {
    setTimeout(() => {
      store.refreshMessages(cid)
      // 最后一轮再拉一次建议，确保卡片状态收尾
      if (i === 4) store.fetchSuggestions()
    }, ms)
  })
}

function toggleThinking(m) {
  m._thinkingOpen = !m._thinkingOpen
}

function prettyArgs(args) {
  try {
    const s = JSON.stringify(args)
    return s.length > 60 ? s.slice(0, 60) + '…' : s
  } catch (e) {
    return String(args)
  }
}

/** 建议卡片参数：把 s.params（对象或 JSON 字符串）展开为 [{k, v}] 供 key-value 展示。 */
function paramsEntries(s) {
  let raw = s.params
  if (typeof raw === 'string') {
    try {
      raw = JSON.parse(raw)
    } catch (e) {
      return []
    }
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
  return Object.entries(raw).map(([k, v]) => ({ k, v: fmtParam(v) }))
}

/** 参数值格式化：对象/数组 JSON 化，标量原样。 */
function fmtParam(v) {
  if (v === null || v === undefined) return ''
  if (typeof v === 'object') {
    try {
      return JSON.stringify(v)
    } catch (e) {
      return String(v)
    }
  }
  return String(v)
}

// ===== 文案映射 =====
// 与 agent 工具注册表（approve_<write_tool>）一致；任何新增写工具都需在此登记
// actionType 名称，否则 fallback 到原字符串显示。
const ACTIONS = {
  training_create: { text: '创建训练', icon: 'mdi-rocket-launch' },
  training_delete: { text: '中止训练', icon: 'mdi-stop-circle' },
  serving_deploy: { text: '部署服务', icon: 'mdi-server-plus' },
  serving_undeploy: { text: '下线服务', icon: 'mdi-server-remove' },
  dataset_create: { text: '创建数据集', icon: 'mdi-database-plus' },
  dataset_collect: { text: '采集数据', icon: 'mdi-cloud-download' },
  dataset_update: { text: '更新数据集', icon: 'mdi-database-edit' },
  dataset_delete: { text: '删除数据集', icon: 'mdi-database-remove' }
}
const SUG_STATUS = {
  PENDING: { text: '待确认', color: 'warning' },
  APPROVED: { text: '已授权', color: 'info' },
  REJECTED: { text: '已忽略', color: 'grey' },
  EXECUTING: { text: '执行中', color: 'info' },
  EXECUTED: { text: '已执行', color: 'success' },
  FAILED: { text: '失败', color: 'error' },
  EXPIRED: { text: '已过期', color: 'grey' }
}
const PRIORITIES = { HIGH: { text: '高', color: 'error' }, NORMAL: { text: '中', color: 'warning' }, LOW: { text: '低', color: 'grey' } }
const TARGETS = {
  training_job: '训练任务',
  serving_endpoint: '服务端点',
  dataset: '数据集',
  model_version: '模型版本'
}

function actionText(t) { return ACTIONS[t]?.text || t }
function actionIcon(t) { return ACTIONS[t]?.icon || 'mdi-tune' }
function sugText(s) { return SUG_STATUS[s]?.text || s }
function sugColor(s) { return SUG_STATUS[s]?.color || 'grey' }
function priorityText(p) { return PRIORITIES[p]?.text || p }
function priorityColor(p) { return PRIORITIES[p]?.color || 'grey' }
function targetText(x) { return `${TARGETS[x.targetType] || x.targetType}:${x.targetId}` }

// 执行结果（suggestion 的 markdown result）按条目跟踪折叠/展开状态。
// 建议列表刷新时清空，避免 stale id。
const resultExpanded = reactive({})
function toggleResult(id) { resultExpanded[id] = !resultExpanded[id] }
watch(() => store.suggestions, (list) => {
  const ids = new Set(list.map(s => s.id))
  for (const id of Object.keys(resultExpanded)) {
    if (!ids.has(id)) delete resultExpanded[id]
  }
}, { deep: false })
function planTargetText(st) {
  const tt = st.target_type || st.targetType
  const tid = st.target_id ?? st.targetId
  if (tt == null || tt === '') return ''
  return `${TARGETS[tt] || tt}:${tid ?? 0}`
}

// ===== plan 卡片状态映射 =====
const PLAN_STATUS = {
  PLANNED: { text: '规划中', color: 'info' },
  RUNNING: { text: '执行中', color: 'primary' },
  DONE: { text: '已完成', color: 'success' },
  FAILED: { text: '已失败', color: 'error' },
  CANCELLED: { text: '已废弃', color: 'grey' }
}
const STEP_STATUS = {
  PENDING: { text: '待审批', color: 'warning', icon: 'mdi-clock-outline' },
  APPROVED: { text: '已授权', color: 'info', icon: 'mdi-key-outline' },
  EXECUTING: { text: '执行中', color: 'primary', icon: 'mdi-progress-clock' },
  EXECUTED: { text: '已完成', color: 'success', icon: 'mdi-check-circle-outline' },
  FAILED: { text: '失败', color: 'error', icon: 'mdi-close-circle-outline' },
  REJECTED: { text: '已忽略', color: 'grey', icon: 'mdi-cancel' },
  EXPIRED: { text: '已过期', color: 'grey', icon: 'mdi-clock-alert-outline' },
  CANCELLED: { text: '已取消', color: 'grey', icon: 'mdi-cancel' },
  WAITING: { text: '等待中', color: 'grey', icon: 'mdi-clock-outline' }
}
function planStatusText(s) { return PLAN_STATUS[s]?.text || s }
function planStatusColor(s) { return PLAN_STATUS[s]?.color || 'grey' }
function stepStatusText(s) { return STEP_STATUS[s]?.text || s }
function stepStatusColor(s) { return STEP_STATUS[s]?.color || 'grey' }
function stepStatusIcon(s) { return STEP_STATUS[s]?.icon || 'mdi-circle-outline' }
</script>

<style scoped>
.agent-fab {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 1200;
}
.agent-drawer {
  border-left: 1px solid rgba(0, 0, 0, 0.06);
}
.drawer-resizer {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 8px;
  cursor: col-resize;
  z-index: 20;
  touch-action: none;
  transition: background 0.15s ease;
}
.drawer-resizer:hover,
.drawer-resizer:active {
  background: color-mix(in srgb, rgb(var(--v-theme-primary)) 30%, transparent);
}

/* ---- 消息流 ---- */
.msg-row {
  margin-bottom: 16px;
  display: flex;
}
.msg-row--user {
  justify-content: flex-end;
}
.msg-row--assistant {
  justify-content: flex-start;
}
.msg-bubble {
  max-width: 92%;
  padding: 10px 14px;
  border-radius: 12px;
  word-break: break-word;
}
.msg-bubble--user {
  background: color-mix(in srgb, rgb(var(--v-theme-primary)) 12%, transparent);
  border-top-right-radius: 4px;
}
.msg-bubble--assistant {
  background: rgba(0, 0, 0, 0.03);
  border-top-left-radius: 4px;
}
.msg-text {
  white-space: pre-wrap;
  line-height: 1.6;
}
/* execute 执行中：等待秒数 */
.exec-wait {
  font-size: 12px;
  color: rgba(0, 0, 0, 0.45);
  white-space: nowrap;
}

/* ---- 思考过程折叠 ---- */
.thinking-box {
  margin-bottom: 8px;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.02);
  overflow: hidden;
}
.thinking-head {
  display: flex;
  align-items: center;
  padding: 6px 10px;
  cursor: pointer;
  font-size: 13px;
  color: rgba(0, 0, 0, 0.55);
  user-select: none;
}
.thinking-head:hover {
  background: rgba(0, 0, 0, 0.03);
}
.thinking-body {
  padding: 4px 10px 8px;
  border-top: 1px dashed rgba(0, 0, 0, 0.08);
}
.thinking-text {
  font-size: 12px;
  line-height: 1.7;
  color: rgba(0, 0, 0, 0.45);
  white-space: pre-wrap;
  max-height: 220px;
  overflow-y: auto;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}
.tool-item {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  margin-top: 6px;
  gap: 4px;
}
.tool-args {
  font-size: 11px;
  color: rgba(0, 0, 0, 0.4);
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}
.tool-summary {
  width: 100%;
  padding-left: 20px;
  color: rgba(0, 0, 0, 0.45);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 80px;
  overflow-y: auto;
}

/* ---- 流式光标 ---- */
.streaming-dots span {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 4px;
  border-radius: 50%;
  background: rgb(var(--v-theme-primary));
  animation: dot-bounce 1.2s infinite;
}
.streaming-dots span:nth-child(2) { animation-delay: 0.2s; }
.streaming-dots span:nth-child(3) { animation-delay: 0.4s; }
@keyframes dot-bounce {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.4; }
  40% { transform: translateY(-4px); opacity: 1; }
}

.suggestion-card {
  border-left: 3px solid rgb(var(--v-theme-warning));
}
/* 建议卡片：业务参数 key-value 列表 */
.suggestion-params {
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.03);
  padding: 4px 8px;
}
.suggestion-params .param-key {
  min-width: 96px;
  flex-shrink: 0;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}
.suggestion-params .param-val {
  word-break: break-word;
}

/* ---- plan 卡片 ---- */
.plan-card {
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.02);
}
.plan-step {
  min-height: 24px;
  margin-top: 2px;
}
.plan-step__text {
  font-weight: 500;
}
</style>
