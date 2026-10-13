// 前端数据访问层：与 FastAPI /api/* 一一对应

async function request(path, options = {}) {
  const resp = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!resp.ok) {
    throw new Error(`${resp.status} ${await resp.text()}`)
  }
  return resp.json()
}

function qs(params) {
  const entries = Object.entries(params || {}).filter(
    ([, v]) => v !== undefined && v !== null && v !== ''
  )
  return entries.length ? `?${new URLSearchParams(entries).toString()}` : ''
}

export const api = {
  overview: () => request('/api/overview'),
  events: (params = {}) => request(`/api/events${qs(params)}`),
  event: (id) => request(`/api/events/${id}`),
  eventMarket: (id) => request(`/api/events/${id}/market`),
  untrackEvent: (id) => request(`/api/events/${id}/untrack`, { method: 'POST' }),
  trackEvent: (id) => request(`/api/events/${id}/track`, { method: 'POST' }),
  document: (id) => request(`/api/documents/${id}`),
  notifications: () => request('/api/notifications'),
  markRead: (id) => request(`/api/notifications/${id}/read`, { method: 'POST' }),
  readAll: () => request('/api/notifications/read-all', { method: 'POST' }),
  watchlist: () => request('/api/watchlist'),
  addWatch: (item) =>
    request('/api/watchlist', { method: 'POST', body: JSON.stringify(item) }),
  removeWatch: (code) => request(`/api/watchlist/${code}`, { method: 'DELETE' }),
  // 材料收件箱（新材料导入动线）
  inbox: () => request('/api/inbox'),
  inboxImport: (itemIds = null) =>
    request('/api/inbox/import', {
      method: 'POST',
      body: JSON.stringify({ item_ids: itemIds }),
    }),
  // 热点榜与一键追踪
  hot: () => request('/api/hot'),
  hotRefresh: () => request('/api/hot/refresh', { method: 'POST' }),
  hotTrack: (id) => request(`/api/hot/${id}/track`, { method: 'POST' }),
  hotUntrack: (id) => request(`/api/hot/${id}/untrack`, { method: 'POST' }),
  // 后台任务
  job: (id) => request(`/api/jobs/${id}`),
}
