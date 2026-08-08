<template>
  <v-app>
    <router-view />
    <AppConfirmDialog />
    <AgentAssistant />
  </v-app>
</template>

<script setup>
import AppConfirmDialog from './components/AppConfirmDialog.vue'
import AgentAssistant from './components/AgentAssistant.vue'
</script>
