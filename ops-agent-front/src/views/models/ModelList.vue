<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h6 list-title">模型管理</h2>
      <v-spacer />
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
        <template #item.metrics="{ item }">
          <span v-if="metricsOf(item)">
            MAE {{ metricsOf(item).mae }} · RMSE {{ metricsOf(item).rmse }}
          </span>
          <span v-else class="text-medium-emphasis">—</span>
        </template>
        <template #item.createdAt="{ item }">
          {{ fmt(item.createdAt) }}
        </template>
        <template #item.actions="{ item }">
          <v-btn icon="mdi-information" size="small" variant="text" color="primary" title="详情" @click="openDetail(item)" />
          <v-btn icon="mdi-download" size="small" variant="text" color="secondary" title="下载模型" @click="download(item)" />
          <v-btn v-if="canWrite" icon="mdi-delete" size="small" variant="text" color="error" title="删除" @click="remove(item)" />
        </template>
      </v-data-table-server>
    </v-card>

    <v-dialog v-model="detailDialog" max-width="560">
      <v-card rounded="lg">
        <v-card-title>模型详情 · {{ detailItem?.name }}</v-card-title>
        <v-card-text>
          <v-row dense>
            <v-col cols="6"><div class="text-caption text-medium-emphasis">版本</div><div>{{ detailItem?.version }}</div></v-col>
            <v-col cols="6"><div class="text-caption text-medium-emphasis">算法</div><div>{{ detailItem?.algorithm }}</div></v-col>
            <v-col cols="6"><div class="text-caption text-medium-emphasis">关联数据集</div><div>#{{ detailItem?.datasetId }}</div></v-col>
            <v-col cols="6"><div class="text-caption text-medium-emphasis">状态</div><div>{{ statusText(detailItem?.status) }}</div></v-col>
          </v-row>
          <div class="text-subtitle-2 mt-4 mb-2">训练指标</div>
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
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { useAuthStore } from '../../stores/auth'
import api from '../../plugins/axios'

const auth = useAuthStore()
const canWrite = computed(() => auth.hasPerm('model:write'))

const headers = [
  { title: 'ID', key: 'id', width: 70 },
  { title: '名称', key: 'name' },
  { title: '版本', key: 'version', width: 90 },
  { title: '算法', key: 'algorithm', width: 100 },
  { title: '数据集', key: 'datasetId', width: 100 },
  { title: '状态', key: 'status', width: 110 },
  { title: '关键指标', key: 'metrics' },
  { title: '创建时间', key: 'createdAt', width: 170 },
  { title: '操作', key: 'actions', sortable: false, width: 140 }
]

const items = ref([])
const total = ref(0)
const loading = ref(false)
const pageSize = ref(20)
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
function fmt(d) {
  if (!d) return '—'
  return new Date(d).toLocaleString()
}

const detailDialog = ref(false)
const detailItem = ref(null)
function openDetail(item) { detailItem.value = item; detailDialog.value = true }

async function download(item) {
  try {
    const { data } = await api.get(`/models/${item.id}/download`)
    window.open(data.data.url, '_blank')
  } catch (e) {
    alert(e.response?.data?.message || '获取下载链接失败')
  }
}

async function remove(item) {
  if (!confirm(`确认删除模型 ${item.name}？`)) return
  try {
    await api.delete(`/models/${item.id}`)
    load()
  } catch (e) {
    alert(e.response?.data?.message || '删除失败')
  }
}

onMounted(load)
</script>
