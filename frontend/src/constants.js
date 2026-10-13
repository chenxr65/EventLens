// 共享枚举与展示映射（与后端字段一一对应）

export const EVENT_STATUS = {
  unverified: { label: '待核验', type: 'info' },
  supported: { label: '已有支持', type: 'primary' },
  confirmed: { label: '已确认', type: 'success' },
  disputed: { label: '存在争议', type: 'warning' },
  denied: { label: '已被否认', type: 'danger' },
  corrected: { label: '已更正', type: 'warning' },
  completed: { label: '已完成', type: 'success' },
  stale: { label: '证据较旧', type: 'info' },
  cancelled: { label: '已取消', type: 'info' },
}

export const CLAIM_TYPE = {
  fact: { label: '事实', type: 'primary', color: '#2f6bff' },
  opinion: { label: '观点', type: 'warning', color: '#9c27b0' },
  inference: { label: '推测', type: '', color: '#e67e22' },
  rumor: { label: '传闻', type: 'info', color: '#909399' },
}

export const EVIDENCE_STATUS = {
  supported: { label: '有证据支持', type: 'success' },
  contradicted: { label: '存在反证', type: 'danger' },
  disputed: { label: '存在争议', type: 'warning' },
  unverified: { label: '证据不足', type: 'info' },
  superseded: { label: '已被替代', type: 'warning' },
  stale: { label: '证据较旧', type: 'info' },
}

export const RELATION = {
  supports: { label: '支持', type: 'success' },
  contradicts: { label: '反驳', type: 'danger' },
  qualifies: { label: '限定', type: 'warning' },
  supersedes: { label: '取代', type: 'info' },
}

export const CHANGE_TYPE = {
  initial: { label: '首次记录', type: 'info' },
  update: { label: '更新', type: 'primary' },
  denial: { label: '否认', type: 'danger' },
  correction: { label: '更正', type: 'warning' },
  expiry: { label: '过期', type: 'info' },
  cancellation: { label: '取消', type: 'info' },
  duplicate: { label: '重复', type: 'info' },
}

export const WEIGHT_DIMENSIONS = [
  { key: 'R', label: '来源权威' },
  { key: 'D', label: '直接程度' },
  { key: 'I', label: '独立性' },
  { key: 'F', label: '时效性' },
  { key: 'Q', label: '完整性' },
]
