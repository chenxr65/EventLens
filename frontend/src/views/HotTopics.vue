<template>
  <div class="page">
    <div class="flex-between mb12">
      <h3 style="margin: 0">
        热点榜
        <span class="meta">数据源：iFinD 热点事件资讯 · 每 30 分钟自动刷新 · 断网时保留最近快照</span>
      </h3>
      <div class="flex-gap">
        <span v-if="batchAt" class="meta">当前批次 {{ fmtTime(batchAt) }}（与上一批对比排名变化）</span>
        <el-button size="small" type="primary" :loading="refreshing" @click="refresh">
          立即刷新
        </el-button>
      </div>
    </div>

    <el-alert v-if="tracking" type="warning" :closable="false" class="mb12" show-icon>
      <template #title>
        <template v-if="tracking.status !== 'failed'">
          正在追踪「{{ tracking.title }}」：采集材料 → AI 分析 → 生成事件…（已用时
          {{ tracking.elapsed }}s，完成后自动跳转）
        </template>
        <template v-else>
          追踪失败：{{ tracking.error }}（请检查网络与数据源 Key 后重试）
        </template>
      </template>
    </el-alert>

    <el-alert
      v-if="refreshError"
      type="error"
      :closable="true"
      class="mb12"
      show-icon
      :title="`刷新失败：${refreshError}。榜单保留最近一次快照，不伪造实时数据。`"
      @close="refreshError = ''"
    />

    <el-card shadow="never">
      <div v-for="item in items" :key="item.id" class="hot-row">
        <div class="hot-rank" :class="{ top: item.rank <= 3 }">{{ item.rank }}</div>

        <div class="hot-main">
          <div class="hot-title">{{ item.title }}</div>
          <div class="flex-gap mt8">
            <span class="meta">来源：{{ item.source || '—' }}</span>
            <el-tag
              v-for="name in item.related_names"
              :key="name"
              size="small"
              type="danger"
              effect="plain"
            >
              {{ name }}（已关注）
            </el-tag>
            <el-tag
              v-for="code in item.related_codes.filter((c) => !item.related_names.length || !c.includes('.'))"
              :key="code"
              size="small"
              effect="plain"
            >
              {{ code }}
            </el-tag>
          </div>
        </div>

        <div class="hot-heat">
          <div class="heat-value">{{ item.heat ?? '—' }}</div>
          <div class="heat-bar">
            <div class="heat-inner" :style="{ width: heatWidth(item.heat) }"></div>
          </div>
          <div class="meta">热度</div>
        </div>

        <div class="hot-move">
          <span v-if="item.movement === null" class="move-new">NEW</span>
          <span v-else-if="item.movement > 0" class="move-up">↑{{ item.movement }}</span>
          <span v-else-if="item.movement < 0" class="move-down">↓{{ -item.movement }}</span>
          <span v-else class="meta">—</span>
        </div>

        <div class="hot-action">
          <template v-if="item.tracked_scope">
            <el-button size="small" @click="$router.push(`/events/${item.tracked_event_id}`)">
              查看事件 →
            </el-button>
            <el-button size="small" link type="danger" @click="untrack(item)">
              取消追踪
            </el-button>
          </template>
          <el-button
            v-else
            size="small"
            type="primary"
            plain
            :disabled="!!tracking && tracking.status !== 'failed'"
            @click="track(item)"
          >
            追踪为事件
          </el-button>
        </div>
      </div>
      <el-empty v-if="!items.length" description="暂无热点数据（数据源不可用时会保留最近快照）" />
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { fmtTime } from '../format'

const router = useRouter()
const items = ref([])
const batchAt = ref(null)
const refreshing = ref(false)
const refreshError = ref('')
const tracking = ref(null)
let pollTimer = null
let startedAt = 0

async function load() {
  const data = await api.hot()
  items.value = data.items
  batchAt.value = data.batch_at
}

async function refresh() {
  refreshing.value = true
  refreshError.value = ''
  try {
    const info = await api.hotRefresh()
    await load()
    ElMessage.success(`已刷新：新批次 ${info.count} 条`)
  } catch (err) {
    refreshError.value = err.message
  } finally {
    refreshing.value = false
  }
}

async function track(item) {
  try {
    const { job_id } = await api.hotTrack(item.id)
    startedAt = Date.now()
    tracking.value = { title: item.title, status: 'running', elapsed: 0 }
    pollTimer = setInterval(async () => {
      try {
        const job = await api.job(job_id)
        tracking.value.elapsed = Math.round((Date.now() - startedAt) / 1000)
        if (job.status === 'done') {
          clearInterval(pollTimer)
          tracking.value = null
          ElMessage.success('追踪完成：已生成新事件')
          await load()
          if (job.result?.event_id) {
            router.push(`/events/${job.result.event_id}`)
          }
        } else if (job.status === 'failed') {
          clearInterval(pollTimer)
          tracking.value = { title: item.title, status: 'failed', error: job.error }
        }
      } catch (err) {
        clearInterval(pollTimer)
        tracking.value = { title: item.title, status: 'failed', error: err.message }
      }
    }, 3000)
  } catch (err) {
    ElMessage.error(`追踪启动失败：${err.message}`)
  }
}

async function untrack(item) {
  try {
    await ElMessageBox.confirm(
      `取消追踪「${item.title}」？其生成的事件将停止追踪（历史数据保留）。`,
      '取消追踪',
      { type: 'warning', confirmButtonText: '取消追踪', cancelButtonText: '再想想' }
    )
  } catch {
    return
  }
  await api.hotUntrack(item.id)
  ElMessage.success('已取消追踪')
  await load()
}

function heatWidth(heat) {
  const max = Math.max(...items.value.map((i) => i.heat || 0), 1)
  return `${Math.round(((heat || 0) / max) * 100)}%`
}

onMounted(load)
onBeforeUnmount(() => pollTimer && clearInterval(pollTimer))
</script>

<style scoped>
.hot-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 4px;
  border-bottom: 1px solid #f0f2f5;
}
.hot-row:last-child {
  border-bottom: none;
}
.hot-rank {
  width: 30px;
  height: 30px;
  border-radius: 6px;
  background: #f0f2f5;
  color: #8a919f;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  flex-shrink: 0;
}
.hot-rank.top {
  background: #d03050;
  color: #fff;
}
.hot-main {
  flex: 1;
  min-width: 0;
}
.hot-title {
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
}
.hot-heat {
  width: 110px;
  text-align: center;
  flex-shrink: 0;
}
.heat-value {
  font-size: 16px;
  font-weight: 700;
  color: #d03050;
}
.heat-bar {
  height: 5px;
  background: #f0f2f5;
  border-radius: 3px;
  overflow: hidden;
  margin: 3px 0;
}
.heat-inner {
  height: 100%;
  background: linear-gradient(90deg, #ff9a76, #d03050);
}
.hot-move {
  width: 46px;
  text-align: center;
  font-size: 12px;
  flex-shrink: 0;
}
.move-new {
  color: #2f6bff;
  font-weight: 700;
}
.move-up {
  color: #d03050;
}
.move-down {
  color: #1a9e5c;
}
.hot-action {
  min-width: 104px;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
  flex-shrink: 0;
}
</style>
