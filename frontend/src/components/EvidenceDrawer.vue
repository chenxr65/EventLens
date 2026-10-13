<template>
  <el-drawer v-model="visible" size="580px" :title="doc?.title || '原始材料'" destroy-on-close>
    <template v-if="doc">
      <div class="flex-gap mb12">
        <el-tag size="small" :type="doc.source_type === '公告' ? 'danger' : 'primary'" effect="plain">
          {{ doc.source_type }}
        </el-tag>
        <span class="meta">{{ doc.source_name }}<template v-if="doc.publisher"> · {{ doc.publisher }}</template></span>
        <a v-if="doc.source_url" :href="doc.source_url" target="_blank" rel="noopener" class="meta">
          原始链接 ↗
        </a>
      </div>

      <el-descriptions :column="1" size="small" border class="mb12">
        <el-descriptions-item label="披露时间">{{ doc.published_at || '未知' }}</el-descriptions-item>
        <el-descriptions-item label="抓取时间">{{ fmtTime(doc.first_fetched_at) }}</el-descriptions-item>
        <el-descriptions-item label="数据性质">
          <el-tag size="small" type="success" effect="plain">真实采集数据（data_origin=real）</el-tag>
        </el-descriptions-item>
        <el-descriptions-item v-if="doc.duplicate_of" label="重复关系">
          该材料与材料 #{{ doc.duplicate_of }} 为同一来源的重复片段
        </el-descriptions-item>
        <el-descriptions-item v-else-if="doc.source_cluster_id" label="同源组">
          {{ doc.source_cluster_id }}（疑似同源传播，独立性已降权）
        </el-descriptions-item>
      </el-descriptions>

      <div v-if="quote" class="mb12">
        <div class="card-title">引用片段（已通过原文回溯校验，下方高亮定位）</div>
        <div class="quote-block">{{ quote }}</div>
      </div>

      <div class="card-title">材料内容（系统保存的原文）</div>
      <div class="doc-content">
        <template v-for="(part, i) in parts" :key="i">
          <span :class="{ hl: part.hl }">{{ part.text }}</span>
        </template>
      </div>
    </template>
    <el-skeleton v-else :rows="8" animated />
  </el-drawer>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import { fmtTime } from '../format'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  documentId: { type: Number, default: null },
  quote: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})
const doc = ref(null)

watch(
  () => [props.modelValue, props.documentId],
  async ([open, id]) => {
    if (open && id) {
      doc.value = null
      try {
        doc.value = (await api.document(id)).document
      } catch (err) {
        console.error('材料加载失败', err)
      }
    }
  }
)

// 将原文按引用片段切分为三段，中段高亮（找不到引用时不加高亮）
const parts = computed(() => {
  const content = doc.value?.content || ''
  const q = props.quote || ''
  if (!q) return [{ text: content }]
  const idx = content.indexOf(q)
  if (idx < 0) return [{ text: content }]
  return [
    { text: content.slice(0, idx) },
    { text: content.slice(idx, idx + q.length), hl: true },
    { text: content.slice(idx + q.length) },
  ]
})
</script>
