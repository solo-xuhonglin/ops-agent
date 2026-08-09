<template>
  <div>
    <div class="page-toolbar">
      <v-spacer />
      <v-select
        v-model="filterAction"
        :items="actionOptions"
        label="写操作"
        density="compact"
        clearable
        hide-details
        style="max-width: 200px"
        @update:model-value="load"
      />
      <v-text-field
        v-model="filterActor"
        label="执行人"
        density="compact"
        clearable
        hide-details
        prepend-inner-icon="mdi-account"
        style="max-width: 160px"
        @update:model-value="load"
      />
      <v-select
        v-model="filterAgent"
        :items="agentOptions"
        label="是否Agent"
        density="compact"
        clearable
        hide-details
        style="max-width: 140px"
        @update:model-value="load"
      />
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
        <template #item.createdAt="{ item }">
          <span class="text-body-small">{{ fmtDateTime(item.createdAt) }}</span>
        </template>
        <template #item.action="{ item }">
          <v-chip variant="tonal" color="primary" size="small">{{ actionLabel(item.action) }}</v-chip>
          <div v-if="!ACTION_LABELS[item.action]" class="text-caption text-medium-emphasis">{{ item.action }}</div>
        </template>
        <template #item.actor="{ item }">
          <div class="d-flex align-center">
            <v-icon :icon="item.actorType === 'AGENT' ? 'mdi-robot' : 'mdi-account'" size="18" class="mr-1" />
            <span>{{ item.actorName }}</span>
          </div>
        </template>
        <template #item.actorType="{ item }">
          <v-chip v-if="item.actorType === 'AGENT'" color="secondary" size="small">Agent</v-chip>
          <v-chip v-else color="grey" size="small" variant="tonal">人工</v-chip>
        </template>
        <template #item.approverName="{ item }">
          <span v-if="item.approverName">{{ item.approverName }}</span>
          <span v-else class="text-medium-emphasis">—</span>
        </template>
        <template #item.target="{ item }">
          <span v-if="item.targetType || item.targetId">
            {{ item.targetType }}<template v-if="item.targetId">#{{ item.targetId }}</template>
          </span>
          <span v-else class="text-medium-emphasis">—</span>
        </template>
        <template #item.params="{ item }">
          <v-btn v-if="item.params" size="x-small" variant="text" color="primary" @click="openParams(item)">查看</v-btn>
          <span v-else class="text-medium-emphasis">—</span>
        </template>
      </v-data-table-server>
    </v-card>

    <v-dialog v-model="paramsDialog" max-width="640">
      <v-card>
        <v-card-title class="d-flex align-center">
          参数详情
          <v-spacer />
          <v-chip variant="tonal" size="small">{{ paramsItem?.action }}</v-chip>
        </v-card-title>
        <v-card-text>
          <pre class="params-pre">{{ prettyParams }}</pre>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="paramsDialog = false">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import api from '../../plugins/axios'
import { useAuthStore } from '../../stores/auth'
import { fmtDateTime } from '../../utils/format'

const auth = useAuthStore()
const canRead = computed(() => auth.hasPerm('audit:read'))

// action 码 -> 中文标签
const ACTION_LABELS = {
  'dataset:create': '创建数据集',
  'dataset:update': '更新数据集',
  'dataset:delete': '删除数据集',
  'dataset:collect': '采集天气',
  'model:delete': '删除模型',
  'training:create': '发起训练',
  'training:delete': '删除训练',
  'serving:deploy': '部署服务',
  'serving:undeploy': '下线服务',
  'serving:delete': '删除服务',
  'user:create': '创建用户',
  'user:update': '更新用户',
  'user:delete': '删除用户',
  'user:reset_password': '重置密码',
  'role:create': '创建角色',
  'role:update': '更新角色',
  'role:delete': '删除角色',
  'permission:create': '创建权限',
  'permission:update': '更新权限',
  'permission:delete': '删除权限',
  'agent:dispatch': '派发任务',
  'agent:task_cancel': '取消任务',
  'agent:suggestion_approve': '批准建议',
  'agent:suggestion_reject': '拒绝建议',
  'agent:conversation_create': '创建会话',
  'agent:conversation_delete': '删除会话',
  'agent:message': '发送消息',
  'agent_tool:delete': '删除工具'
}
// fallback: 将 method:resource 格式化为可读中文
const METHOD_LABELS = { post: '创建', put: '更新', delete: '删除', patch: '修改' }
const RESOURCE_LABELS = {
  agent: 'Agent 任务', dataset: '数据集', model: '模型', training: '训练',
  serving: '服务', user: '用户', role: '角色', permission: '权限',
  conversation: '会话', tool: '工具'
}

function actionLabel(a) {
  if (ACTION_LABELS[a]) return ACTION_LABELS[a]
  //兜底：post:agent → "创建 Agent 任务"
  const [method, resource] = a.split(':')
  const mLabel = METHOD_LABELS[method] || method
  const rLabel = RESOURCE_LABELS[resource] || resource
  return `${mLabel}${rLabel}`
}
const actionOptions = Object.entries(ACTION_LABELS).map(([value, title]) => ({ title, value }))
const agentOptions = [
  { title: '全部', value: null },
  { title: '人工', value: 'USER' },
  { title: 'Agent', value: 'AGENT' }
]

const headers = [
  { title: '时间', key: 'createdAt', width: 150 },
  { title: '写操作', key: 'action', width: 170 },
  { title: '执行人', key: 'actor', width: 140 },
  { title: '是否Agent', key: 'actorType', width: 110 },
  { title: '审批人', key: 'approverName', width: 120 },
  { title: '目标', key: 'target', width: 150 },
  { title: '参数', key: 'params', sortable: false, width: 100 }
]

const items = ref([])
const total = ref(0)
const loading = ref(false)
const pageSize = ref(10)
const page = ref(0)
const filterAction = ref(null)
const filterActor = ref(null)
const filterAgent = ref(null)

const paramsDialog = ref(false)
const paramsItem = ref(null)
const prettyParams = computed(() => {
  const p = paramsItem.value?.params
  if (!p) return ''
  try {
    return JSON.stringify(JSON.parse(p), null, 2)
  } catch {
    return p
  }
})

function openParams(item) {
  paramsItem.value = item
  paramsDialog.value = true
}

async function load() {
  if (!canRead.value) return
  loading.value = true
  try {
    const { data } = await api.get('/audit/logs', {
      params: {
        page: page.value,
        size: pageSize.value,
        action: filterAction.value || undefined,
        actorName: filterActor.value || undefined,
        actorType: filterAgent.value || undefined
      }
    })
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

// 进入即加载（路由已用 perm 守护）
load()
</script>

<style scoped>
.params-pre {
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 8px;
  padding: 12px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 50vh;
  overflow: auto;
}
</style>
