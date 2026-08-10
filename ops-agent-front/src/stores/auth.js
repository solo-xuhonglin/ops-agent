import { defineStore } from 'pinia'
import api from '../plugins/axios'

// 用户信息持久化 key：user/roles/permissions 随 token 一起落 localStorage，
// 页面刷新后同步还原，避免角色/权限短暂丢失（路由守卫与菜单渲染依赖它们）。
const PROFILE_KEY = 'authProfile'

function loadProfile() {
  try {
    const raw = localStorage.getItem(PROFILE_KEY)
    if (raw) return JSON.parse(raw)
  } catch (e) { /* ignore corrupted cache */ }
  return null
}

export const useAuthStore = defineStore('auth', {
  state: () => {
    const profile = loadProfile()
    return {
      // token 同步到 localStorage，并保留在 state 里以保证 isLoggedIn 响应式
      token: localStorage.getItem('token') || null,
      user: profile?.user || null,
      roles: profile?.roles || [],
      permissions: profile?.permissions || []
    }
  },
  getters: {
    isLoggedIn: (state) => !!state.token,
    hasPerm: (state) => (code) => state.permissions.includes(code)
  },
  actions: {
    persistProfile() {
      localStorage.setItem(PROFILE_KEY, JSON.stringify({
        user: this.user,
        roles: this.roles,
        permissions: this.permissions
      }))
    },
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
      this.persistProfile()
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
      this.persistProfile()
      return res
    },
    logout() {
      localStorage.removeItem('token')
      localStorage.removeItem('refreshToken')
      localStorage.removeItem(PROFILE_KEY)
      this.token = null
      this.user = null
      this.roles = []
      this.permissions = []
    }
  }
})
