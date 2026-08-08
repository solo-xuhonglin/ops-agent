<template>
  <!-- 全局 AI 助手浮窗：右下 FAB + 右侧抽屉，跨路由常驻（挂在 App.vue）。
       抽屉打开时 FAB 隐藏，避免遮挡抽屉内容 -->
  <div v-if="visible && !store.drawerOpen">
    <!-- FAB：未读建议红点角标 -->
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
      <!-- 头部：标题 + 处置建议/历史任务入口 + 关闭 -->
      <template #prepend>
        <div class="d-flex align-center px-4 py-3">
          <v-icon color="primary">mdi-robot</v-icon>
          <span class="text-title-medium font-weight-bold ml-2">Agent 助手</span>
          <v-spacer />
          <v-tooltip text="处置建议" location="bottom">
            <template #activator="{ props }">
              <v-btn v-bind="props" icon="mdi-clipboard-text-outline" variant="text" size="small"
                     :color="store.activeView === 'history' && historyTab === 'suggestions' ? 'primary' : ''"
                     @click="switchList('suggestions')" />
            </template>
          </v-tooltip>
          <v-tooltip text="历史任务" location="bottom">
            <template #activator="{ props }">
              <v-btn v-bind="props" icon="mdi-history" variant="text" size="small"
                     :color="store.activeView === 'history' && historyTab === 'tasks' ? 'primary' : ''"
                     @click="switchList('tasks')" />
            </template>
          </v-tooltip>
          <v-btn icon="mdi-close" variant="text" size="small" @click="store.closeDrawer()" />
        </div>
        <v-divider />
      </template>

      <div class="d-flex flex-column fill-height">
        <!-- 对话视图 -->
        <div v-if="store.activeView === 'chat'" class="d-flex flex-column fill-height">
          <!-- 输入框上方：PENDING 处置建议卡片（滑动弹出，不打断对话） -->
          <div v-if="store.pendingSuggestions.length" class="px-3 pt-3">
            <v-slide-x-transition group>
              <v-card v-for="s in store.pendingSuggestions.slice(0, 3)" :key="s.id"
                      class="mb-2 suggestion-card" variant="outlined">
                <v-card-text class="py-2">
                  <div class="d-flex align-center mb-1">
                    <v-chip :color="priorityColor(s.priority)" size="x-small">{{ priorityText(s.priority) }}</v-chip>
                    <span class="text-body-small font-weight-bold ml-2">{{ actionText(s.actionType) }}</span>
                    <v-spacer />
                    <span class="text-caption text-medium-emphasis">{{ targetText(s) }}</span>
                  </div>
                  <div class="text-body-small text-medium-emphasis">{{ s.reason }}</div>
                  <div v-if="canWrite" class="mt-2">
                    <v-btn size="small" color="primary" @click="approve(s)">确认</v-btn>
                    <v-btn size="small" variant="text" class="ml-2" @click="reject(s)">忽略</v-btn>
                  </div>
                </v-card-text>
              </v-card>
            </v-slide-x-transition>
          </div>

          <!-- 任务事件时间线（滚动区） -->
          <div class="flex-grow-1 overflow-y-auto pa-4">
            <div v-if="!store.currentTask" class="empty-hint">
              <v-icon icon="mdi-robot-outline" size="48" class="mb-2" />
              <div class="text-body-medium text-medium-emphasis">
                我是 Agent 助手，可以诊断训练任务、服务、数据集与模型。<br />
                在下方输入问题，或从列表页点击「分析」发起诊断。
              </div>
            </div>
            <template v-else>
              <div class="d-flex align-center mb-3">
                <span class="text-body-small font-weight-bold">任务 {{ shortId(store.currentTask.taskId) }}</span>
                <v-chip size="x-small" :color="taskColor(store.currentTask.status)" class="ml-2">
                  {{ taskText(store.currentTask.status) }}
                </v-chip>
                <v-spacer />
                <span class="text-caption text-medium-emphasis">{{ fmtDateTime(store.currentTask.createdAt) }}</span>
              </div>
              <div v-if="store.taskError" class="text-body-small text-error mb-3">{{ store.taskError }}</div>
              <div v-for="(e, i) in store.events" :key="i" class="d-flex align-start mb-3">
                <v-icon :icon="eventIcon(e.eventType)" size="small"
                        :color="eventColor(e.eventType)" class="mr-2" />
                <div>
                  <div class="text-body-small" :class="{ 'text-error': e.eventType === 'error' }">{{ e.content }}</div>
                  <div class="text-caption text-medium-emphasis">{{ fmtDateTime(e.createdAt) }}</div>
                </div>
              </div>
              <v-card v-if="store.currentTask.conclusion" class="conclusion-card mt-2"
                      variant="tonal" color="primary">
                <v-card-text class="text-body-medium conclusion-text">
                  {{ store.currentTask.conclusion }}
                </v-card-text>
              </v-card>
            </template>
          </div>

          <!-- 底部输入框：自然语言问询（多行，回车发送，Shift+回车换行） -->
          <div class="pa-3">
            <v-textarea v-model="input" density="compact" hide-details rows="1" max-rows="3"
                        auto-grow
                        placeholder="询问系统状态，如：最近有哪些异常？"
                        :disabled="sending"
                        @keydown.enter.exact.prevent="send">
              <template #append-inner>
                <v-btn :icon="sending ? undefined : 'mdi-send'"
                       :loading="sending"
                       :disabled="!input.trim()"
                       variant="text" size="small" @click="send" />
              </template>
            </v-textarea>
            <div class="text-caption text-medium-emphasis mt-1 px-1">Enter 发送 · Shift+Enter 换行</div>
          </div>
        </div>

        <!-- 历史视图：处置建议 / 历史任务，由头部两个图标切换 -->
        <div v-else class="d-flex flex-column fill-height">
          <div class="flex-grow-1 overflow-y-auto">
            <!-- 建议列表 -->
            <div v-if="historyTab === 'suggestions'" class="pa-2">
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
                <div v-else-if="s.status === 'EXECUTED' && s.result"
                     class="history-item__body text-caption text-success history-item__clamp-3" :title="s.result">{{ s.result }}</div>
              </div>
              <div v-if="!store.suggestions.length" class="empty-hint">
                <v-icon icon="mdi-inbox-outline" size="40" class="mb-2" />
                <div class="text-body-medium text-medium-emphasis">暂无处置建议</div>
              </div>
            </div>
            <!-- 任务列表 -->
            <div v-else class="pa-2">
              <div v-for="t in store.tasks" :key="t.taskId"
                   class="history-item history-item--compact history-item--clickable mb-1"
                   :class="{ 'history-item--active': store.currentTask?.taskId === t.taskId }"
                   @click="store.selectTask(t.taskId)">
                <div class="history-item__head">
                  <v-avatar color="primary" size="24" variant="tonal">
                    <v-icon size="14">mdi-robot</v-icon>
                  </v-avatar>
                  <span class="history-item__title">{{ taskTypeText(t.taskType) }}</span>
                  <v-chip size="x-small" :color="taskColor(t.status)">{{ taskText(t.status) }}</v-chip>
                  <span class="history-item__time">{{ fmtDateTime(t.createdAt) }}</span>
                </div>
                <div class="history-item__body">{{ t.query || targetText(t) }}</div>
              </div>
              <div v-if="!store.tasks.length" class="empty-hint">
                <v-icon icon="mdi-history" size="40" class="mb-2" />
                <div class="text-body-medium text-medium-emphasis">暂无历史任务</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </v-navigation-drawer>
