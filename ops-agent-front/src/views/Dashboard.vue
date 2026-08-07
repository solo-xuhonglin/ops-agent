<template>
  <div>
    <div class="mb-6">
      <h2 class="text-h5 font-weight-bold mb-1">{{ greeting }}，{{ auth.user?.displayName || auth.user?.username }}</h2>
      <p class="text-medium-emphasis">算法资产平台 · 将 LSTM 天气模型工具化、Agent 化、平台化</p>
    </div>

    <v-row>
      <v-col cols="12" sm="6" md="3" v-for="s in stats" :key="s.title">
        <v-card class="stat-card" rounded="lg">
          <v-card-text>
            <div class="d-flex align-center justify-space-between">
              <div>
                <div class="text-overline text-medium-emphasis">{{ s.title }}</div>
                <div class="text-h4 font-weight-bold mt-1">{{ s.value }}</div>
              </div>
              <v-avatar :color="s.color" variant="tonal" size="48">
                <v-icon size="26">{{ s.icon }}</v-icon>
              </v-avatar>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-card rounded="lg" class="mt-6">
      <v-card-title class="font-weight-bold pt-4 px-4">快速入口</v-card-title>
      <v-card-text class="pa-4">
        <v-row dense>
          <v-col cols="6" sm="4" md="3" v-for="m in menus" :key="m.to">
            <v-card
              class="menu-card text-center pa-4"
              variant="outlined"
              rounded="lg"
              @click="router.push(m.to)"
            >
              <v-avatar color="primary" variant="tonal" size="40" class="mb-2">
                <v-icon>{{ m.icon }}</v-icon>
              </v-avatar>
              <div class="text-body-2 font-weight-medium">{{ m.title }}</div>
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

const stats = ref([
  { title: '用户', value: '-', icon: 'mdi-account-group', color: 'info' },
  { title: '数据集', value: '-', icon: 'mdi-database', color: 'success' },
  { title: '模型版本', value: '-', icon: 'mdi-brain', color: 'accent' },
  { title: '我的角色', value: (auth.roles.join(' / ') || '-'), icon: 'mdi-shield-account', color: 'warning' }
])

const menus = [
  { to: '/users', title: '用户管理', icon: 'mdi-account-group' },
  { to: '/roles', title: '角色管理', icon: 'mdi-shield-account' },
  { to: '/permissions', title: '权限管理', icon: 'mdi-key' },
  { to: '/datasets', title: '数据集管理', icon: 'mdi-database' }
]

async function load() {
  try {
    const [u, d, m] = await Promise.all([
      api.get('/users?size=1'),
      api.get('/datasets?size=1'),
      api.get('/models?size=1')
    ])
    stats.value[0].value = u.data.data.totalElements ?? '-'
    stats.value[1].value = d.data.data.totalElements ?? '-'
    stats.value[2].value = m.data.data.totalElements ?? '-'
  } catch (e) { /* 忽略 */ }
}

onMounted(load)
</script>

<style scoped>
.stat-card {
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.08);
}
.menu-card {
  cursor: pointer;
  transition: transform 0.15s ease, background 0.15s ease;
}
.menu-card:hover {
  transform: translateY(-2px);
  background: rgba(var(--v-theme-primary), 0.05);
}
</style>
