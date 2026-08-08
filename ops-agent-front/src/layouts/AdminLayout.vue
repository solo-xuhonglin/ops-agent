<template>
  <v-app>
    <v-navigation-drawer
      v-model="drawer"
      :rail="rail && mdAndUp"
      :temporary="!mdAndUp"
      :permanent="mdAndUp"
      color="surface"
      class="nav-drawer"
    >
      <div class="brand pa-4 d-flex align-center">
        <v-avatar color="primary" variant="tonal" size="36" class="mr-3">
          <v-icon>mdi-brain</v-icon>
        </v-avatar>
        <div v-if="!(rail && mdAndUp)">
          <div class="text-title-small font-weight-bold">ops-agent</div>
          <div class="text-body-small text-medium-emphasis">算法资产平台</div>
        </div>
      </div>
      <v-divider />
      <v-list nav density="comfortable" class="nav-list">
        <v-list-item
          v-for="item in menus"
          :key="item.to"
          :to="item.to"
          :prepend-icon="item.icon"
          :title="item.title"
          rounded="lg"
          color="primary"
          exact
          class="nav-item"
        />
      </v-list>
      <template #append>
        <v-list nav>
          <v-list-item
            @click="rail = !rail"
            :prepend-icon="rail ? 'mdi-chevron-right' : 'mdi-chevron-left'"
            title="收起"
            v-if="mdAndUp"
          />
        </v-list>
      </template>
    </v-navigation-drawer>

    <v-app-bar flat border="b" color="surface">
      <v-app-bar-nav-icon v-if="!mdAndUp" @click="drawer = !drawer" />
      <v-app-bar-title class="font-weight-bold">{{ currentTitle }}</v-app-bar-title>
      <v-spacer />
      <v-menu>
        <template #activator="{ props }">
          <v-btn v-bind="props" variant="text" class="text-none">
            <v-icon start>mdi-account-circle</v-icon>
            {{ auth.user?.displayName || auth.user?.username }}
          </v-btn>
        </template>
        <v-list>
          <v-list-item :subtitle="auth.roles.join(' / ')" :title="auth.user?.username" />
          <v-divider />
          <v-list-item prepend-icon="mdi-logout" title="退出登录" @click="logout" />
        </v-list>
      </v-menu>
    </v-app-bar>

    <v-main class="main-area">
      <v-container fluid class="pa-6">
        <router-view />
      </v-container>
    </v-main>
  </v-app>
</template>

<style scoped>
.main-area {
  background: rgb(var(--v-theme-background));
}
.nav-drawer {
  border-right: 1px solid rgba(0, 0, 0, 0.06);
}
.nav-list :deep(.v-list-item--active) {
  background: color-mix(in srgb, rgb(var(--v-theme-primary)) 10%, transparent);
}
.nav-list :deep(.v-list-item--active .v-list-item-title) {
  color: rgb(var(--v-theme-primary));
  font-weight: 600;
}
.nav-list :deep(.nav-item) {
  margin: 2px 8px;
}
</style>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useDisplay } from 'vuetify'

const { mdAndUp } = useDisplay()
const drawer = ref(true)
const rail = ref(false)
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const menus = computed(() => {
  const all = [
    { to: '/dashboard', title: '仪表盘', icon: 'mdi-view-dashboard', perm: null },
    { to: '/users', title: '用户管理', icon: 'mdi-account-group', perm: 'user:read' },
    { to: '/roles', title: '角色管理', icon: 'mdi-shield-account', perm: 'role:read' },
    { to: '/permissions', title: '权限管理', icon: 'mdi-key', perm: 'permission:read' },
    { to: '/datasets', title: '数据集管理', icon: 'mdi-database', perm: 'dataset:read' },
    { to: '/training/jobs', title: '训练任务', icon: 'mdi-rocket-launch', perm: 'training:read' },
    { to: '/models', title: '模型管理', icon: 'mdi-cube-outline', perm: 'model:read' },
    { to: '/serving', title: '模型服务', icon: 'mdi-server', perm: 'serving:read' },
    { to: '/agent/tools', title: 'Agent 工具', icon: 'mdi-wrench-outline', perm: 'agent:read' }
  ]
  return all.filter((m) => !m.perm || auth.hasPerm(m.perm))
})

const currentTitle = computed(() => route.meta.title || '控制台')

onMounted(async () => {
  if (!auth.user) {
    try { await auth.fetchMe() } catch (e) { router.push('/login') }
  }
})

function logout() {
  auth.logout()
  router.push('/login')
}
</script>
