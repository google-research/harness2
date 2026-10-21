<script setup lang="ts">
/**
 * Initial stub for KpiParameterCard.vue
 *
 * Requirements:
 * - Dynamic status badge: Normal -> 'success', Warning -> 'warning', Critical -> 'error'
 * - Props typed strictly with TypeScript: title, currentValue, target, status, unit
 * - Display title, current value, target, and formatted deviation percentage: ((currentValue - target) / target) * 100
 */
export interface Props {
  title: string
  currentValue: number
  target: number
  status: 'Normal' | 'Warning' | 'Critical'
  unit?: string
}

const props = withDefaults(defineProps<Props>(), {
  unit: '',
})
</script>

<template>
  <v-card class="kpi-parameter-card">
    <v-card-title>{{ title }}</v-card-title>
    <v-card-text>
      <!-- Incomplete stub: badge and deviation percentage calculation needed -->
      <div class="current-value">{{ currentValue }} {{ unit }}</div>
    </v-card-text>
  </v-card>
</template>
