<template>
  <div>
    <div class="page-toolbar">
      <v-text-field
        v-model="search"
        label="搜索用户名"
        prepend-inner-icon="mdi-magnify"
        density="compact"
        hide-details
        clearable
        class="field-fixed"
        @update:model-value="load"
      />
      <v-spacer />
      <v-btn v-if="canWrite" color="primary" prepend-icon="mdi-plus" @click="openCreate">新建用户</v-btn>
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
          <v-chip :color="item.status === 'ACTIVE' ? 'success' : 'grey'">{{ item.status }}</v-chip>
        </template>
        <template #item.roles="{ item }">
          <v-chip v-for="r in item.roles" :key="r" class="ma-1" variant="tonal">{{ r }}</v-chip>
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
        <v-card-title>{{ editId ? '编辑用户' : '新建用户' }}</v-card-title>
        <v-card-text>
          <v-form ref="form">
            <v-text-field v-model="form.username" label="用户名" :disabled="!!editId" :rules="[(v)=>!!v||'必填']" />
            <v-text-field v-if="!editId" v-model="form.password" label="密码" type="password" :rules="[(v)=>!!v||'必填']" />
            <v-text-field v-model="form.displayName" label="显示名" />
            <v-text-field v-model="form.email" label="邮箱" />
            <v-select
              v-model="form.roleIds"
              :items="roleOptions"
              item-title="name"
              item-value="id"
              label="角色"
              multiple
              chips
            />
            <v-select v-model="form.status" :items="['ACTIVE','DISABLED']" label="状态" />
          </v-form>
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
const canWrite = computed(() => auth.hasPerm('user:write'))

const headers = [
  { title: 'ID', key: 'id', width: 80 },
  { title: '用户名', key: 'username' },
  { title: '显示名', key: 'displayName' },
  { title: '邮箱', key: 'email' },
  { title: '状态', key: 'status', width: 110 },
  { title: '角色', key: 'roles' },
  { title: '操作', key: 'actions', sortable: false, width: 100 }
]

const items = ref([])
const total = ref(0)
const loading = ref(false)
const search = ref('')
const pageSize = ref(10)
const page = ref(0)

const dialog = ref(false)
const editId = ref(null)
const saving = ref(false)
const roleOptions = ref([])
const form = reactive({ username: '', password: '', displayName: '', email: '', roleIds: [], status: 'ACTIVE' })

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/users', { params: { page: page.value, size: pageSize.value, keyword: search.value } })
    items.value = data.data.content
    total.value = data.data.totalElements
  } finally {
    loading.value = false
  }
}

function onOptions(o) {
  page.value = o.page - 1
  pageSize.value = o.itemsPerPage
  load()
}

async function loadRoles() {
  const { data } = await api.get('/roles', { params: { size: 100 } })
  roleOptions.value = data.data.content
}

function reset() {
  editId.value = null
  Object.assign(form, { username: '', password: '', displayName: '', email: '', roleIds: [], status: 'ACTIVE' })
}

function openCreate() { reset(); dialog.value = true }
function openEdit(item) {
  reset()
  editId.value = item.id
  Object.assign(form, {
    username: item.username, displayName: item.displayName, email: item.email,
    roleIds: (item.roles || []).map(r => (roleOptions.value.find(x => x.name === r)?.id)).filter(Boolean),
    status: item.status
  })
  dialog.value = true
}

async function save() {
  saving.value = true
  try {
    if (editId.value) {
      await api.put(`/users/${editId.value}`, { displayName: form.displayName, email: form.email, status: form.status, roleIds: form.roleIds })
    } else {
      await api.post('/users', { ...form })
    }
    dialog.value = false
    load()
  } catch (e) {
    notifyError(errMsg(e, '保存失败'))
  } finally {
    saving.value = false
  }
}

async function remove(item) {
  const ok = await confirmDialog({
    title: '删除用户',
    message: `确定要删除用户「${item.username}」吗？删除后不可恢复。`,
    confirmText: '删除',
    danger: true
  })
  if (!ok) return
  try {
    await api.delete(`/users/${item.id}`)
    load()
  } catch (e) {
    notifyError(errMsg(e, '删除失败'))
  }
}

onMounted(async () => { await loadRoles(); load() })
</script>
