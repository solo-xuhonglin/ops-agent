<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-title-large list-title">模型服务</h2>
      <v-spacer />
      <v-chip v-if="hasActive" color="info" size="small" class="mr-2">
        <v-progress-circular indeterminate size="14" width="2" class="mr-1" />轮询中
      </v-chip>
      <v-btn v-if="canWrite" variant="tonal" color="primary" prepend-icon="mdi-rocket-launch" @click="openDeploy">部署模型</v-btn>
      <v-btn variant="text" prepend-icon="mdi-refresh" @click="load">刷新</v-btn>
    </div>

    <v-card rounded="lg" class="data-card" elevation="0">
      <v-data-table-server
        :headers="headers"
        :items="items"
        :loading="loading"
        :items-length="total"
        :items-per-page="pageSize"
        @update:options="onOptions"
      >
        <template #item.status="{ item }">
          <v-chip :color="statusColor(item.status)" size="small">{{ statusText(item.status) }}</v-chip>
        </template>
        <template #item.url="{ item }">
          <span v-if="item.url" class="text-body-small text-medium-emphasis">{{ item.url }}</span>
          <span v-else class="text-medium-emphasis">—</span>
        </template>
        <template #item.createdAt="{ item }">
          {{ fmt(item.createdAt) }}
        </template>
        <template #item.actions="{ item }">
          <v-btn icon="mdi-chart-line" size="small" variant="text" color="primary"
                 title="测试推理" :disabled="item.status !== 'DEPLOYED'" @click="openTest(item)" />
          <v-btn v-if="canWrite" icon="mdi-stop-circle" size="small" variant="text" color="error"
                 title="下线" :disabled="['STOPPED', 'FAILED', 'STOPPING'].includes(item.status)" @click="undeploy(item)" />
        </template>
      </v-data-table-server>
    </v-card>

    <!-- 部署：选择 READY 模型 -->
    <v-dialog v-model="deployDialog" max-width="480">
      <v-card rounded="lg">
        <v-card-title>部署模型</v-card-title>
        <v-card-text>
          <v-select
            v-model="deployModelId"
            :items="readyModels"
            item-title="label"
            item-value="value"
            label="模型版本（仅 READY）"
            density="compact"
            variant="outlined"
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
      <v-card rounded="lg">
        <v-card-title>测试推理 · endpoint #{{ testItem?.id }}</v-card-title>
        <v-card-text>
          <v-textarea
            v-model="testValues"
            label="历史气温序列（逗号分隔，单位 ℃）"
            hint="例如: 20.1,20.5,21.0,20.8,19.9,20.3"
            rows="3"
            density="compact"
            variant="outlined"
          />
          <v-text-field
            v-model="testHorizon"
            label="预测步数 horizon（1-168）"
            type="number"
            density="compact"
            variant="outlined"
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
import api from '../../plugins/axios'
import { useConfirm } from '../../composables/useConfirm'

const auth = useAuthStore()
const { confirmDialog } = useConfirm()
const canWrite = computed(() => auth.hasPerm('serving:write'))

const headers = [
  { title: 'ID', key: 'id', width: 70 },
  { title: '模型版本', key: 'modelVersionId', width: 110 },
  { title: '状态', key: 'status', width: 120 },
  { title: '访问地址', key: 'url' },
  { title: '创建时间', key: 'createdAt', width: 170 },
  { title: '操作', key: 'actions', sortable: false, width: 110 }
]

const items = ref([])
const total = ref(0)
const loading = ref(false)
const pageSize = ref(20)
const page = ref(0)

const hasActive = computed(() => items.value.some(ep => ['CREATING', 'STOPPING'].includes(ep.status)))

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/serving/endpoints', { params: { page: page.value, size: pageSize.value } })
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
function fmt(d) {
  if (!d) return '—'
  return new Date(d).toLocaleString()
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
    alert(e.response?.data?.message || '加载模型列表失败')
  } finally { modelsLoading.value = false }
}

async function doDeploy() {
  deploying.value = true
  try {
    await api.post('/serving/deploy', { modelVersionId: deployModelId.value })
    deployDialog.value = false
    load()
  } catch (e) {
    alert(e.response?.data?.message || '部署失败')
  } finally { deploying.value = false }
}

async function undeploy(item) {
  const ok = await confirmDialog({
    title: '下线模型服务',
    message: `确定要下线 endpoint #${item.id}（模型版本 #${item.modelVersionId}）吗？对应容器将被停止并删除。`,
    confirmText: '下线',
    danger: true
  })
  if (!ok) return
  try {
    await api.delete(`/serving/endpoints/${item.id}`)
    load()
  } catch (e) {
    alert(e.response?.data?.message || '下线失败')
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
    const { data } = await api.post(`/serving-proxy/${testItem.value.id}/predict`, { values, horizon })
    testResult.value = data.data.predictions
  } catch (e) {
    testError.value = e.response?.data?.message || '推理调用失败'
  } finally { testing.value = false }
}

onMounted(() => { load(); timer = setInterval(tick, 5000) })
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>
