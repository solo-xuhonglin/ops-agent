<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-title-large list-title">数据集管理</h2>
      <v-spacer />
      <v-btn v-if="canWrite" color="primary" prepend-icon="mdi-plus" @click="openCreate">新建数据集</v-btn>
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
        <template #item.regions="{ item }">
          <v-chip v-for="r in (item.regions || [])" :key="r" class="ma-1" size="small" variant="tonal">{{ r }}</v-chip>
          <span v-if="!item.regions || !item.regions.length" class="text-medium-emphasis">—</span>
        </template>
        <template #item.dateRange="{ item }">
          <span v-if="item.dateStart && item.dateEnd">{{ item.dateStart }} ~ {{ item.dateEnd }}</span>
          <span v-else class="text-medium-emphasis">—</span>
        </template>
        <template #item.status="{ item }">
          <v-chip :color="statusColor(item.status)" size="small">{{ statusText(item.status) }}</v-chip>
        </template>
        <template #item.rowCount="{ item }">
          <span v-if="item.rowCount != null">{{ item.rowCount.toLocaleString() }}</span>
          <span v-else class="text-medium-emphasis">—</span>
        </template>
        <template #item.actions="{ item }">
          <v-btn icon="mdi-chart-line" size="small" variant="text" color="primary" title="查看图表" @click="openChart(item)" />
          <v-btn v-if="canTrain" icon="mdi-rocket-launch" size="small" variant="text" color="secondary" title="训练" @click="openTrain(item)" />
          <v-btn v-if="canWrite" icon="mdi-pencil" size="small" variant="text" @click="openEdit(item)" />
          <v-btn v-if="canWrite" icon="mdi-delete" size="small" variant="text" color="error" @click="remove(item)" />
        </template>
      </v-data-table-server>
    </v-card>

    <v-dialog v-model="dialog" max-width="620">
      <v-card rounded="lg">
        <v-card-title>{{ editId ? '编辑数据集' : '新建数据集' }}</v-card-title>
        <v-card-text>
          <v-text-field v-model="form.name" label="名称" variant="outlined" :rules="[(v)=>!!v||'必填']" />
          <v-textarea v-model="form.description" label="描述" variant="outlined" rows="2" />
          <v-select
            v-model="form.regions"
            :items="cityOptions"
            label="地区（可多选）"
            multiple
            chips
            closable-chips
            variant="outlined"
          />
          <v-row>
            <v-col cols="6">
              <v-date-input v-model="form.dateStart" label="起始日期" variant="outlined" prepend-icon="" :max="form.dateEnd || undefined" />
            </v-col>
            <v-col cols="6">
              <v-date-input v-model="form.dateEnd" label="结束日期" variant="outlined" prepend-icon="" :min="form.dateStart || undefined" />
            </v-col>
          </v-row>
          <v-alert v-if="form.regions.length && form.dateStart && form.dateEnd" type="info" variant="tonal" density="compact" class="mt-2">
            保存后将自动从免费天气接口采集 {{ form.regions.join('、') }} 在 {{ fmt(form.dateStart) }} ~ {{ fmt(form.dateEnd) }} 的每日天气。
          </v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="dialog = false">取消</v-btn>
          <v-btn color="primary" :loading="saving" @click="save">保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="chartDialog" max-width="920">
      <v-card rounded="lg">
        <v-card-title class="d-flex align-center">
          天气图表
          <v-spacer />
          <v-chip size="small" variant="tonal" class="mr-2">{{ chartItem?.name }}</v-chip>
        </v-card-title>
        <v-card-text>
          <div class="d-flex align-center flex-wrap mb-3">
            <v-select
              v-model="metric"
              :items="metricOptions"
              item-title="label"
              item-value="value"
              label="指标"
              variant="outlined"
              density="compact"
              hide-details
              style="max-width: 220px"
            />
            <v-progress-linear v-if="chartLoading" indeterminate color="primary" class="ml-4" style="max-width: 200px" />
          </div>
          <div ref="chartEl" style="width: 100%; height: 420px"></div>
          <div v-if="!chartLoading && !hasData" class="text-medium-emphasis text-center py-8">暂无天气数据</div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="chartDialog = false">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="trainDialog" max-width="620">
      <v-card rounded="lg">
        <v-card-title>发起训练</v-card-title>
        <v-card-text>
          <v-alert v-if="trainItem" type="info" variant="tonal" density="compact" class="mb-3">
            数据集：{{ trainItem.name }}（数据条数 {{ trainItem.rowCount != null ? trainItem.rowCount.toLocaleString() : '—' }}）
          </v-alert>
          <v-text-field v-model="trainForm.name" label="模型名称" variant="outlined" :rules="[(v)=>!!v||'必填']" />
          <v-row>
            <v-col cols="6">
              <v-text-field v-model="trainForm.version" label="版本号" variant="outlined" />
            </v-col>
            <v-col cols="6">
              <v-select v-model="trainForm.algorithm" :items="['LSTM']" label="算法" variant="outlined" />
            </v-col>
          </v-row>
          <div class="text-body-large mb-2">超参数</div>
          <v-row>
            <v-col cols="4"><v-text-field v-model.number="trainForm.seqLen" label="seqLen" type="number" variant="outlined" density="compact" /></v-col>
            <v-col cols="4"><v-text-field v-model.number="trainForm.hiddenSize" label="hiddenSize" type="number" variant="outlined" density="compact" /></v-col>
            <v-col cols="4"><v-text-field v-model.number="trainForm.epochs" label="epochs" type="number" variant="outlined" density="compact" /></v-col>
            <v-col cols="6"><v-text-field v-model.number="trainForm.batchSize" label="batchSize" type="number" variant="outlined" density="compact" /></v-col>
            <v-col cols="6"><v-text-field v-model.number="trainForm.lr" label="lr" type="number" step="0.0001" variant="outlined" density="compact" /></v-col>
          </v-row>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="trainDialog = false">取消</v-btn>
          <v-btn color="secondary" :loading="training" @click="submitTrain">提交训练</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { useAuthStore } from '../../stores/auth'
