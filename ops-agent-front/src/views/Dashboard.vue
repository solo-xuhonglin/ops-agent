<template>
  <div>
    <h2 class="text-h5 mb-1">欢迎回来，{{ auth.user?.displayName || auth.user?.username }}</h2>
    <p class="text-medium-emphasis mb-6">算法资产平台 · 将 LSTM 天气模型工具化、Agent 化、平台化</p>

    <v-row>
      <v-col cols="12" sm="6" md="3" v-for="s in stats" :key="s.title">
        <v-card rounded="lg" elevation="2">
          <v-card-text>
            <div class="d-flex align-center justify-space-between">
              <div>
                <div class="text-overline text-medium-emphasis">{{ s.title }}</div>
                <div class="text-h4">{{ s.value }}</div>
              </div>
              <v-avatar color="primary" variant="tonal" size="44">
                <v-icon>{{ s.icon }}</v-icon>
              </v-avatar>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-card rounded="lg" class="mt-6" elevation="2">
      <v-card-title>快速入口</v-card-title>
      <v-card-text>
        <v-chip
          v-for="m in menus"
          :key="m.to"
          :prepend-icon="m.icon"
          class="ma-1"
          color="primary"
          variant="outlined"
          @click="router.push(m.to)"
          style="cursor:pointer"
        >
          {{ m.title }}
        </v-chip>
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import api from '../plugins/axios'

const router = useRouter()
const auth = useAuthStore()
const stats = ref([
  { title: '用户', value: '-', icon: 'mdi-account-group' },
  { title: '数据集', value: '-', icon: 'mdi-database' },
  { title: '模型版本', value: '-', icon: 'mdi-brain' },
  { title: '我的角色', value: (auth.roles.join(' / ') || '-'), icon: 'mdi-shield-account' }
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
