<template>
  <v-main class="d-flex align-center justify-center" style="min-height: 100vh">
    <v-card class="pa-6" width="400" elevation="8">
      <div class="text-center mb-4">
        <v-icon size="48" color="primary">mdi-weather-partly-cloudy</v-icon>
        <h2 class="text-h5 mt-2">ops-agent 控制台</h2>
        <p class="text-caption text-medium-emphasis">算法资产平台 · 登录</p>
      </div>
      <v-form @submit.prevent="onSubmit" ref="form">
        <v-text-field
          v-model="username"
          label="用户名"
          prepend-inner-icon="mdi-account"
          variant="outlined"
          :rules="[(v) => !!v || '请输入用户名']"
        />
        <v-text-field
          v-model="password"
          label="密码"
          type="password"
          prepend-inner-icon="mdi-lock"
          variant="outlined"
          :rules="[(v) => !!v || '请输入密码']"
        />
        <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>
        <v-btn type="submit" color="primary" block :loading="loading">登录</v-btn>
      </v-form>
    </v-card>
  </v-main>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import api from '../plugins/axios'

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
