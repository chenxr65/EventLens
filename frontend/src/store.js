import { reactive } from 'vue'
import { api } from './api'

// 轻量全局状态：概览统计（顶部导航徽标等共用）
export const store = reactive({
  overview: null,
  async refreshOverview() {
    try {
      this.overview = await api.overview()
    } catch (err) {
      console.error('overview 加载失败', err)
    }
  },
})
