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
      <v-btn v-if="canWrite" color="primary" prepend-icon="mdi-rocket-launch" @click="openDeploy">部署模型</v-btn>
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
        <template #item.url="{ item }">
          <span v-if="item.url" class="text-body-small text-medium-emphasis">{{ item.url }}</span>
          <span v-else class="text-medium-emphasis">—</span>
        </template>
        <template #item.createdAt="{ item }">
          {{ fmtDateTime(item.createdAt) }}
        </template>
        <template #item.actions="{ item }">
          <div class="row-actions">
            <v-btn v-if="canAgent" icon="mdi-robot" size="small" variant="text" color="primary"
                   title="让 Agent 分析" @click="analyze(item)" />
            <v-btn icon="mdi-chart-line" size="small" variant="text" color="primary"
                   title="测试推理" :disabled="item.status !== 'DEPLOYED'" @click="openTest(item)" />
            <v-btn v-if="canWrite" icon="mdi-stop-circle" size="small" variant="text" color="warning"
                   title="下线" :disabled="['STOPPED', 'FAILED', 'STOPPING'].includes(item.status)" @click="undeploy(item)" />
            <v-btn v-if="canWrite" icon="mdi-delete" size="small" variant="text" color="error"
                   title="删除" @click="remove(item)" />
          </div>
        </template>
      </v-data-table-server>
    </v-card>

    <!-- 部署：选择 READY 模型 -->
    <v-dialog v-model="deployDialog" max-width="480">
      <v-card>
        <v-card-title>部署模型</v-card-title>
        <v-card-text>
          <v-select
            v-model="deployModelId"
            :items="readyModels"
            item-title="label"
            item-value="value"
            label="模型版本（仅 READY）"
            density="compact"
            :loading="modelsLoading"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="deployDialog = false">取消</v-btn>
          <v-btn color="primary" :loading="deploying" :disabled="!deployModelId" @click="doDeploy">部署</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 测试推理 -->
    <v-dialog v-model="testDialog" max-width="640">
      <v-card>
        <v-card-title>测试推理 · endpoint #{{ testItem?.id }}</v-card-title>
        <v-card-text>
          <v-textarea
            v-model="testValues"
            label="历史气温序列（逗号分隔，单位 ℃）"
            hint="例如: 20.1,20.5,21.0,20.8,19.9,20.3"
            rows="3"
            density="compact"
          />
          <v-text-field
            v-model="testHorizon"
            label="预测步数 horizon（1-168）"
            type="number"
            density="compact"
            class="mt-2"
          />
          <v-alert v-if="testResult" variant="tonal" color="primary" class="mt-3">
            <div class="text-body-small text-medium-emphasis mb-1">预测结果（长度 {{ testResult.length }}）</div>
            <div class="text-body-medium">{{ testResult.join(', ') }}</div>
          </v-alert>
          <v-alert v-if="testError" type="error" variant="tonal" class="mt-3">{{ testError }}</v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="testDialog = false">关闭</v-btn>
          <v-btn color="primary" :loading="testing" @click="doTest">预测</v-btn>
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
const canWrite = computed(() => auth.hasPerm('serving:write'))
const canAgent = computed(() => auth.hasPerm('agent:read'))

function analyze(item) {
  agentStore.dispatchDiagnose({ taskType: 'diagnose_serving', targetType: 'serving_endpoint', targetId: item.id })
}

const headers = [
  { title: 'ID', key: 'id', width: 70 },
  { title: '模型版本', key: 'modelVersionId', width: 110 },
  { title: '状态', key: 'status', width: 120 },
  { title: '访问地址', key: 'url' },
  { title: '创建时间', key: 'createdAt', width: 150 },
  { title: '操作', key: 'actions', sortable: false, width: 160 }
]

const items = ref([])
const total = ref(0)
const loading = ref(false)
const pageSize = ref(10)
const page = ref(0)
const filterStatus = ref(null)
const statusOptions = ['CREATING', 'DEPLOYED', 'UNHEALTHY', 'STOPPING', 'STOPPED', 'FAILED']

