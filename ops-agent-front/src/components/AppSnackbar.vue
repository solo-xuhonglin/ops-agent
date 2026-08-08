<template>
  <v-snackbar
    v-model="state.visible"
    :color="state.color"
    :timeout="state.timeout"
    location="top"
    rounded="lg"
    elevation="6"
  >
    {{ state.text }}
  </v-snackbar>
</template>

<script setup>
import { useNotify } from '../composables/useNotify'

const { state } = useNotify()
</script>
