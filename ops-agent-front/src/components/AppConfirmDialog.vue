<template>
  <v-dialog v-model="state.visible" width="420" :persistent="state.persistent">
    <v-card rounded="xl" :elevation="0" class="confirm-card">
      <div class="d-flex flex-column align-center text-center pa-6 pb-2">
        <v-avatar :color="state.iconColor" variant="tonal" size="60" class="mb-3">
          <v-icon :color="state.iconColor" size="34">{{ state.icon }}</v-icon>
        </v-avatar>
        <div class="text-title-large font-weight-bold">{{ state.title }}</div>
        <div v-if="state.message" class="text-body-medium text-medium-emphasis mt-2" style="white-space: pre-line; line-height: 1.6">
          {{ state.message }}
        </div>
      </div>
      <v-card-actions class="pa-6 pt-3">
        <v-spacer />
        <v-btn variant="text" :color="state.danger ? 'medium-emphasis' : 'secondary'" min-width="88" @click="resolveDialog(false)">
          {{ state.cancelText }}
        </v-btn>
        <v-btn :color="state.color" variant="flat" min-width="88" :loading="state.loading" @click="resolveDialog(true)">
          {{ state.confirmText }}
        </v-btn>
        <v-spacer />
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
import { useConfirm } from '../composables/useConfirm'

const { state, resolveDialog } = useConfirm()
</script>

<style scoped>
.confirm-card {
  border: 1px solid rgba(0, 0, 0, 0.08);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.12) !important;
}
</style>
