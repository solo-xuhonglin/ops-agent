<template>
  <v-main>
    <v-container class="text-center mt-10">
      <v-icon size="64" color="grey-lighten-1">mdi-alert-circle-outline</v-icon>
      <h2 class="text-headline-small mt-3">页面未找到</h2>
      <v-btn class="mt-4" color="primary" @click="$router.push('/dashboard')">返回仪表盘</v-btn>
    </v-container>
  </v-main>
</template>

<script setup>
</script>
