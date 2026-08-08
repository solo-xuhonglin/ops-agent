<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-title-large list-title">Agent 工具</h2>
      <v-spacer />
      <span class="text-body-small text-medium-emphasis">
        共 {{ items.length }} 个工具 · 能力=数据，注册时按启用状态动态下发，改动即时生效
      </span>
    </div>

    <v-card rounded="lg" class="data-card" elevation="0">
      <v-data-table
        :headers="headers"
        :items="items"
        :loading="loading"
        density="compact"
      >
        <template #item.httpMethod="{ item }">
          <v-chip size="small" :color="methodColor(item.httpMethod)" variant="tonal">
            {{ item.httpMethod }}
          </v-chip>
        </template>

        <template #item.isWrite="{ item }">
          <v-chip size="small" :color="item.isWrite ? 'warning' : 'default'" variant="tonal">
            {{ item.isWrite ? '写' : '只读' }}
          </v-chip>
        </template>

        <template #item.enabled="{ item }">
          <v-switch
            :model-value="item.enabled"
            color="primary"
            hide-details
            density="compact"
            :disabled="!canWrite"
            @update:model-value="toggle(item, $event)"
          />
        </template>
      </v-data-table>
    </v-card>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { listTools, setToolEnabled } from '../../api/agent'

const auth = useAuthStore()
const canWrite = computed(() => auth.hasPerm('agent:write'))

const headers = [
  { title: 'ID', key: 'id', width: 70 },
  { title: '工具名', key: 'name', width: 200 },
  { title: '描述', key: 'description', minWidth: 240 },
  { title: '方法', key: 'httpMethod', width: 90 },
  { title: '路径', key: 'pathTemplate', minWidth: 220 },
  { title: '权限', key: 'authPermission', width: 130 },
  { title: '类型', key: 'isWrite', width: 80 },
  { title: '启用', key: 'enabled', width: 80, sortable: false }
]

const items = ref([])
const loading = ref(false)

const methodColor = (m) => ({ GET: 'success', POST: 'primary', PUT: 'warning', DELETE: 'error' }[m] || 'default')

async function load() {
  loading.value = true
  try {
    const res = await listTools()
    items.value = res.data || []
  } finally {
    loading.value = false
  }
}

async function toggle(item, enabled) {
  const prev = item.enabled
  item.enabled = enabled // 乐观更新
  try {
    await setToolEnabled(item.id, enabled)
  } catch (e) {
    item.enabled = prev // 失败回滚
    throw e
  }
}

onMounted(load)
</script>
