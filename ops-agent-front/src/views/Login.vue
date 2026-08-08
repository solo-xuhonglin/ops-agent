<template>
  <v-main class="login-bg d-flex align-center justify-center">
    <v-card class="login-card pa-8" width="420" elevation="12" rounded="xl">
      <div class="text-center mb-6">
        <v-avatar color="primary" variant="tonal" size="64" class="mb-4">
          <v-icon size="36">mdi-weather-partly-cloudy</v-icon>
        </v-avatar>
        <h2 class="text-headline-small font-weight-bold">ops-agent 控制台</h2>
        <p class="text-body-medium text-medium-emphasis mt-1">算法资产平台 · 登录</p>
      </div>
      <v-form @submit.prevent="onSubmit" ref="form">
        <v-text-field
          v-model="username"
          label="用户名"
          prepend-inner-icon="mdi-account"
          :rules="[(v) => !!v || '请输入用户名']"
        />
        <v-text-field
          v-model="password"
          label="密码"
          type="password"
          prepend-inner-icon="mdi-lock"
          :rules="[(v) => !!v || '请输入密码']"
        />
        <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
        <v-btn type="submit" color="primary" block size="large" :loading="loading">登录</v-btn>
      </v-form>
    </v-card>
  </v-main>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')
const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

async function onSubmit() {
  error.value = ''
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    const redirect = route.query.redirect || '/dashboard'
    router.push(redirect)
  } catch (e) {
    error.value = e.response?.data?.message || '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-bg {
  min-height: 100vh;
  background: linear-gradient(
    135deg,
    rgb(var(--v-theme-primary)) 0%,
    rgb(var(--v-theme-accent)) 50%,
    rgb(var(--v-theme-secondary)) 100%
  );
}
.login-card {
  backdrop-filter: blur(4px);
}
</style>
