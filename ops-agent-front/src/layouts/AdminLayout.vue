<template>
  <v-app>
    <v-navigation-drawer
      v-model="drawer"
      :rail="rail && mdAndUp"
      :temporary="!mdAndUp"
      :permanent="mdAndUp"
      color="grey-lighten-5"
    >
      <v-list>
        <v-list-item
          prepend-icon="mdi-brain"
          title="ops-agent"
          subtitle="算法资产平台"
        />
      </v-list>
      <v-divider />
      <v-list nav density="comfortable">
        <v-list-item
          v-for="item in menus"
          :key="item.to"
          :to="item.to"
          :prepend-icon="item.icon"
          :title="item.title"
          rounded="lg"
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

    <v-app-bar flat border="b">
      <v-app-bar-nav-icon v-if="!mdAndUp" @click="drawer = !drawer" />
      <v-app-bar-title>{{ currentTitle }}</v-app-bar-title>
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

    <v-main>
      <v-container fluid>
        <router-view />
      </v-container>
    </v-main>
  </v-app>
</template>

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
    { to: '/datasets', title: '数据集管理', icon: 'mdi-database', perm: 'dataset:read' }
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
