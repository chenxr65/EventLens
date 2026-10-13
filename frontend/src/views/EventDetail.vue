<template>
  <div v-if="data" class="page">
    <!-- 头部：状态 + 标的 -->
    <el-card shadow="never" class="mb12">
      <div class="flex-between">
        <div class="flex-gap">
          <el-button link @click="$router.push('/')">← 返回总览</el-button>
          <span class="detail-title">{{ ev.title }}</span>
          <el-tag :type="statusOf(ev.current_status).type" effect="dark">
            {{ statusOf(ev.current_status).label }}
          </el-tag>
        </div>
        <div class="flex-gap">
          <span class="meta">最后实质更新：{{ ev.last_substantive_update_at }}</span>
          <el-button size="small" :type="ev.tracked ? 'default' : 'primary'" @click="toggleTrack">
            {{ ev.tracked ? '停止追踪' : '重新追踪' }}
          </el-button>
        </div>
      </div>
      <div class="flex-gap mt8">
        <el-tag v-for="r in data.relations" :key="r.thscode" effect="plain">
          {{ r.stock_name }} {{ r.thscode }}
          <span v-if="r.market" :class="(r.market.change_pct ?? 0) >= 0 ? 'up' : 'down'">
            {{ r.market.close }}（{{ fmtPct(r.market.change_pct) }}）
          </span>
        </el-tag>
        <el-tag v-if="ev.event_type" type="info" effect="plain">{{ ev.event_type }}</el-tag>
      </div>
    </el-card>

    <el-row :gutter="12">
      <!-- 左栏：当前判断 + 证据摘要 -->
      <el-col :span="14">
        <el-card shadow="never" class="mb12">
          <div class="card-title">
            当前判断
            <el-tag size="small" type="info" effect="plain">AI 生成 · 附完整证据链</el-tag>
          </div>
          <el-alert
            type="warning"
            :closable="false"
            class="mb12"
            show-icon
            title="本页结论由 AI 基于下方证据链生成并通过引用回溯校验；传闻类信息已明确标注来源性质。内容仅供研究参考，不构成投资建议。"
          />
          <div v-if="conclusionParts.confirmed" class="conclusion-block confirmed">
            <div class="conclusion-head">已确认部分</div>
            <p class="conclusion-full">{{ conclusionParts.confirmed }}</p>
          </div>
          <div v-if="conclusionParts.uncertain" class="conclusion-block uncertain">
            <div class="conclusion-head">仍不确定部分</div>
            <p class="conclusion-full">{{ conclusionParts.uncertain }}</p>
          </div>
          <template v-if="uncertaintyList.length">
            <div class="card-title mt12">待确认问题</div>
            <el-alert
              v-for="(u, i) in uncertaintyList"
              :key="i"
              :title="u"
              type="warning"
              :closable="false"
              class="mb8"
            />
          </template>
        </el-card>

        <el-card shadow="never" class="mb12">
          <div class="card-title">
            证据摘要
            <span class="meta">
              {{ data.claims.length }} 条主张 · {{ totalEvidence }} 条证据 · 展开可逐条溯源
            </span>
          </div>

          <el-card
            v-for="c in data.claims"
            :key="c.id"
            shadow="never"
            class="claim-card"
            :style="{ borderLeftColor: CLAIM_TYPE[c.claim_type]?.color || '#dcdfe6' }"
          >
            <div class="flex-gap">
              <el-tag :type="CLAIM_TYPE[c.claim_type]?.type" size="small">
                {{ CLAIM_TYPE[c.claim_type]?.label }}
              </el-tag>
              <el-tag :type="EVIDENCE_STATUS[c.evidence_status]?.type" size="small" effect="plain">
                {{ EVIDENCE_STATUS[c.evidence_status]?.label }}
              </el-tag>
              <span class="meta">
                首次出现 {{ c.first_seen_at || '未知' }} · 证据 {{ c.evidences.length }} 条
              </span>
            </div>
            <p class="claim-text">{{ c.text }}</p>

            <el-collapse v-if="c.evidences.length">
              <el-collapse-item :title="`查看证据明细（${c.evidences.length}）`">
                <div v-for="l in c.evidences" :key="l.id" class="evidence-item">
                  <div class="flex-between">
                    <div class="flex-gap">
                      <el-tag :type="RELATION[l.relation]?.type" size="small" effect="dark">
                        {{ RELATION[l.relation]?.label }}
                      </el-tag>
                      <span class="meta">权重 W={{ l.weight?.toFixed(2) }}</span>
                      <el-tag
                        size="small"
                        :type="l.weight_detail?.quote_verified ? 'success' : 'danger'"
                        effect="plain"
                      >
                        {{ l.weight_detail?.quote_verified ? '引用校验通过' : '引用未通过' }}
                      </el-tag>
                    </div>
                    <span class="meta">{{ l.document.source_type }} · {{ l.document.published_at }}</span>
                  </div>

                  <div class="quote-block mt8">{{ l.quote }}</div>

                  <div class="flex-between mt8">
                    <span class="meta">{{ l.document.title }}</span>
                    <el-button size="small" link type="primary" @click="openDoc(l)">
                      查看原文 ↗
                    </el-button>
                  </div>

                  <el-collapse v-if="l.weight_detail">
                    <el-collapse-item title="权重评分明细（启发式五项评分，非真实性概率）">
                      <div class="weight-rows">
                        <div v-for="dim in WEIGHT_DIMENSIONS" :key="dim.key" class="weight-row">
                          <span class="dim-label">{{ dim.label }}</span>
                          <el-progress
                            :percentage="Math.round((l.weight_detail[dim.key] || 0) * 100)"
                            :stroke-width="8"
                            :show-text="false"
                            style="width: 180px"
                          />
                          <span class="meta">{{ ((l.weight_detail[dim.key] || 0) * 100).toFixed(0) }}</span>
                        </div>
                        <div class="meta">材料角色：{{ l.weight_detail.role }}</div>
                      </div>
                    </el-collapse-item>
                  </el-collapse>
                </div>
              </el-collapse-item>
            </el-collapse>
          </el-card>
        </el-card>
      </el-col>

      <!-- 右栏：时间线 + 标的与影响 + 原始材料 -->
      <el-col :span="10">
        <el-card shadow="never" class="mb12">
          <div class="card-title">事件时间线（版本演化）</div>
          <el-timeline>
            <el-timeline-item
              v-for="v in data.versions"
              :key="v.id"
              :timestamp="(v.created_at || '').slice(0, 10)"
              placement="top"
              :type="CHANGE_TYPE[v.change_type]?.type || 'primary'"
            >
              <div class="flex-gap">
                <b>V{{ v.version_no }}</b>
                <el-tag size="small" :type="CHANGE_TYPE[v.change_type]?.type">
                  {{ CHANGE_TYPE[v.change_type]?.label || v.change_type }}
                </el-tag>
                <el-tag size="small" effect="plain" :type="statusOf(v.status_after).type">
                  {{ v.status_before ? statusOf(v.status_before).label + ' → ' : '' }}{{ statusOf(v.status_after).label }}
                </el-tag>
              </div>
              <p class="version-reason">{{ v.reason }}</p>
              <div class="flex-gap">
                <el-tag
                  v-for="d in v.trigger_documents"
                  :key="d.id"
                  size="small"
                  effect="plain"
                  class="cursor-pointer"
                  @click="openDocById(d.id, '')"
                >
                  材料#{{ d.id }} {{ d.published_at }}
                </el-tag>
              </div>
            </el-timeline-item>
          </el-timeline>
        </el-card>

        <el-card shadow="never" class="mb12">
          <div class="card-title">标的关联与影响验证</div>
          <div v-for="r in data.relations" :key="r.thscode" class="mb12">
            <div class="flex-gap mb12">
              <b>{{ r.stock_name }}（{{ r.thscode }}）</b>
              <el-tag size="small" effect="plain">{{ r.relationship_type }}</el-tag>
              <el-tag
                size="small"
                :type="r.relevance_level === 'high' ? 'danger' : 'info'"
                effect="plain"
              >
                相关度 {{ r.relevance_level }}
              </el-tag>
            </div>
            <p class="meta impact-hypo">{{ r.impact_hypothesis }}</p>
            <div v-if="marketStats(r.thscode)" class="stat-chips mb12">
              <el-tag size="small" effect="plain">
                事件以来（自 {{ marketStats(r.thscode).since_v1.date }}）
              </el-tag>
              <span
                class="stat-num"
                :class="(marketStats(r.thscode).since_v1.return_pct ?? 0) >= 0 ? 'up' : 'down'"
              >
                {{ fmtPct(marketStats(r.thscode).since_v1.return_pct) }}
              </span>
              <span class="meta">
                vs {{ market.value?.benchmark?.name }}
                {{ fmtPct(marketStats(r.thscode).since_v1.benchmark_return_pct) }}
              </span>
              <el-tag
                size="small"
                :type="excessType(marketStats(r.thscode).since_v1.excess_pct)"
                effect="dark"
              >
                超额 {{ fmtPct(marketStats(r.thscode).since_v1.excess_pct) }}
              </el-tag>
              <el-divider direction="vertical" />
              <el-tag size="small" effect="plain">
                最新版本以来（自 {{ marketStats(r.thscode).since_latest_version.date }}）
              </el-tag>
              <span
                class="stat-num"
                :class="(marketStats(r.thscode).since_latest_version.return_pct ?? 0) >= 0 ? 'up' : 'down'"
              >
                {{ fmtPct(marketStats(r.thscode).since_latest_version.return_pct) }}
              </span>
              <el-tag
                size="small"
                :type="excessType(marketStats(r.thscode).since_latest_version.excess_pct)"
                effect="dark"
              >
                超额 {{ fmtPct(marketStats(r.thscode).since_latest_version.excess_pct) }}
              </el-tag>
            </div>
            <MarketChart
              v-if="marketFor(r.thscode)"
              :bars="marketFor(r.thscode).bars"
              :markers="marketMarkers"
              :title="`${r.stock_name} 日K · 标注为事件版本形成日`"
            />
          </div>
          <el-alert
            type="info"
            :closable="false"
            title="行情为影响验证的背景数据，仅呈现相关性线索，不代表因果推断或投资建议。"
          />
        </el-card>

        <el-card shadow="never" class="mb12">
          <div class="card-title">
            原始材料（{{ data.documents.length }} 条）
            <span class="meta">已由 AI 语义过滤 {{ data.noise_count }} 条无关材料</span>
          </div>
          <el-table
            :data="data.documents"
            size="small"
            max-height="430"
            class="cursor-pointer"
            @row-click="(row) => openDocById(row.id, '')"
          >
            <el-table-column prop="published_at" label="披露" width="92" />
            <el-table-column label="类型" width="62">
              <template #default="{ row }">
                <el-tag
                  size="small"
                  :type="row.source_type === '公告' ? 'danger' : 'primary'"
                  effect="plain"
                >
                  {{ row.source_type }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="title" label="标题" show-overflow-tooltip />
            <el-table-column label="标注" width="86">
              <template #default="{ row }">
                <el-tag v-if="row.duplicate_of" size="small" type="warning" effect="plain">
                  重复片段
                </el-tag>
                <el-tag v-else-if="row.source_cluster_id" size="small" type="info" effect="plain">
                  同源组
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <EvidenceDrawer v-model="drawerOpen" :document-id="drawerDocId" :quote="drawerQuote" />
  </div>

  <div v-else class="page">
    <el-skeleton :rows="12" animated />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { store } from '../store'
import {
  CHANGE_TYPE,
  CLAIM_TYPE,
  EVIDENCE_STATUS,
  RELATION,
  WEIGHT_DIMENSIONS,
} from '../constants'
import { fmtPct, statusOf } from '../format'
import EvidenceDrawer from '../components/EvidenceDrawer.vue'
import MarketChart from '../components/MarketChart.vue'

const props = defineProps({ id: { type: String, required: true } })

const data = ref(null)
const market = ref(null)

// 溯源抽屉
const drawerOpen = ref(false)
const drawerDocId = ref(null)
const drawerQuote = ref('')

const ev = computed(() => data.value?.event || {})
const uncertaintyList = computed(() =>
  (ev.value.uncertainty || '').split('\n').filter((s) => s && s.trim())
)
// 结论结构化拆分："已确认部分：…仍不确定部分：…" → 两段展示（按首个分隔点切片，兼容多次出现）
const conclusionParts = computed(() => {
  const text = ev.value.conclusion || ''
  const match = text.match(/仍不确定(部分)?[:：]/)
  if (match) {
    const idx = match.index
    return {
      confirmed: text.slice(0, idx).replace(/^已确认(部分)?[:：]/, '').trim(),
      uncertain: text.slice(idx + match[0].length).trim(),
    }
  }
  return { confirmed: text.trim(), uncertain: '' }
})
const totalEvidence = computed(() =>
  (data.value?.claims || []).reduce((acc, c) => acc + c.evidences.length, 0)
)
const marketMarkers = computed(() => market.value?.markers || [])

function marketFor(thscode) {
  return (market.value?.series || []).find((s) => s.thscode === thscode)
}

function marketStats(thscode) {
  return market.value?.stats?.[thscode] || null
}

function excessType(value) {
  if (value === null || value === undefined) return 'info'
  return value >= 0 ? 'danger' : 'success'
}

function openDoc(link) {
  drawerDocId.value = link.document_id
  drawerQuote.value = link.quote || ''
  drawerOpen.value = true
}

function openDocById(id, quote) {
  drawerDocId.value = id
  drawerQuote.value = quote
  drawerOpen.value = true
}

async function toggleTrack() {
  const current = ev.value
  if (current.tracked) {
    try {
      await ElMessageBox.confirm(
        '停止追踪该事件？历史数据与版本将完整保留，可在事件总览的"显示已停止追踪"中恢复。',
        '停止追踪',
        { type: 'warning', confirmButtonText: '停止追踪', cancelButtonText: '取消' }
      )
    } catch {
      return
    }
    await api.untrackEvent(props.id)
    ElMessage.success('已停止追踪（历史保留，可恢复）')
  } else {
    await api.trackEvent(props.id)
    ElMessage.success('已重新追踪')
  }
  data.value = await api.event(props.id)
  store.refreshOverview()
}

onMounted(async () => {
  const [detail, marketData] = await Promise.all([
    api.event(props.id),
    api.eventMarket(props.id),
  ])
  data.value = detail
  market.value = marketData
})
</script>

<style scoped>
.detail-title {
  font-size: 18px;
  font-weight: 700;
}
.conclusion-full {
  color: #333;
  font-size: 13.5px;
  line-height: 1.85;
  margin: 0;
  white-space: pre-wrap;
}
.claim-text {
  font-size: 14px;
  font-weight: 600;
  margin: 10px 0;
  line-height: 1.6;
}
.version-reason {
  color: #4a5262;
  font-size: 13px;
  line-height: 1.7;
  margin: 8px 0;
}
.weight-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.dim-label {
  width: 64px;
  font-size: 12px;
  color: #4a5262;
}
.impact-hypo {
  line-height: 1.7;
  margin: 0 0 8px;
}
.conclusion-block {
  border-radius: 6px;
  padding: 10px 14px;
  margin-bottom: 10px;
}
.conclusion-block.confirmed {
  background: #f0f9f4;
  border-left: 3px solid #1a9e5c;
}
.conclusion-block.uncertain {
  background: #fff8f0;
  border-left: 3px solid #e67e22;
}
.conclusion-head {
  font-size: 13px;
  font-weight: 700;
  margin-bottom: 6px;
  color: #4a5262;
}
.stat-chips {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.stat-num {
  font-weight: 700;
  font-size: 14px;
}
</style>