import api from '../../plugins/axios'
import { useConfirm } from '../../composables/useConfirm'

const CITY_OPTIONS = [
  '北京', '上海', '广州', '深圳', '成都', '杭州', '武汉', '西安', '南京', '重庆',
  '天津', '苏州', '长沙', '郑州', '青岛', '沈阳', '大连', '厦门', '昆明', '哈尔滨'
]

const auth = useAuthStore()
const { confirmDialog } = useConfirm()
const router = useRouter()
const canWrite = computed(() => auth.hasPerm('dataset:write'))
const canTrain = computed(() => auth.hasPerm('training:write'))

const headers = [
  { title: 'ID', key: 'id', width: 70 },
  { title: '名称', key: 'name' },
  { title: '地区', key: 'regions' },
  { title: '日期范围', key: 'dateRange' },
  { title: '数据条数', key: 'rowCount', width: 110 },
  { title: '状态', key: 'status', width: 110 },
  { title: '操作', key: 'actions', sortable: false, width: 160 }
]

const cityOptions = CITY_OPTIONS
const items = ref([])
const total = ref(0)
const loading = ref(false)
const pageSize = ref(20)
const page = ref(0)
const dialog = ref(false)
const editId = ref(null)
const saving = ref(false)
const form = reactive({ name: '', description: '', regions: [], dateStart: null, dateEnd: null, status: 'READY' })

// ---------- 训练 ----------
const trainDialog = ref(false)
const training = ref(false)
const trainItem = ref(null)
const trainForm = reactive({ name: '', version: 'v1', algorithm: 'LSTM', seqLen: 24, hiddenSize: 64, epochs: 50, batchSize: 32, lr: 0.001 })

function openTrain(item) {
  trainItem.value = item
  trainForm.name = '模型-' + (item.name || item.id)
  trainDialog.value = true
}

async function submitTrain() {
  if (!trainItem.value) return
  training.value = true
  try {
    await api.post('/training/jobs', {
      datasetId: trainItem.value.id,
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
    alert(e.response?.data?.message || '提交训练失败')
  } finally {
    training.value = false
  }
}

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/datasets', { params: { page: page.value, size: pageSize.value } })
    items.value = data.data.content
    total.value = data.data.totalElements
  } finally { loading.value = false }
}
function onOptions(o) { page.value = o.page - 1; pageSize.value = o.itemsPerPage; load() }

