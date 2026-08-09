<template>
  <div>
    <div class="page-toolbar">
      <v-spacer />
      <v-btn v-if="canTrain" color="primary" prepend-icon="mdi-rocket-launch" @click="openTrain">训练模型</v-btn>
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
        <template #item.metrics="{ item }">
          <span v-if="metricsOf(item)">
            MAE {{ metricsOf(item).mae }} · RMSE {{ metricsOf(item).rmse }}
          </span>
          <span v-else class="text-medium-emphasis">—</span>
        </template>
        <template #item.createdAt="{ item }">
          {{ fmtDateTime(item.createdAt) }}
        </template>
        <template #item.actions="{ item }">
          <div class="row-actions">
            <v-btn v-if="canAgent" icon="mdi-robot" size="small" variant="text" color="primary" title="让 Agent 分析" @click="analyze(item)" />
            <v-btn icon="mdi-information" size="small" variant="text" color="primary" title="详情" @click="openDetail(item)" />
            <v-btn icon="mdi-download" size="small" variant="text" color="secondary" title="下载模型" @click="download(item)" />
            <v-btn v-if="canServe && item.status === 'READY'" icon="mdi-rocket-launch" size="small" variant="text" color="primary"
                   title="部署服务" @click="deploy(item)" />
            <v-btn v-if="canWrite" icon="mdi-delete" size="small" variant="text" color="error" title="删除" @click="remove(item)" />
          </div>
        </template>
      </v-data-table-server>
    </v-card>

    <v-dialog v-model="detailDialog">
      <v-card>
        <v-card-title>模型详情 · {{ detailItem?.name }}</v-card-title>
        <v-card-text>
          <v-row density="compact">
            <v-col cols="6"><div class="text-body-small text-medium-emphasis">版本</div><div>{{ detailItem?.version }}</div></v-col>
            <v-col cols="6"><div class="text-body-small text-medium-emphasis">算法</div><div>{{ detailItem?.algorithm }}</div></v-col>
            <v-col cols="6"><div class="text-body-small text-medium-emphasis">关联数据集</div><div>#{{ detailItem?.datasetId }}</div></v-col>
            <v-col cols="6"><div class="text-body-small text-medium-emphasis">状态</div><div>{{ statusText(detailItem?.status) }}</div></v-col>
          </v-row>
          <div class="text-body-large mt-4 mb-2">训练指标</div>
          <div v-if="metricsOf(detailItem)" class="d-flex flex-wrap ga-2">
            <v-chip variant="tonal" color="primary">MAE {{ metricsOf(detailItem).mae }}</v-chip>
            <v-chip variant="tonal" color="primary">RMSE {{ metricsOf(detailItem).rmse }}</v-chip>
            <v-chip variant="tonal">训练Loss {{ metricsOf(detailItem).train_loss }}</v-chip>
            <v-chip variant="tonal">epochs {{ metricsOf(detailItem).epochs }}</v-chip>
            <v-chip variant="tonal">hidden {{ metricsOf(detailItem).hidden_size }}</v-chip>
            <v-chip variant="tonal">seqLen {{ metricsOf(detailItem).seq_len }}</v-chip>
          </div>
          <div v-else class="text-medium-emphasis">暂无指标（训练可能未完成或失败）</div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="detailDialog = false">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 训练模型：选择 READY 数据集 -->
    <v-dialog v-model="trainDialog" max-width="620">
      <v-card>
        <v-card-title>发起训练</v-card-title>
        <v-card-text>
          <v-select
            v-model="trainForm.datasetId"
            :items="readyDatasets"
            item-title="label"
            item-value="value"
            label="训练数据集（仅就绪）"
            density="compact"
            :loading="datasetsLoading"
            :rules="[(v) => !!v || '请选择数据集']"
          />
          <v-text-field v-model="trainForm.name" label="模型名称" :rules="[(v) => !!v || '必填']" />
          <v-row>
            <v-col cols="6">
              <v-text-field v-model="trainForm.version" label="版本号" />
            </v-col>
            <v-col cols="6">
              <v-select v-model="trainForm.algorithm" :items="['LSTM']" label="算法" />
            </v-col>
          </v-row>
          <div class="text-body-large mb-2">超参数</div>
          <v-row>
            <v-col cols="4"><v-text-field v-model.number="trainForm.seqLen" label="seqLen" type="number" density="compact" /></v-col>
            <v-col cols="4"><v-text-field v-model.number="trainForm.hiddenSize" label="hiddenSize" type="number" density="compact" /></v-col>
            <v-col cols="4"><v-text-field v-model.number="trainForm.epochs" label="epochs" type="number" density="compact" /></v-col>
            <v-col cols="6"><v-text-field v-model.number="trainForm.batchSize" label="batchSize" type="number" density="compact" /></v-col>
            <v-col cols="6"><v-text-field v-model.number="trainForm.lr" label="lr" type="number" step="0.0001" density="compact" /></v-col>
          </v-row>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="trainDialog = false">取消</v-btn>
          <v-btn color="secondary" :loading="training" :disabled="!trainForm.datasetId" @click="submitTrain">提交训练</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
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
const router = useRouter()
const canWrite = computed(() => auth.hasPerm('model:write'))
const canServe = computed(() => auth.hasPerm('serving:write'))
const canTrain = computed(() => auth.hasPerm('training:write'))
const canAgent = computed(() => auth.hasPerm('agent:read'))

