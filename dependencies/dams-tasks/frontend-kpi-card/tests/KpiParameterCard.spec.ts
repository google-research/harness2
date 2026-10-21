import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import KpiParameterCard from '../source/KpiParameterCard.vue'

describe('KpiParameterCard.vue', () => {
  it('renders title, current value, target, and unit', () => {
    const wrapper = mount(KpiParameterCard, {
      props: {
        title: 'Parameter P-04 (OEE)',
        currentValue: 92.5,
        target: 95.0,
        status: 'Warning',
        unit: '%',
      },
      global: {
        stubs: {
          'v-card': { template: '<div class="v-card"><slot /></div>' },
          'v-card-title': { template: '<div class="v-card-title"><slot /></div>' },
          'v-card-text': { template: '<div class="v-card-text"><slot /></div>' },
          'v-chip': { template: '<span class="v-chip" :class="color"><slot /></span>', props: ['color'] },
        },
      },
    })

    expect(wrapper.text()).toContain('Parameter P-04 (OEE)')
    expect(wrapper.text()).toContain('92.5')
    expect(wrapper.text()).toContain('95')
  })

  it('renders correct badge color mapping: Normal=success, Warning=warning, Critical=error', () => {
    const testCases: Array<{ status: 'Normal' | 'Warning' | 'Critical'; expectedColor: string }> = [
      { status: 'Normal', expectedColor: 'success' },
      { status: 'Warning', expectedColor: 'warning' },
      { status: 'Critical', expectedColor: 'error' },
    ]

    for (const { status, expectedColor } of testCases) {
      const wrapper = mount(KpiParameterCard, {
        props: {
          title: 'Test KPI',
          currentValue: 80,
          target: 100,
          status,
        },
        global: {
          stubs: {
            'v-card': { template: '<div class="v-card"><slot /></div>' },
            'v-card-title': { template: '<div class="v-card-title"><slot /></div>' },
            'v-card-text': { template: '<div class="v-card-text"><slot /></div>' },
            'v-chip': {
              template: '<span class="v-chip" :data-color="color">{{ $slots.default ? "" : "" }}<slot /></span>',
              props: ['color'],
            },
          },
        },
      })

      const chip = wrapper.find('.v-chip')
      expect(chip.exists()).toBe(true)
      expect(chip.attributes('data-color')).toBe(expectedColor)
    }
  })

  it('calculates and formats deviation percentage correctly', () => {
    const wrapper = mount(KpiParameterCard, {
      props: {
        title: 'Production Yield',
        currentValue: 80,
        target: 100,
        status: 'Warning',
      },
      global: {
        stubs: {
          'v-card': { template: '<div class="v-card"><slot /></div>' },
          'v-card-title': { template: '<div class="v-card-title"><slot /></div>' },
          'v-card-text': { template: '<div class="v-card-text"><slot /></div>' },
          'v-chip': { template: '<span class="v-chip"><slot /></span>' },
        },
      },
    })

    // Deviation = ((80 - 100) / 100) * 100 = -20%
    expect(wrapper.text()).toMatch(/-20(\.0+)?%/)
  })
})
