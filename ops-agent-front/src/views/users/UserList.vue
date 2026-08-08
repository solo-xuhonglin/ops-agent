<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-title-large list-title">用户管理</h2>
      <v-spacer />
      <v-text-field
        v-model="search"
        label="搜索用户名"
        prepend-inner-icon="mdi-magnify"
        variant="solo-filled"
        density="compact"
        hide-details
        style="max-width: 260px"
        @update:model-value="load"
      />
      <v-btn v-if="canWrite" color="primary" class="ml-3" prepend-icon="mdi-plus" @click="openCreate">新建用户</v-btn>
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
          <v-chip :color="item.status === 'ACTIVE' ? 'success' : 'grey'" size="small">{{ item.status }}</v-chip>
        </template>
        <template #item.roles="{ item }">
          <v-chip v-for="r in item.roles" :key="r" class="ma-1" size="small" variant="tonal">{{ r }}</v-chip>
        </template>
        <template #item.actions="{ item }">
          <v-btn v-if="canWrite" icon="mdi-pencil" size="small" variant="text" @click="openEdit(item)" />
          <v-btn v-if="canWrite" icon="mdi-delete" size="small" variant="text" color="error" @click="remove(item)" />
        </template>
      </v-data-table-server>
    </v-card>

    <v-dialog v-model="dialog" max-width="520">
      <v-card rounded="lg">
        <v-card-title>{{ editId ? '编辑用户' : '新建用户' }}</v-card-title>
        <v-card-text>
          <v-form ref="form">
            <v-text-field v-model="form.username" label="用户名" variant="outlined" :disabled="!!editId" :rules="[(v)=>!!v||'必填']" />
            <v-text-field v-if="!editId" v-model="form.password" label="密码" type="password" variant="outlined" :rules="[(v)=>!!v||'必填']" />
            <v-text-field v-model="form.displayName" label="显示名" variant="outlined" />
            <v-text-field v-model="form.email" label="邮箱" variant="outlined" />
            <v-select
              v-model="form.roleIds"
              :items="roleOptions"
              item-title="name"
              item-value="id"
              label="角色"
              multiple
              chips
              variant="outlined"
            />
            <v-select v-model="form.status" :items="['ACTIVE','DISABLED']" label="状态" variant="outlined" />
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

const auth = useAuthStore()
const { confirmDialog } = useConfirm()
const canWrite = computed(() => auth.hasPerm('user:write'))

const headers = [
  { title: 'ID', key: 'id', width: 80 },
  { title: '用户名', key: 'username' },
  { title: '显示名', key: 'displayName' },
  { title: '邮箱', key: 'email' },
  { title: '状态', key: 'status' },
  { title: '角色', key: 'roles' },
  { title: '操作', key: 'actions', sortable: false }
]

const items = ref([])
const total = ref(0)
const loading = ref(false)
const search = ref('')
const pageSize = ref(20)
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
    alert(e.response?.data?.message || '保存失败')
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
  await api.delete(`/users/${item.id}`)
  load()
}

onMounted(async () => { await loadRoles(); load() })
</script>
