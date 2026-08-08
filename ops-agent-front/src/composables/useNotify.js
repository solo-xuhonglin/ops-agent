import { reactive } from 'vue'

// 全局轻提示（snackbar）状态（模块级单例）
// 由 AppSnackbar.vue 渲染，任意组件通过 useNotify().notify*(...) 调用，替代散落的 alert()
const state = reactive({
  visible: false,
  text: '',
  // success | error | warning | info
  color: 'info',
  timeout: 3000
})

let timer = null

export function useNotify() {
  function show(text, color = 'info', timeout = 3000) {
    if (timer) clearTimeout(timer)
    Object.assign(state, { visible: true, text, color, timeout })
    timer = setTimeout(() => { state.visible = false }, timeout + 100)
  }

  return {
    state,
    notify: show,
    notifySuccess: (text) => show(text, 'success'),
    notifyError: (text) => show(text, 'error', 4000),
    notifyWarning: (text) => show(text, 'warning'),
    notifyInfo: (text) => show(text, 'info')
  }
}

/** 从 axios 错误里提取用户可读信息（后端统一 ApiResponse.message） */
export function errMsg(e, fallback = '操作失败') {
  return e?.response?.data?.message || fallback
}
