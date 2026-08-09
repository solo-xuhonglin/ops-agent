<template>
  <div>
    <div class="page-toolbar">
      <v-chip v-if="hasActive" color="info">
        <v-progress-circular indeterminate size="14" width="2" class="mr-1" />轮询中
      </v-chip>
      <v-spacer />
      <v-select
        v-model="filterStatus"
        :items="statusOptions"
        label="状态"
        density="compact"
        clearable
        hide-details
        style="max-width: 160px"
        @update:model-value="load"
      />
      <v-btn variant="text" prepend-icon="mdi-refresh" @click="load">刷新</v-btn>
    </div>

    <v-card class="data-card" elevation="0">
      <v-data-table-server
        :headers="headers"
        :items="items"
        :loading="loading"
        :items-length="total"
        :items-per-page="pageSize"
        @update:options="onOptions"
      >
        <template #item.status="{ item }">
          <v-chip :color="statusColor(item.status)">{{ statusText(item.status) }}</v-chip>
        </template>
        <template #item.startedAt="{ item }">
          {{ fmtDateTime(item.startedAt) }}
        </template>
        <template #item.finishedAt="{ item }">
          {{ fmtDateTime(item.finishedAt) }}
        </template>
        <template #item.actions="{ item }">
          <div class="row-actions">
            <v-btn v-if="canAgent" icon="mdi-robot" size="small" variant="text" color="primary"
                   title="让 Agent 分析" @click="analyze(item)" />
            <v-btn icon="mdi-file-document-outline" size="small" variant="text" color="primary"
                   title="查看日志" :disabled="!item.logKey" @click="openLog(item)" />
            <v-btn v-if="canWrite" icon="mdi-delete" size="small" variant="text" color="error"
                   title="删除" @click="remove(item)" />
          </div>
        </template>
      </v-data-table-server>
    </v-card>

    <v-dialog v-model="logDialog" max-width="900">
      <v-card>
        <v-card-title class="d-flex align-center">
          训练日志
          <v-spacer />
          <v-chip variant="tonal">{{ logItem?.id }}</v-chip>
        </v-card-title>
        <v-card-text style="height: 60vh">
          <iframe v-if="logUrl" :src="logUrl" class="log-frame" />
          <div v-else class="text-medium-emphasis text-center py-8">日志生成中，稍后重试</div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="logDialog = false">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { useAgentStore } from '../../stores/agent'
import api from '../../plugins/axios'
import { useConfirm } from '../../composables/useConfirm'
import { useNotify, errMsg } from '../../composables/useNotify'
import { fmtDateTime } from '../../utils/format'

const auth = useAuthStore()
const agentStore = useAgentStore()
const { confirmDialog } = useConfirm()
const { notifyError } = useNotify()
const canWrite = computed(() => auth.hasPerm('training:write'))
const canAgent = computed(() => auth.hasPerm('agent:read'))

function analyze(item) {
  agentStore.dispatchDiagnose({ taskType: 'diagnose_training', targetType: 'training_job', targetId: item.id })
}

const headers = [
  { title: 'ID', key: 'id', width: 70 },
  { title: '数据集', key: 'datasetId', width: 110 },
  { title: '模型版本', key: 'modelVersionId', width: 110 },
  { title: '状态', key: 'status', width: 120 },
  { title: '开始时间', key: 'startedAt', width: 150 },
  { title: '结束时间', key: 'finishedAt', width: 150 },
  { title: '操作', key: 'actions', sortable: false, width: 130 }
]

const items = ref([])
const total = ref(0)
const loading = ref(false)
const pageSize = ref(10)
const page = ref(0)
const filterStatus = ref(null)
const statusOptions = ['PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED']

const hasActive = computed(() => items.value.some(j => ['PENDING', 'RUNNING'].includes(j.status)))

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/training/jobs', { params: { page: page.value, size: pageSize.value, status: filterStatus.value || undefined } })
    items.value = data.data.content
    total.value = data.data.totalElements
  } finally { loading.value = false }
}
function onOptions(o) { page.value = o.page - 1; pageSize.value = o.itemsPerPage; load() }

function statusText(s) {
  return { PENDING: '排队中', RUNNING: '运行中', SUCCEEDED: '成功', FAILED: '失败' }[s] || s || '未知'
}
function statusColor(s) {
  return { PENDING: 'warning', RUNNING: 'info', SUCCEEDED: 'success', FAILED: 'error' }[s] || 'grey'
}

// 每 5s 轮询：仅在有活跃任务时拉取，避免无谓请求
let timer = null
function tick() {
  if (hasActive.value) load()
}

const logDialog = ref(false)
const logItem = ref(null)
const logUrl = ref('')
async function openLog(item) {
  logItem.value = item
  logUrl.value = ''
  logDialog.value = true
  try {
    const { data } = await api.get(`/training/jobs/${item.id}/logs`)
    logUrl.value = data.data.url
  } catch (e) {
    notifyError(errMsg(e, '获取日志失败'))
  }
}

async function remove(item) {
  const ok = await confirmDialog({
    title: '删除训练任务',
    message: `确定要删除训练任务 #${item.id} 吗？关联的训练日志将一并清理，运行中的容器会被停止。`,
    confirmText: '删除',
    danger: true
  })
  if (!ok) return
  try {
    await api.delete(`/training/jobs/${item.id}`)
    load()
  } catch (e) {
    notifyError(errMsg(e, '删除失败'))
  }
}

onMounted(() => { load(); timer = setInterval(tick, 5000) })
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>