function analyze(item) {
  agentStore.dispatchDiagnose({ taskType: 'model_review', targetType: 'model_version', targetId: item.id })
}

const headers = [
  { title: 'ID', key: 'id', width: 70 },
  { title: '名称', key: 'name' },
  { title: '版本', key: 'version', width: 90 },
  { title: '算法', key: 'algorithm', width: 100 },
  { title: '数据集', key: 'datasetId', width: 100 },
  { title: '状态', key: 'status', width: 110 },
  { title: '关键指标', key: 'metrics' },
  { title: '创建时间', key: 'createdAt', width: 150 },
  { title: '操作', key: 'actions', sortable: false, width: 190 }
]

const items = ref([])
const total = ref(0)
const loading = ref(false)
const pageSize = ref(10)
const page = ref(0)

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/models', { params: { page: page.value, size: pageSize.value } })
    items.value = data.data.content
    total.value = data.data.totalElements
  } finally { loading.value = false }
}
function onOptions(o) { page.value = o.page - 1; pageSize.value = o.itemsPerPage; load() }

function metricsOf(item) {
  if (!item || !item.metrics) return null
  try { return JSON.parse(item.metrics) } catch (e) { return null }
}

function statusText(s) {
  return { TRAINING: '训练中', READY: '就绪', FAILED: '失败' }[s] || s || '未知'
}
function statusColor(s) {
  return { TRAINING: 'info', READY: 'success', FAILED: 'error' }[s] || 'grey'
}

const detailDialog = ref(false)
const detailItem = ref(null)
function openDetail(item) { detailItem.value = item; detailDialog.value = true }

async function download(item) {
  try {
    const { data } = await api.get(`/models/${item.id}/download`)
    window.open(data.data.url, '_blank')
  } catch (e) {
    notifyError(errMsg(e, '获取下载链接失败'))
  }
}

async function remove(item) {
  const ok = await confirmDialog({
    title: '删除模型',
    message: `确定要删除模型「${item.name}」吗？对应的模型文件与训练产物将一并清理。`,
    confirmText: '删除',
    danger: true
  })
  if (!ok) return
  try {
    await api.delete(`/models/${item.id}`)
    load()
  } catch (e) {
    notifyError(errMsg(e, '删除失败'))
  }
}

async function deploy(item) {
  const ok = await confirmDialog({
    title: '部署模型服务',
    message: `确定要部署模型「${item.name} (${item.version})」为推理服务吗？将启动一个 serving 容器。`,
    confirmText: '部署'
  })
  if (!ok) return
  try {
    await api.post('/serving/deploy', { modelVersionId: item.id })
    router.push('/serving')
  } catch (e) {
    notifyError(errMsg(e, '部署失败'))
  }
}

// ---- 右上角「训练模型」：选择 READY 数据集发起训练 ----
const trainDialog = ref(false)
const training = ref(false)
const datasetsLoading = ref(false)
const readyDatasets = ref([])
const trainForm = reactive({ datasetId: null, name: '', version: 'v1', algorithm: 'LSTM', seqLen: 24, hiddenSize: 64, epochs: 50, batchSize: 32, lr: 0.001 })

async function openTrain() {
  trainDialog.value = true
  trainForm.datasetId = null
  datasetsLoading.value = true
  try {
    const { data } = await api.get('/datasets', { params: { page: 0, size: 100 } })
    readyDatasets.value = (data.data.content || [])
      .filter(d => d.status === 'READY')
      .map(d => ({ label: `${d.name} · #${d.id}`, value: d.id }))
  } catch (e) {
    notifyError(errMsg(e, '加载数据集列表失败'))
  } finally { datasetsLoading.value = false }
}

async function submitTrain() {
  if (!trainForm.datasetId) return
  training.value = true
  try {
    await api.post('/training/jobs', {
      datasetId: trainForm.datasetId,
      name: trainForm.name,
      version: trainForm.version,
      algorithm: trainForm.algorithm,
      hyperparameters: {
        seqLen: trainForm.seqLen,
        hiddenSize: trainForm.hiddenSize,
        epochs: trainForm.epochs,
        batchSize: trainForm.batchSize,
        lr: trainForm.lr
      }
    })
    trainDialog.value = false
    const go = await confirmDialog({
      title: '训练任务已提交',
      message: '训练任务已提交，是否前往「训练任务」页查看进度？',
      confirmText: '前往查看',
      cancelText: '留在本页',
      color: 'secondary',
      icon: 'mdi-rocket-launch-outline'
    })
    if (go) router.push('/training/jobs')
  } catch (e) {
    notifyError(errMsg(e, '提交训练失败'))
  } finally { training.value = false }
}

onMounted(load)
</script>