function fmt(d) {
  if (!d) return ''
  const dt = d instanceof Date ? d : new Date(d)
  const y = dt.getFullYear()
  const m = String(dt.getMonth() + 1).padStart(2, '0')
  const day = String(dt.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function reset() {
  editId.value = null
  Object.assign(form, { name: '', description: '', regions: [], dateStart: null, dateEnd: null, status: 'READY' })
}
function openCreate() { reset(); dialog.value = true }
function openEdit(item) {
  reset(); editId.value = item.id
  Object.assign(form, {
    name: item.name, description: item.description, regions: item.regions || [],
    dateStart: item.dateStart ? new Date(item.dateStart) : null,
    dateEnd: item.dateEnd ? new Date(item.dateEnd) : null, status: item.status
  })
  dialog.value = true
}

async function save() {
  saving.value = true
  try {
    const payload = {
      name: form.name,
      description: form.description,
      regions: form.regions,
      dateStart: form.dateStart ? fmt(form.dateStart) : null,
      dateEnd: form.dateEnd ? fmt(form.dateEnd) : null
    }
    if (editId.value) {
      await api.put(`/datasets/${editId.value}`, { ...payload, status: form.status })
    } else {
      await api.post('/datasets', payload)
    }
    dialog.value = false
    load()
  } catch (e) {
    alert(e.response?.data?.message || '保存失败')
  } finally { saving.value = false }
}

async function remove(item) {
  const ok = await confirmDialog({
    title: '删除数据集',
    message: `确定要删除数据集「${item.name}」吗？关联的天气数据与文件将一并清理。`,
    confirmText: '删除',
    danger: true
  })
  if (!ok) return
  await api.delete(`/datasets/${item.id}`); load()
}

function statusText(s) {
  return { READY: '就绪', COLLECTING: '采集中', UPLOADING: '上传中', INVALID: '无效' }[s] || s
}
function statusColor(s) {
  return { READY: 'success', COLLECTING: 'info', UPLOADING: 'warning', INVALID: 'error' }[s] || 'grey'
}

// ---------- 图表 ----------
const chartDialog = ref(false)
const chartItem = ref(null)
const chartEl = ref(null)
const chartLoading = ref(false)
const hasData = ref(false)
const metric = ref('temperature')
const metricOptions = [
  { label: '气温 (℃)', value: 'temperature' },
  { label: '降水量 (mm)', value: 'precip' }
]
let chartInstance = null

async function openChart(item) {
  chartItem.value = item
  chartDialog.value = true
  chartLoading.value = true
  hasData.value = false
  try {
    const { data } = await api.get(`/datasets/${item.id}/weather`)
    const payload = data.data
    hasData.value = !!(payload.regions && payload.regions.length)
    await nextTick()
    renderChart(payload)
  } catch (e) {
    console.error(e)
  } finally {
    chartLoading.value = false
  }
}

function renderChart(payload) {
  if (!chartEl.value) return
  if (!chartInstance) chartInstance = echarts.init(chartEl.value)
  const regions = payload.regions || []
  const times = (payload.times || []).map(t => t.replace('T', ' '))
  const series = payload.series || {}
  const seriesData = regions.map((r, idx) => {
    const arr = (series[r] || []).map(p => p[metric.value])
    const color = echarts.color.lerp(idx / Math.max(regions.length - 1, 1), ['#5B6EF0', '#F07D5B'])
    return {
      name: r,
      type: 'line',
      smooth: true,
      showSymbol: false,
      sampling: 'lttb',
      data: arr,
      lineStyle: { width: 1 },
      itemStyle: { color }
    }
  })
  const startPct = Math.max(0, 100 - Math.min(100, (times.length > 0 ? 240 : 0) / times.length * 100))
  chartInstance.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { data: regions, top: 0 },
    grid: { left: 55, right: 20, top: 40, bottom: 70 },
    xAxis: {
      type: 'category',
      data: times,
      boundaryGap: false,
      axisLabel: { rotate: 45, hideOverlap: true }
    },
    yAxis: { type: 'value', name: metricLabel(), scale: true },
    dataZoom: [
      { type: 'inside' },
      { type: 'slider', bottom: 10, start: startPct, end: 100 }
    ],
    series: seriesData
  }, true)
}

function metricLabel() {
  return metricOptions.find(m => m.value === metric.value)?.label || ''
}
watch(metric, () => { if (chartDialog.value && chartItem.value) openChart(chartItem.value) })
watch(chartDialog, (v) => { if (!v && chartInstance) { chartInstance.dispose(); chartInstance = null } })

onMounted(load)
</script>
