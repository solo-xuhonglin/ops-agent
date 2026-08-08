import 'vuetify/styles'
import { createVuetify } from 'vuetify'
import { aliases, mdi } from 'vuetify/iconsets/mdi'
import '@mdi/font/css/materialdesignicons.css'
import { VDateInput } from 'vuetify/components/VDateInput'

export default createVuetify({
  components: {
    VDateInput
  },
  icons: {
    defaultSet: 'mdi',
    aliases,
    sets: { mdi }
  },
  // 恢复 Vuetify 3 的断点阈值，避免 Vuetify 4 默认（md=840 等）改变既有响应式布局
  display: {
    thresholds: {
      md: 960,
      lg: 1280,
      xl: 1920,
      xxl: 2560
    }
  },
  theme: {
    defaultTheme: 'light',
    themes: {
      light: {
        dark: false,
        colors: {
          primary: '#5B6EF0',
          'primary-darken-1': '#4553D8',
          secondary: '#6B7280',
          accent: '#8B5CF6',
          error: '#EF4444',
          warning: '#F59E0B',
          info: '#3B82F6',
          success: '#10B981',
          background: '#F4F6FA',
          surface: '#FFFFFF'
        }
      }
    }
  },
  defaults: {
    VCard: {
      rounded: 'lg',
      elevation: 1
    },
    VBtn: {
      rounded: 'lg',
      style: 'text-transform:none'
    },
    VTextField: {
      variant: 'outlined',
      density: 'comfortable'
    },
    VSelect: {
      variant: 'outlined',
      density: 'comfortable'
    }
  }
})
