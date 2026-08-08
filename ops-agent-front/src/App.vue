<template>
  <v-app>
    <router-view />
    <AppConfirmDialog />
    <AppSnackbar />
    <AgentAssistant />
  </v-app>
</template>

<script setup>
import AppConfirmDialog from './components/AppConfirmDialog.vue'
import AppSnackbar from './components/AppSnackbar.vue'
import AgentAssistant from './components/AgentAssistant.vue'
</script>
