<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h6">数据集管理</h2>
      <v-spacer />
      <v-btn v-if="canWrite" color="primary" prepend-icon="mdi-plus" @click="openCreate">新建数据集</v-btn>
    </div>

    <v-card rounded="lg" elevation="2">
      <v-data-table-server
        :headers="headers"
        :items="items"
        :loading="loading"
        :items-length="total"
        :items-per-page="pageSize"
        @update:options="onOptions"
      >
        <template #item.status="{ item }">
          <v-chip :color="item.status === 'READY' ? 'success' : 'grey'" size="small">{{ item.status }}</v-chip>
        </template>
        <template #item.actions="{ item }">
          <v-btn v-if="canWrite" icon="mdi-pencil" size="small" variant="text" @click="openEdit(item)" />
          <v-btn v-if="canWrite" icon="mdi-delete" size="small" variant="text" color="error" @click="remove(item)" />
        </template>
      </v-data-table-server>
    </v-card>

    <v-dialog v-model="dialog" max-width="560">
      <v-card rounded="lg">
        <v-card-title>{{ editId ? '编辑数据集' : '新建数据集' }}</v-card-title>
        <v-card-text>
          <v-text-field v-model="form.name" label="名称" variant="outlined" :rules="[(v)=>!!v||'必填']" />
          <v-textarea v-model="form.description" label="描述" variant="outlined" rows="2" />
          <v-text-field v-model="form.objectKey" label="对象存储 Key" variant="outlined" placeholder="datasets/1/xxx.csv" />
          <v-row>
            <v-col cols="6"><v-text-field v-model="form.region" label="地区" variant="outlined" /></v-col>
            <v-col cols="6"><v-text-field v-model="form.source" label="数据源" variant="outlined" /></v-col>
          </v-row>
          <v-row>
            <v-col cols="6"><v-text-field v-model="form.fileFormat" label="格式" variant="outlined" placeholder="csv/parquet" /></v-col>
            <v-col cols="6"><v-text-field v-model.number="form.rowCount" label="行数" type="number" variant="outlined" /></v-col>
          </v-row>
          <v-row>
            <v-col cols="6"><v-text-field v-model="form.dateStart" label="起始日期" type="date" variant="outlined" /></v-col>
            <v-col cols="6"><v-text-field v-model="form.dateEnd" label="结束日期" type="date" variant="outlined" /></v-col>
          </v-row>
          <v-select v-model="form.status" :items="['READY','UPLOADING','INVALID']" label="状态" variant="outlined" />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="dialog = false">取消</v-btn>
          <v-btn color="primary" :loading="saving" @click="save">保存</v-btn>
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
const canWrite = computed(() => auth.hasPerm('dataset:write'))

const headers = [
  { title: 'ID', key: 'id', width: 70 },
  { title: '名称', key: 'name' },
  { title: '地区', key: 'region' },
  { title: '格式', key: 'fileFormat' },
  { title: '行数', key: 'rowCount' },
  { title: '状态', key: 'status' },
  { title: '操作', key: 'actions', sortable: false }
]

const items = ref([])
const total = ref(0)
const loading = ref(false)
const pageSize = ref(20)
const page = ref(0)
const dialog = ref(false)
const editId = ref(null)
const saving = ref(false)
const form = reactive({
  name: '', description: '', objectKey: '', region: '', source: '',
  fileFormat: '', rowCount: null, dateStart: null, dateEnd: null, status: 'READY'
})

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/datasets', { params: { page: page.value, size: pageSize.value } })
    items.value = data.data.content
    total.value = data.data.totalElements
  } finally { loading.value = false }
}
function onOptions(o) { page.value = o.page - 1; pageSize.value = o.itemsPerPage; load() }

function reset() {
  editId.value = null
  Object.assign(form, { name: '', description: '', objectKey: '', region: '', source: '', fileFormat: '', rowCount: null, dateStart: null, dateEnd: null, status: 'READY' })
}
function openCreate() { reset(); dialog.value = true }
function openEdit(item) {
  reset(); editId.value = item.id
  Object.assign(form, {
    name: item.name, description: item.description, objectKey: item.objectKey,
    region: item.region, source: item.source, fileFormat: item.fileFormat,
    rowCount: item.rowCount, dateStart: item.dateStart, dateEnd: item.dateEnd, status: item.status
  })
  dialog.value = true
}

async function save() {
  saving.value = true
  try {
    if (editId.value) await api.put(`/datasets/${editId.value}`, { ...form })
    else await api.post('/datasets', { ...form })
    dialog.value = false; load()
  } catch (e) { alert(e.response?.data?.message || '保存失败') }
  finally { saving.value = false }
}

async function remove(item) {
  if (!confirm(`确认删除数据集 ${item.name}？`)) return
  await api.delete(`/datasets/${item.id}`); load()
}

onMounted(load)
</script>
