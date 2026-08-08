import { defineStore } from 'pinia'
import api from '../plugins/axios'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    // token 同步到 localStorage，并保留在 state 里以保证 isLoggedIn 响应式
    token: localStorage.getItem('token') || null,
    user: null,
    roles: [],
    permissions: []
  }),
  getters: {
    isLoggedIn: (state) => !!state.token,
    hasPerm: (state) => (code) => state.permissions.includes(code)
  },
  actions: {
    async login(username, password) {
      const { data } = await api.post('/auth/login', { username, password })
      const res = data.data
      localStorage.setItem('token', res.token)
      localStorage.setItem('refreshToken', res.refreshToken)
      this.token = res.token
      this.user = {
        id: res.userId,
        username: res.username,
        displayName: res.displayName
      }
      this.roles = res.roles || []
      this.permissions = res.permissions || []
      return res
    },
    async fetchMe() {
      const { data } = await api.get('/auth/me')
      const res = data.data
      this.user = {
        id: res.userId,
        username: res.username,
        displayName: res.displayName
      }
      this.roles = res.roles || []
      this.permissions = res.permissions || []
      return res
    },
    logout() {
      localStorage.removeItem('token')
      localStorage.removeItem('refreshToken')
      this.token = null
      this.user = null
      this.roles = []
      this.permissions = []
    }
  }
})
