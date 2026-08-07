<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h6 list-title">权限管理</h2>
      <v-spacer />
      <v-btn v-if="canWrite" color="primary" prepend-icon="mdi-plus" @click="openCreate">新建权限</v-btn>
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
        <template #item.actions="{ item }">
          <v-btn v-if="canWrite" icon="mdi-delete" size="small" variant="text" color="error" @click="remove(item)" />
        </template>
      </v-data-table-server>
    </v-card>

    <v-dialog v-model="dialog" max-width="480">
      <v-card rounded="lg">
        <v-card-title>新建权限</v-card-title>
        <v-card-text>
          <v-text-field v-model="form.code" label="权限编码" variant="outlined" :rules="[(v)=>!!v||'必填']" placeholder="如 dataset:write" />
          <v-text-field v-model="form.description" label="描述" variant="outlined" />
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
const canWrite = computed(() => auth.hasPerm('permission:write'))

const headers = [
  { title: 'ID', key: 'id', width: 80 },
  { title: '编码', key: 'code' },
  { title: '描述', key: 'description' },
  { title: '操作', key: 'actions', sortable: false }
]

const items = ref([])
const total = ref(0)
const loading = ref(false)
const pageSize = ref(100)
const page = ref(0)
const dialog = ref(false)
const saving = ref(false)
const form = reactive({ code: '', description: '' })

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/permissions', { params: { page: page.value, size: pageSize.value } })
    items.value = data.data.content
    total.value = data.data.totalElements
  } finally { loading.value = false }
}
function onOptions(o) { page.value = o.page - 1; load() }
function openCreate() { Object.assign(form, { code: '', description: '' }); dialog.value = true }

async function save() {
  saving.value = true
  try { await api.post('/permissions', { ...form }); dialog.value = false; load() }
  catch (e) { alert(e.response?.data?.message || '保存失败') }
  finally { saving.value = false }
}

async function remove(item) {
  if (!confirm(`确认删除权限 ${item.code}？`)) return
  await api.delete(`/permissions/${item.id}`); load()
}

onMounted(load)
</script>
