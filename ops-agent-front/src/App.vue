<template>
  <v-app>
    <router-view />
    <AppConfirmDialog />
  </v-app>
</template>

<script setup>
import AppConfirmDialog from './components/AppConfirmDialog.vue'
</script>