</template>

<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useAgentStore } from '../stores/agent'
import { useConfirm } from '../composables/useConfirm'
import { useNotify, errMsg } from '../composables/useNotify'
import { fmtDateTime, shortId } from '../utils/format'

const auth = useAuthStore()
const store = useAgentStore()
const { confirmDialog } = useConfirm()
const { notifyError } = useNotify()

const visible = computed(() => auth.isLoggedIn)
const canWrite = computed(() => auth.hasPerm('agent:write'))

const input = ref('')
const sending = ref(false)
const historyTab = ref('suggestions')

// 头部两个图标：切到对应列表；再次点击当前列表图标回到对话视图
function switchList(tab) {
  if (store.activeView === 'history' && historyTab.value === tab) {
    store.activeView = 'chat'
  } else {
    store.activeView = 'history'
    historyTab.value = tab
    store.refreshAll()
  }
}

// ===== 抽屉宽度：左缘拖拽调整（320~760），持久化到 localStorage =====
// 跟手的关键：拖动期间直接改 DOM（同步写入，零 Vue 重渲染），松手后才提交 ref
const MIN_W = 320
const MAX_W = 760
const drawerEl = ref(null)
const drawerWidth = ref(Number(localStorage.getItem('agentDrawerWidth')) || 430)

// 取抽屉根 DOM：优先直接查 DOM（组件实例的 $el 在此环境解析不稳定）
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

  // 拖动中禁用过渡，避免宽度变化被动画拖后腿
  if (drawer) drawer.style.transition = 'none'

  const onMove = (ev) => {
    if (!drawer) return
    // 抽屉在右侧：手柄向左拖 → 宽度增大
    const w = Math.min(MAX_W, Math.max(MIN_W, startW + (startX - ev.clientX)))
    drawer.style.width = `${w}px`   // 直接写 DOM：视觉即时跟手
    drawerWidth.value = w           // 同步 ref：轮询重渲染时宽度不会跳回
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
      // 关键：不要把 inline width 清空！清空瞬间 Vuetify 抽屉的 CSS 默认
      // （width: 100%）生效会把抽屉撑到全屏（1440px）造成闪跳。
      // 保留 inline width 作为最终值，与 prop 同步即可。
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

// ===== 轮询：抽屉打开时 3s 刷新建议/任务/当前任务，关闭即停 =====
let timer = null
watch(() => store.drawerOpen, (open) => {
  if (timer) clearInterval(timer)
  timer = null
  if (open) {
    store.refreshAll()
    timer = setInterval(() => {
      store.refreshAll()
      store.refreshCurrentTask()
    }, 3000)
  }
})
onUnmounted(() => { if (timer) clearInterval(timer) })

async function send() {
  const q = input.value.trim()
  if (!q || sending.value) return
  input.value = ''
  sending.value = true
  try {
    await store.dispatchQuestion(q)
  } catch (e) {
    notifyError(errMsg(e, '任务派发失败'))
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
    await store.approve(s.id)
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
    await store.reject(s.id)
  } catch (e) {
    notifyError(errMsg(e, '操作失败'))
  }
}

// ===== 文案映射 =====
const ACTIONS = {
  training_create: { text: '创建训练', icon: 'mdi-rocket-launch' },
  training_delete: { text: '中止训练', icon: 'mdi-stop-circle' },
  serving_deploy: { text: '部署服务', icon: 'mdi-server-plus' },
  serving_undeploy: { text: '下线服务', icon: 'mdi-server-remove' }
}
const TASK_TYPES = {
  question: '问询',
  diagnose_training: '训练诊断',
  diagnose_serving: '服务诊断',
  diagnose_dataset: '数据集诊断',
  model_review: '模型评估'
}
const TASK_STATUS = {
  DISPATCHED: { text: '已派发', color: 'grey' },
  RUNNING: { text: '执行中', color: 'info' },
  SUCCEEDED: { text: '成功', color: 'success' },
  FAILED: { text: '失败', color: 'error' },
  CANCELLED: { text: '已取消', color: 'warning' }
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
function taskTypeText(t) { return TASK_TYPES[t] || t || '问询' }
function taskText(s) { return TASK_STATUS[s]?.text || s || '未知' }
function taskColor(s) { return TASK_STATUS[s]?.color || 'grey' }
function sugText(s) { return SUG_STATUS[s]?.text || s }
function sugColor(s) { return SUG_STATUS[s]?.color || 'grey' }
function priorityText(p) { return PRIORITIES[p]?.text || p }
function priorityColor(p) { return PRIORITIES[p]?.color || 'grey' }
function targetText(x) { return `${TARGETS[x.targetType] || x.targetType}:${x.targetId}` }
function eventIcon(t) { return { progress: 'mdi-progress-clock', tool_call: 'mdi-wrench', error: 'mdi-alert-circle' }[t] || 'mdi-circle-small' }
function eventColor(t) { return { progress: 'primary', tool_call: 'info', error: 'error' }[t] || 'grey' }
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
  touch-action: none; /* 触屏拖动不触发滚动 */
  transition: background 0.15s ease;
}
.drawer-resizer:hover,
.drawer-resizer:active {
  background: color-mix(in srgb, rgb(var(--v-theme-primary)) 30%, transparent);
}
.suggestion-card {
  border-left: 3px solid rgb(var(--v-theme-warning));
}
.conclusion-card {
  border-left: 3px solid rgb(var(--v-theme-primary));
}
.conclusion-text {
  white-space: pre-wrap;
  line-height: 1.6;
}
</style>
