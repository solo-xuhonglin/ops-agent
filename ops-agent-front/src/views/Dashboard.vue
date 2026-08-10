<template>
  <div>
    <div class="mb-6">
      <h2 class="text-headline-small font-weight-bold mb-1">{{ greeting }}，{{ auth.user?.displayName || auth.user?.username }}</h2>
      <p class="text-medium-emphasis">算法资产平台 · 将 LSTM 天气模型工具化、Agent 化、平台化</p>
    </div>

    <v-row>
      <v-col cols="12" sm="6" md="3" v-for="s in stats" :key="s.title">
        <v-card class="hover-lift">
          <v-card-text>
            <div class="d-flex align-center justify-space-between">
              <div>
                <div class="text-label-medium text-medium-emphasis">{{ s.title }}</div>
                <div class="text-headline-large font-weight-bold mt-1">{{ s.value }}</div>
              </div>
              <v-avatar :color="s.color" variant="tonal" size="48">
                <v-icon size="26">{{ s.icon }}</v-icon>
              </v-avatar>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-card class="mt-6">
      <v-card-title class="font-weight-bold pt-4 px-4">快速入口</v-card-title>
      <v-card-text class="pa-4">
        <v-row density="compact">
          <v-col cols="6" sm="4" md="3" v-for="m in menus" :key="m.to">
            <v-card
              class="menu-card hover-lift hover-tint text-center pa-4"
              variant="outlined"
              @click="router.push(m.to)"
            >
              <v-avatar color="primary" variant="tonal" size="40" class="mb-2">
                <v-icon>{{ m.icon }}</v-icon>
              </v-avatar>
              <div class="text-body-medium font-weight-medium">{{ m.title }}</div>
            </v-card>
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import api from '../plugins/axios'

const router = useRouter()
const auth = useAuthStore()

const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 6) return '夜深了'
  if (h < 12) return '早上好'
  if (h < 18) return '下午好'
  return '晚上好'
})

// 角色统计随 auth.roles 响应式更新：刷新后角色恢复时卡片不再停留在 "-"
const userCount = ref('-')
const datasetCount = ref('-')
const modelCount = ref('-')

const stats = computed(() => [
  { title: '用户', value: userCount.value, icon: 'mdi-account-group', color: 'info' },
  { title: '数据集', value: datasetCount.value, icon: 'mdi-database', color: 'success' },
  { title: '模型版本', value: modelCount.value, icon: 'mdi-brain', color: 'accent' },
  { title: '我的角色', value: auth.roles.join(' / ') || '-', icon: 'mdi-shield-account', color: 'warning' }
])

// 快速入口：与侧边栏一致按权限过滤
const menus = computed(() => {
  const all = [
    { to: '/datasets', title: '数据集管理', icon: 'mdi-database', perm: 'dataset:read' },
    { to: '/training/jobs', title: '训练任务', icon: 'mdi-rocket-launch', perm: 'training:read' },
    { to: '/models', title: '模型管理', icon: 'mdi-cube-outline', perm: 'model:read' },
    { to: '/serving', title: '模型服务', icon: 'mdi-server', perm: 'serving:read' }
  ]
  return all.filter((m) => !m.perm || auth.hasPerm(m.perm))
})

async function load() {
  try {
    const [u, d, m] = await Promise.all([
      api.get('/users?size=1'),
      api.get('/datasets?size=1'),
      api.get('/models?size=1')
    ])
    userCount.value = u.data.data.totalElements ?? '-'
    datasetCount.value = d.data.data.totalElements ?? '-'
    modelCount.value = m.data.data.totalElements ?? '-'
  } catch (e) { /* 忽略 */ }
}

onMounted(load)
</script>

<style scoped>
.menu-card {
  cursor: pointer;
}
</style>
