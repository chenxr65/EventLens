// 展示格式化工具
import { EVENT_STATUS } from './constants'

export function fmtPct(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—'
  const n = Number(v)
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

export function statusOf(key) {
  return EVENT_STATUS[key] || { label: key || '未知', type: 'info' }
}

export function fmtTime(s) {
  return (s || '').replace('T', ' ')
}
