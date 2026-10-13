<template>
  <div class="page">
    <!-- 概览统计 -->
    <el-row :gutter="12" class="stat-row">
      <el-col v-for="s in statCards" :key="s.label" :span="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-value" :style="{ color: s.color }">{{ s.value }}</div>
          <div class="stat-label">{{ s.label }}</div>
          <div class="meta">{{ s.hint }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 材料收件箱（新材料导入动线） -->
    <el-card v-if="inbox.pending > 0 || importReport" shadow="never" class="mb12">
      <div class="flex-between">
        <div class="flex-gap">
          <el-tag type="warning" effect="dark" size="small">材料收件箱</el-tag>
          <span v-if="inbox.pending > 0">{{ inbox.pending }} 条待处理新材料</span>
          <span v-else>导入任务已完成</span>
          <span class="meta">导入后自动触发 AI 分析管线：识别重复 → 并入事件 → 更新证据与结论</span>
        </div>
        <div class="flex-gap">
          <el-button size="small" @click="showInbox = !showInbox">
            {{ showInbox ? '收起明细' : '查看明细' }}
          </el-button>
          <el-button
            size="small"
            type="primary"
            :loading="importing"
            :disabled="inbox.pending === 0"
            @click="importAll"
          >
            一键导入全部
          </el-button>
        </div>
      </div>

      <div v-if="showInbox || importReport" class="mt8">
        <div v-for="it in inbox.items" :key="it.id" class="inbox-item">
          <el-tag size="small" :type="inboxStatusType(it.status)" effect="plain">
            {{ inboxStatusLabel(it.status) }}
          </el-tag>
          <span class="meta">[{{ it.published_at }}] {{ it.source_name }}</span>
          <span class="inbox-title">{{ it.title }}</span>
        </div>
      </div>

      <el-alert
        v-if="importReport"
        type="success"
        :closable="false"
        class="mt8"
        show-icon
        :title="importTitle"
      />
      <el-alert
        v-if="importError"
        type="error"
        :closable="false"
        class="mt8"
        show-icon
        :title="`导入失败：${importError}`"
      />
    </el-card>

    <!-- 数据与质检说明 -->
    <el-alert v-if="ov" type="info" :closable="false" show-icon class="mb12">
      <template #title>
        监控 {{ ov.events_total }} 个投资事件 · 有效材料 {{ ov.relevant_documents }} 条（AI
        语义过滤无关材料 {{ ov.noise_documents }} 条）· 证据 {{ ov.evidence_total }} 条，引用回溯校验通过率
        {{ quoteRate }}（{{ ov.quote_verified }}/{{ ov.evidence_total }}）
      </template>
    </el-alert>

    <!-- 过滤与搜索 -->
    <div class="flex-between mb12">
      <div class="flex-gap">
        <el-radio-group v-model="statusFilter" @change="load">
          <el-radio-button value="">全部</el-radio-button>
          <el-radio-button v-for="(v, k) in filters" :key="k" :value="k">
            {{ v.label }}
          </el-radio-button>
        </el-radio-group>
        <el-tag v-if="thscode" closable type="primary" @close="clearStockFilter">
          标的筛选：{{ thscode }}
        </el-tag>
        <el-checkbox v-model="showUntracked" @change="load">
          显示已停止追踪（{{ ov?.untracked_total ?? 0 }}）
        </el-checkbox>
      </div>
      <div class="flex-gap">
        <el-input
          v-model="query"
          placeholder="搜索事件关键词 / 标的"
          clearable
          style="width: 240px"
          @keyup.enter="load"
          @clear="load"
        >
          <template #append>
            <el-button @click="load">搜索</el-button>
          </template>
        </el-input>
      </div>
    </div>

    <!-- 事件卡片 -->
    <el-card
      v-for="e in events"
      :key="e.id"
      shadow="hover"
      class="event-card cursor-pointer mb12"
      @click="$router.push(`/events/${e.id}`)"
    >
      <div class="flex-between">
        <div class="flex-gap">
          <el-tag :type="statusOf(e.status).type" effect="dark" size="small">
            {{ statusOf(e.status).label }}
          </el-tag>
          <span class="event-title">{{ e.title }}</span>
        </div>
        <span class="meta">最后更新 {{ e.last_update }}</span>
      </div>

      <div class="flex-gap mt8">
        <el-tag
          v-for="s in cardStocks(e)"
          :key="s.thscode"
          size="small"
          effect="plain"
          class="stock-tag"
          @click.stop="$router.push(`/?thscode=${s.thscode}`)"
        >
          {{ s.stock_name || s.thscode }}
          <span
            v-if="s.snapshot"
            :class="(s.snapshot.change_pct ?? 0) >= 0 ? 'up' : 'down'"
            style="margin-left: 4px"
          >
            {{ fmtPct(s.snapshot.change_pct) }}
          </span>
        </el-tag>
        <el-tag v-if="e.event_type" size="small" type="info" effect="plain">
          {{ e.event_type }}
        </el-tag>
      </div>

      <p class="conclusion">{{ e.conclusion }}</p>

      <div class="flex-between">
        <span class="meta">
          材料 {{ e.documents_count }} · 主张 {{ e.claims_count }} ·
          证据 {{ e.evidence_count }} · 版本 {{ e.versions_count }}
        </span>
        <div class="flex-gap">
          <el-tag v-if="!e.tracked" size="small" type="info" effect="plain">已停止追踪</el-tag>
          <el-tag
            v-if="e.latest_notification"
            size="small"
            :type="e.latest_notification.severity === 'important' ? 'danger' : 'info'"
            effect="light"
          >
            最新变化：{{ e.latest_notification.diff_summary }}
            <template v-if="e.unread_count">（未读 {{ e.unread_count }}）</template>
          </el-tag>
          <el-button
            size="small"
            link
            :type="e.tracked ? 'danger' : 'primary'"
            @click.stop="toggleTrack(e)"
          >
            {{ e.tracked ? '停止追踪' : '重新追踪' }}
          </el-button>
        </div>
      </div>
    </el-card>

    <el-empty v-if="!loading && !events.length" description="没有符合条件的事件" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { store } from '../store'
import { fmtPct, statusOf } from '../format'

const route = useRoute()
const router = useRouter()

const events = ref([])
const loading = ref(false)
const statusFilter = ref('')
const thscode = ref((route.query.thscode || '').toString())
const query = ref('')
const showUntracked = ref(false)
const ov = computed(() => store.overview)

// 收件箱状态
const inbox = ref({ items: [], pending: 0 })
const showInbox = ref(false)
const importing = ref(false)
const importReport = ref(null)
const importError = ref('')
let pollTimer = null

const filters = {
  disputed: { label: '存在争议' },
  supported: { label: '已有支持' },
  denied: { label: '已被否认' },
  unverified: { label: '待核验' },
}

const quoteRate = computed(() => {
  if (!ov.value?.evidence_total) return '—'
  return `${((ov.value.quote_verified / ov.value.evidence_total) * 100).toFixed(0)}%`
})

const statCards = computed(() => {
  const o = ov.value || {}
  return [
    { label: '追踪事件', value: o.events_total ?? '—', hint: '全部完成版本演化', color: '#2f6bff' },
    { label: '争议中事件', value: o.disputed_total ?? '—', hint: '存在证据冲突', color: '#e67e22' },
    { label: '未读通知', value: o.unread_notifications ?? '—', hint: '状态变化提醒', color: '#d03050' },
    { label: '关注标的', value: o.watchlist_total ?? '—', hint: '关联行情验证', color: '#1a9e5c' },
  ]
})

const importTitle = computed(() => {
  const r = importReport.value
  if (!r) return ''
  const parts = [`导入 ${r.imported_count} 条`]
  if (r.duplicate_count) parts.push(`识别重复 ${r.duplicate_count} 条（未产生新增）`)
  const scopes = Object.values(r.pipeline || {})
  if (scopes.length) {
    const ev = scopes[0]
    parts.push(`已触发事件增量更新：材料/证据重新校验 ${ev.evidence_verified}/${ev.evidence} 通过`)
  }
  return parts.join(' · ')
})

function inboxStatusType(status) {
  return { pending: 'warning', imported: 'success', duplicate: 'info', failed: 'danger' }[status] || 'info'
}
function inboxStatusLabel(status) {
  return { pending: '待处理', imported: '已导入', duplicate: '重复', failed: '失败' }[status] || status
}

async function load() {
  loading.value = true
  try {
    events.value = (
      await api.events({
        status: statusFilter.value,
        q: query.value,
        thscode: thscode.value,
        include_untracked: showUntracked.value ? 1 : 0,
      })
    ).items
  } catch (err) {
    ElMessage.error(`事件加载失败：${err.message}`)
  } finally {
    loading.value = false
  }
}

async function loadInbox() {
  try {
    inbox.value = await api.inbox()
  } catch (err) {
    console.error(err)
  }
}

function clearStockFilter() {
  thscode.value = ''
  if (route.query.thscode) router.replace({ path: '/' })
  load()
}

// 卡片只展示非低相关的标的标签（低相关仅在同日行情联动等场景出现，详情页仍完整展示）
function cardStocks(e) {
  const filtered = (e.stocks || []).filter((s) => s.relevance_level !== 'low')
  return filtered.length ? filtered : e.stocks || []
}

async function toggleTrack(e) {
  if (e.tracked) {
    try {
      await ElMessageBox.confirm(
        `停止追踪「${e.title}」？事件数据与历史版本将完整保留，可在"显示已停止追踪"中随时恢复。`,
        '停止追踪',
        { type: 'warning', confirmButtonText: '停止追踪', cancelButtonText: '取消' }
      )
    } catch {
      return
    }
    await api.untrackEvent(e.id)
    ElMessage.success('已停止追踪（历史保留，可恢复）')
  } else {
    await api.trackEvent(e.id)
    ElMessage.success('已重新追踪')
  }
  await Promise.all([load(), store.refreshOverview()])
}

async function importAll() {
  importing.value = true
  importReport.value = null
  importError.value = ''
  try {
    const { job_id } = await api.inboxImport(null)
    pollTimer = setInterval(async () => {
      try {
        const job = await api.job(job_id)
        if (job.status === 'done') {
          clearInterval(pollTimer)
          importing.value = false
          importReport.value = job.result
          await Promise.all([loadInbox(), load(), store.refreshOverview()])
        } else if (job.status === 'failed') {
          clearInterval(pollTimer)
          importing.value = false
          importError.value = job.error
        }
      } catch (err) {
        clearInterval(pollTimer)
        importing.value = false
        importError.value = err.message
      }
    }, 3000)
  } catch (err) {
    importing.value = false
    importError.value = err.message
  }
}

onMounted(async () => {
  await Promise.all([store.refreshOverview(), load(), loadInbox()])
})

onBeforeUnmount(() => pollTimer && clearInterval(pollTimer))
</script>

<style scoped>
.stat-row {
  margin-bottom: 12px;
}
.stat-card {
  text-align: center;
}
.stat-value {
  font-size: 28px;
  font-weight: 700;
}
.stat-label {
  font-size: 13px;
  color: #4a5262;
  margin: 2px 0;
}
.event-card {
  transition: transform 0.15s;
}
.event-card:hover {
  transform: translateY(-2px);
}
.event-title {
  font-size: 16px;
  font-weight: 600;
}
.stock-tag {
  cursor: pointer;
}
.conclusion {
  color: #4a5262;
  font-size: 13px;
  line-height: 1.7;
  margin: 10px 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.inbox-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 0;
  font-size: 13px;
}
.inbox-title {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
