<template>
  <div>
    <div class="page-toolbar">
      <v-spacer />
      <v-btn v-if="canWrite" color="primary" prepend-icon="mdi-plus" @click="openCreate">新建权限</v-btn>
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
        <template #item.actions="{ item }">
          <div class="row-actions">
            <v-btn v-if="canWrite" icon="mdi-delete" size="small" variant="text" color="error" title="删除" @click="remove(item)" />
          </div>
        </template>
      </v-data-table-server>
    </v-card>

    <v-dialog v-model="dialog">
      <v-card>
        <v-card-title>新建权限</v-card-title>
        <v-card-text>
          <v-text-field v-model="form.code" label="权限编码" :rules="[(v)=>!!v||'必填']" placeholder="如 dataset:write" />
          <v-text-field v-model="form.description" label="描述" />
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
const canWrite = computed(() => auth.hasPerm('permission:write'))

const headers = [
  { title: 'ID', key: 'id', width: 80 },
  { title: '编码', key: 'code' },
  { title: '描述', key: 'description' },
  { title: '操作', key: 'actions', sortable: false, width: 90 }
]

const items = ref([])
const total = ref(0)
const loading = ref(false)
const pageSize = ref(10)
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
  catch (e) { notifyError(errMsg(e, '保存失败')) }
  finally { saving.value = false }
}

async function remove(item) {
  const ok = await confirmDialog({
    title: '删除权限',
    message: `确定要删除权限「${item.code}」吗？删除后不可恢复。`,
    confirmText: '删除',
    danger: true
  })
  if (!ok) return
  try {
    await api.delete(`/permissions/${item.id}`); load()
  } catch (e) { notifyError(errMsg(e, '删除失败')) }
}

onMounted(load)
</script>