const hasActive = computed(() => items.value.some(ep => ['CREATING', 'STOPPING'].includes(ep.status)))

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/serving/endpoints', { params: { page: page.value, size: pageSize.value, status: filterStatus.value || undefined } })
    items.value = data.data.content
    total.value = data.data.totalElements
  } finally { loading.value = false }
}
function onOptions(o) { page.value = o.page - 1; pageSize.value = o.itemsPerPage; load() }

function statusText(s) {
  return { CREATING: '部署中', DEPLOYED: '运行中', UNHEALTHY: '异常', STOPPING: '下线中', STOPPED: '已下线', FAILED: '失败' }[s] || s || '未知'
}
function statusColor(s) {
  return { CREATING: 'info', DEPLOYED: 'success', UNHEALTHY: 'warning', STOPPING: 'info', STOPPED: 'grey', FAILED: 'error' }[s] || 'grey'
}

// 每 5s 轮询：仅在有活跃状态（部署中/下线中）时拉取
let timer = null
function tick() {
  if (hasActive.value) load()
}

// ---- 部署 ----
const deployDialog = ref(false)
const deployModelId = ref(null)
const deploying = ref(false)
const modelsLoading = ref(false)
const readyModels = ref([])

async function openDeploy() {
  deployDialog.value = true
  deployModelId.value = null
  modelsLoading.value = true
  try {
    const { data } = await api.get('/models', { params: { page: 0, size: 100 } })
    readyModels.value = (data.data.content || [])
      .filter(m => m.status === 'READY')
      .map(m => ({ label: `${m.name} (${m.version}) · #${m.id}`, value: m.id }))
  } catch (e) {
    notifyError(errMsg(e, '加载模型列表失败'))
  } finally { modelsLoading.value = false }
}

async function doDeploy() {
  deploying.value = true
  try {
    await api.post('/serving/endpoints/deploy', { modelVersionId: deployModelId.value })
    deployDialog.value = false
    load()
  } catch (e) {
    notifyError(errMsg(e, '部署失败'))
  } finally { deploying.value = false }
}

async function undeploy(item) {
  const ok = await confirmDialog({
    title: '下线模型服务',
    message: `确定要下线 endpoint #${item.id}（模型版本 #${item.modelVersionId}）吗？对应容器将被停止并删除，记录保留。`,
    confirmText: '下线'
  })
  if (!ok) return
  try {
    await api.post(`/serving/endpoints/${item.id}/undeploy`)
    load()
  } catch (e) {
    notifyError(errMsg(e, '下线失败'))
  }
}

async function remove(item) {
  const ok = await confirmDialog({
    title: '删除模型服务记录',
    message: `确定要删除 endpoint #${item.id} 的记录吗？关联容器将被停止并删除，此操作不可恢复。`,
    confirmText: '删除',
    danger: true
  })
  if (!ok) return
  try {
    await api.delete(`/serving/endpoints/${item.id}`)
    load()
  } catch (e) {
    notifyError(errMsg(e, '删除失败'))
  }
}

// ---- 测试推理 ----
const testDialog = ref(false)
const testItem = ref(null)
const testValues = ref('')
const testHorizon = ref(1)
const testResult = ref(null)
const testError = ref('')
const testing = ref(false)

function openTest(item) {
  testItem.value = item
  testValues.value = ''
  testHorizon.value = 1
  testResult.value = null
  testError.value = ''
  testDialog.value = true
}

async function doTest() {
  const values = testValues.value.split(/[,，\s]+/).filter(Boolean).map(Number)
  if (!values.length || values.some(v => Number.isNaN(v))) {
    testError.value = '请输入合法的数值序列（逗号分隔）'
    return
  }
  const horizon = Number(testHorizon.value) || 1
  testing.value = true
  testError.value = ''
  testResult.value = null
  try {
    const { data } = await api.post(`/serving/endpoints/${testItem.value.id}/predict`, { values, horizon })
    testResult.value = data.data.predictions
  } catch (e) {
    testError.value = errMsg(e, '推理调用失败')
  } finally { testing.value = false }
}

onMounted(() => { load(); timer = setInterval(tick, 5000) })
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>
