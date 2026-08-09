<template>
  <div>
    <div class="page-toolbar">
      <v-spacer />
      <v-btn v-if="canWrite" color="primary" prepend-icon="mdi-plus" @click="openCreate">新建角色</v-btn>
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
        <template #item.permissions="{ item }">
          <v-chip v-for="p in item.permissions.slice(0, 5)" :key="p.id" class="ma-1" variant="tonal">{{ p.code }}</v-chip>
          <v-chip v-if="item.permissions.length > 5" class="ma-1" variant="text">+{{ item.permissions.length - 5 }}…</v-chip>
        </template>
        <template #item.actions="{ item }">
          <div class="row-actions">
            <v-btn v-if="canWrite" icon="mdi-pencil" size="small" variant="text" title="编辑" @click="openEdit(item)" />
            <v-btn v-if="canWrite" icon="mdi-delete" size="small" variant="text" color="error" title="删除" @click="remove(item)" />
          </div>
        </template>
      </v-data-table-server>
    </v-card>

    <v-dialog v-model="dialog">
      <v-card>
        <v-card-title>{{ editId ? '编辑角色' : '新建角色' }}</v-card-title>
        <v-card-text>
          <v-text-field v-model="form.name" label="角色名" :disabled="!!editId" :rules="[(v)=>!!v||'必填']" />
          <v-text-field v-model="form.description" label="描述" />
          <v-select
            v-model="form.permissionIds"
            :items="permOptions"
            item-title="code"
            item-value="id"
            label="权限"
            multiple
            chips
          />
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
import { useConfirm } from '../../composables/useConfirm'
import { useNotify, errMsg } from '../../composables/useNotify'

const auth = useAuthStore()
const { confirmDialog } = useConfirm()
const { notifyError } = useNotify()
const canWrite = computed(() => auth.hasPerm('role:write'))

const headers = [
  { title: 'ID', key: 'id', width: 80 },
  { title: '角色名', key: 'name' },
  { title: '描述', key: 'description' },
  { title: '权限', key: 'permissions' },
  { title: '操作', key: 'actions', sortable: false, width: 100 }
]

const items = ref([])
const total = ref(0)
const loading = ref(false)
const pageSize = ref(10)
const page = ref(0)
const dialog = ref(false)
const editId = ref(null)
const saving = ref(false)
const permOptions = ref([])
const form = reactive({ name: '', description: '', permissionIds: [] })

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/roles', { params: { page: page.value, size: pageSize.value } })
    items.value = data.data.content
    total.value = data.data.totalElements
  } finally { loading.value = false }
}
function onOptions(o) { page.value = o.page - 1; load() }

async function loadPerms() {
  const { data } = await api.get('/permissions', { params: { size: 200 } })
  permOptions.value = data.data.content
}

function reset() { editId.value = null; Object.assign(form, { name: '', description: '', permissionIds: [] }) }
function openCreate() { reset(); dialog.value = true }
function openEdit(item) {
  reset(); editId.value = item.id
  Object.assign(form, { name: item.name, description: item.description, permissionIds: (item.permissions || []).map(p => p.id) })
  dialog.value = true
}

async function save() {
  saving.value = true
  try {
    if (editId.value) await api.put(`/roles/${editId.value}`, { description: form.description, permissionIds: form.permissionIds })
    else await api.post('/roles', { ...form })
    dialog.value = false; load()
  } catch (e) { notifyError(errMsg(e, '保存失败')) }
  finally { saving.value = false }
}

async function remove(item) {
  const ok = await confirmDialog({
    title: '删除角色',
    message: `确定要删除角色「${item.name}」吗？删除后不可恢复。`,
    confirmText: '删除',
    danger: true
  })
  if (!ok) return
  try {
    await api.delete(`/roles/${item.id}`); load()
  } catch (e) { notifyError(errMsg(e, '删除失败')) }
}

onMounted(async () => { await loadPerms(); load() })
</script>
