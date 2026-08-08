<template>
  <!-- 全局 AI 助手浮窗：右下 FAB + 右侧抽屉，跨路由常驻（挂在 App.vue） -->
  <div v-if="visible">
    <!-- FAB：未读建议红点角标 -->
    <v-badge :content="store.pendingCount" :model-value="store.pendingCount > 0" color="error">
      <v-btn class="agent-fab" color="primary" size="large" elevation="4"
             :icon="store.drawerOpen ? 'mdi-robot' : 'mdi-robot-outline'"
             @click="store.toggleDrawer()" />
    </v-badge>

    <v-navigation-drawer v-model="store.drawerOpen" location="right" temporary
                         width="430" class="agent-drawer">
      <!-- 头部：标题 + 历史/对话切换 + 关闭 -->
      <template #prepend>
        <div class="d-flex align-center px-4 py-3">
          <v-icon color="primary">mdi-robot</v-icon>
          <span class="text-title-medium font-weight-bold ml-2">Agent 助手</span>
          <v-spacer />
          <v-tooltip text="历史记录" location="bottom">
            <template #activator="{ props }">
              <v-btn v-bind="props" icon="mdi-history" variant="text" size="small"
                     :color="store.activeView === 'history' ? 'primary' : ''"
                     @click="store.setView(store.activeView === 'history' ? 'chat' : 'history')" />
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
                <span class="text-caption text-medium-emphasis">{{ fmt(store.currentTask.createdAt) }}</span>
              </div>
              <div v-if="store.taskError" class="text-body-small text-error mb-3">{{ store.taskError }}</div>
              <div v-for="(e, i) in store.events" :key="i" class="d-flex mb-3">
                <v-icon :icon="eventIcon(e.eventType)" size="small"
                        :color="eventColor(e.eventType)" class="mt-0.5 mr-2" />
                <div>
                  <div class="text-body-small" :class="{ 'text-error': e.eventType === 'error' }">{{ e.content }}</div>
                  <div class="text-caption text-medium-emphasis">{{ fmt(e.createdAt) }}</div>
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

          <!-- 底部输入框：自然语言问询 -->
          <div class="pa-3">
            <v-text-field v-model="input" density="compact" variant="outlined" hide-details
                          placeholder="询问系统状态，如：最近有哪些异常？"
                          :disabled="sending"
                          :append-inner-icon="sending ? '' : 'mdi-send'"
                          @click:append-inner="send" @keydown.enter="send">
              <template v-if="sending" #append-inner>
                <v-progress-circular indeterminate size="18" width="2" />
              </template>
            </v-text-field>
          </div>
        </div>

        <!-- 历史视图 -->
        <div v-else class="d-flex flex-column fill-height">
          <v-tabs v-model="historyTab" density="compact" class="px-2">
            <v-tab value="suggestions">处置建议</v-tab>
            <v-tab value="tasks">历史任务</v-tab>
          </v-tabs>
          <v-window v-model="historyTab" class="flex-grow-1 overflow-y-auto">
            <!-- 建议列表 -->
            <v-window-item value="suggestions">
              <v-list density="comfortable" class="pa-2">
                <v-list-item v-for="s in store.suggestions" :key="s.id" class="mb-1 rounded-lg"
                             :class="s.status === 'PENDING' ? 'pending-item' : ''">
                  <template #prepend>
                    <v-avatar :color="priorityColor(s.priority)" size="28" variant="tonal">
                      <v-icon size="16" :color="priorityColor(s.priority)">{{ actionIcon(s.actionType) }}</v-icon>
                    </v-avatar>
                  </template>
                  <div class="d-flex align-center">
                    <span class="text-body-small font-weight-bold">{{ actionText(s.actionType) }}</span>
                    <v-chip size="x-small" :color="sugColor(s.status)" class="ml-2">{{ sugText(s.status) }}</v-chip>
                  </div>
                  <div class="text-caption text-medium-emphasis">{{ targetText(s) }}</div>
                  <div v-if="s.reason" class="text-body-small text-medium-emphasis mt-1">{{ s.reason }}</div>
                  <template #append>
                    <div class="d-flex flex-column align-end">
                      <span class="text-caption text-medium-emphasis">{{ fmt(s.createdAt) }}</span>
                      <div v-if="s.status === 'PENDING' && canWrite" class="mt-1">
                        <v-btn size="x-small" color="primary" variant="tonal" @click="approve(s)">确认</v-btn>
                        <v-btn size="x-small" variant="text" @click="reject(s)">忽略</v-btn>
                      </div>
                      <span v-else-if="s.status === 'EXECUTED' && s.result" class="text-caption text-success mt-1">
                        {{ s.result.slice(0, 40) }}
                      </span>
                    </div>
                  </template>
                </v-list-item>
                <div v-if="!store.suggestions.length" class="text-center text-medium-emphasis py-8">
                  暂无处置建议
                </div>
              </v-list>
            </v-window-item>
            <!-- 任务列表 -->
            <v-window-item value="tasks">
              <v-list density="comfortable" class="pa-2">
                <v-list-item v-for="t in store.tasks" :key="t.taskId" class="mb-1 rounded-lg"
                             :active="store.currentTask?.taskId === t.taskId" color="primary"
                             @click="store.selectTask(t.taskId)">
                  <template #prepend>
                    <v-avatar color="primary" size="28" variant="tonal">
                      <v-icon size="16">mdi-robot</v-icon>
                    </v-avatar>
                  </template>
                  <div class="d-flex align-center">
                    <span class="text-body-small font-weight-bold">{{ taskTypeText(t.taskType) }}</span>
                    <v-chip size="x-small" :color="taskColor(t.status)" class="ml-2">{{ taskText(t.status) }}</v-chip>
                  </div>
                  <div class="text-caption text-medium-emphasis text-truncate">{{ t.query || targetText(t) }}</div>
                  <template #append>
                    <span class="text-caption text-medium-emphasis">{{ fmt(t.createdAt) }}</span>
                  </template>
                </v-list-item>
                <div v-if="!store.tasks.length" class="text-center text-medium-emphasis py-8">
                  暂无历史任务
                </div>
              </v-list>
            </v-window-item>
          </v-window>
        </div>
      </div>
    </v-navigation-drawer>
  </div>
</template>

<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useAgentStore } from '../stores/agent'
import { useConfirm } from '../composables/useConfirm'

const auth = useAuthStore()
const store = useAgentStore()
const { confirmDialog } = useConfirm()

const visible = computed(() => auth.isLoggedIn && auth.hasPerm('agent:read'))
const canWrite = computed(() => auth.hasPerm('agent:write'))

const input = ref('')
const sending = ref(false)
const historyTab = ref('suggestions')

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
    alert(e.response?.data?.message || '任务派发失败')
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
    alert(e.response?.data?.message || '确认失败')
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
    alert(e.response?.data?.message || '操作失败')
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
function shortId(id) { return id ? id.slice(0, 8) : '' }
function fmt(d) {
  if (!d) return '—'
  return new Date(d).toLocaleString()
}
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
.suggestion-card {
  border-left: 3px solid rgb(var(--v-theme-warning));
}
.pending-item {
  background: color-mix(in srgb, rgb(var(--v-theme-warning)) 6%, transparent);
}
.conclusion-card {
  border-left: 3px solid rgb(var(--v-theme-primary));
}
.conclusion-text {
  white-space: pre-wrap;
  line-height: 1.6;
}
.empty-hint {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  height: 100%;
  color: rgb(var(--v-theme-on-surface-variant));
}
</style>
