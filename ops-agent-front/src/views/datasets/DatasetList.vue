<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h6 list-title">数据集管理</h2>
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
        <template #item.actions="{ item }">
          <v-btn icon="mdi-chart-line" size="small" variant="text" color="primary" title="查看图表" @click="openChart(item)" />
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
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed, nextTick, watch } from 'vue'
import * as echarts from 'echarts'
import { useAuthStore } from '../../stores/auth'
import api from '../../plugins/axios'

const CITY_OPTIONS = [
  '北京', '上海', '广州', '深圳', '成都', '杭州', '武汉', '西安', '南京', '重庆',
  '天津', '苏州', '长沙', '郑州', '青岛', '沈阳', '大连', '厦门', '昆明', '哈尔滨'
]

const auth = useAuthStore()
const canWrite = computed(() => auth.hasPerm('dataset:write'))

const headers = [
  { title: 'ID', key: 'id', width: 70 },
  { title: '名称', key: 'name' },
  { title: '地区', key: 'regions' },
  { title: '日期范围', key: 'dateRange' },
  { title: '状态', key: 'status', width: 110 },
  { title: '操作', key: 'actions', sortable: false, width: 140 }
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
  if (!confirm(`确认删除数据集 ${item.name}？`)) return
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
const metric = ref('tAvg')
const metricOptions = [
  { label: '平均气温 (℃)', value: 'tAvg' },
  { label: '最高气温 (℃)', value: 'tMax' },
  { label: '最低气温 (℃)', value: 'tMin' },
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
  const dates = payload.dates || []
  const series = payload.series || {}
  const seriesData = regions.map((r, idx) => {
    const arr = (series[r] || []).map(p => p[metric.value])
    const color = echarts.color.lerp(idx / Math.max(regions.length - 1, 1), ['#5B6EF0', '#F07D5B'])
    return {
      name: r,
      type: 'line',
      smooth: true,
      showSymbol: false,
      data: arr,
      lineStyle: { width: 2 },
      itemStyle: { color }
    }
  })
  chartInstance.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: regions, top: 0 },
    grid: { left: 50, right: 20, top: 40, bottom: 60 },
    xAxis: { type: 'category', data: dates, boundaryGap: false, axisLabel: { rotate: dates.length > 15 ? 45 : 0 } },
    yAxis: { type: 'value', name: metricLabel() },
    dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 10 }],
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
