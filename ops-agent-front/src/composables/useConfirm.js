import { reactive } from 'vue'

// 全局确认对话框状态（模块级单例）
// 由 AppConfirmDialog.vue 渲染，任意组件通过 useConfirm().confirmDialog(options) 调用
const state = reactive({
  visible: false,
  title: '操作确认',
  message: '',
  confirmText: '确认',
  cancelText: '取消',
  // 确认按钮颜色；danger 为 true 时自动使用 error
  color: 'primary',
  // 顶部图标与图标容器色调
  icon: 'mdi-help-circle-outline',
  iconColor: 'primary',
  // 危险操作（红色确认按钮 + 警示图标）
  danger: false,
  // 确认按钮 loading（用于提交类确认，点击后由调用方控制）
  loading: false,
  // 禁止点击遮罩/ESC 关闭
  persistent: true,
  _resolve: null
})

export function useConfirm() {
  /**
   * 弹出确认对话框，返回 Promise<boolean>
   * options: { title, message, confirmText, cancelText, danger, color, icon, persistent }
   */
  function confirmDialog(options = {}) {
    return new Promise((resolve) => {
      Object.assign(state, {
        visible: true,
        title: options.title ?? '操作确认',
        message: options.message ?? '',
        confirmText: options.confirmText ?? '确认',
        cancelText: options.cancelText ?? '取消',
        danger: !!options.danger,
        color: options.color ?? (options.danger ? 'error' : 'primary'),
        icon: options.icon ?? (options.danger ? 'mdi-alert-circle-outline' : 'mdi-help-circle-outline'),
        iconColor: options.iconColor ?? (options.danger ? 'error' : 'primary'),
        loading: false,
        persistent: options.persistent ?? true,
        _resolve: resolve
      })
    })
  }

  function resolveDialog(value) {
    state.visible = false
    state.loading = false
    const resolve = state._resolve
    state._resolve = null
    if (resolve) resolve(value)
  }

  function setConfirmLoading(loading) {
    state.loading = loading
  }

  return { state, confirmDialog, resolveDialog, setConfirmLoading }
}
