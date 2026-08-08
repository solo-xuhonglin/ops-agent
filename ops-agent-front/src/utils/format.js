// 统一的格式化工具：日期/时间/ID 展示全站一致，禁止再在组件里各自 new Date().toLocaleString()

/** '2026-08-09 00:34' —— 列表/抽屉里的时间戳 */
export function fmtDateTime(d) {
  if (!d) return '—'
  const dt = d instanceof Date ? d : new Date(d)
  if (Number.isNaN(dt.getTime())) return '—'
  const y = dt.getFullYear()
  const m = String(dt.getMonth() + 1).padStart(2, '0')
  const day = String(dt.getDate()).padStart(2, '0')
  const hh = String(dt.getHours()).padStart(2, '0')
  const mm = String(dt.getMinutes()).padStart(2, '0')
  return `${y}-${m}-${day} ${hh}:${mm}`
}

/** '2026-08-09' —— 日期选择器/日期范围 */
export function fmtDate(d) {
  if (!d) return ''
  const dt = d instanceof Date ? d : new Date(d)
  if (Number.isNaN(dt.getTime())) return ''
  const y = dt.getFullYear()
  const m = String(dt.getMonth() + 1).padStart(2, '0')
  const day = String(dt.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** 长 ID 截断展示（taskId 等） */
export function shortId(id, len = 8) {
  return id ? String(id).slice(0, len) : ''
}
