<template>
  <div class="page">
    <h3 style="margin: 0 0 12px">
      关注标的（{{ items.length }}）
      <span class="meta">点击行查看关联事件</span>
    </h3>

    <el-card shadow="never" class="mb12">
      <div class="flex-gap">
        <el-input
          v-model="newCode"
          placeholder="代码，如 600519.SH"
          style="width: 200px"
          @keyup.enter="add"
        />
        <el-input v-model="newName" placeholder="名称（可选）" style="width: 160px" />
        <el-button type="primary" @click="add">添加关注</el-button>
        <span class="meta">关注标的后，相关事件更新会在通知中心提醒</span>
      </div>
    </el-card>

    <el-table
      :data="items"
      size="default"
      stripe
      :row-style="{ cursor: 'pointer' }"
      @row-click="goEvents"
    >
      <el-table-column prop="thscode" label="代码" width="120" />
      <el-table-column prop="stock_name" label="名称" width="130" />
      <el-table-column label="最新收盘" width="110">
        <template #default="{ row }">{{ row.snapshot?.close ?? '—' }}</template>
      </el-table-column>
      <el-table-column label="涨跌幅" width="120">
        <template #default="{ row }">
          <span
            v-if="row.snapshot"
            :class="(row.snapshot.change_pct ?? 0) >= 0 ? 'up' : 'down'"
          >
            {{ fmtPct(row.snapshot.change_pct) }}
          </span>
          <span v-else class="meta">停牌 / 无数据</span>
        </template>
      </el-table-column>
      <el-table-column label="关联事件" width="110">
        <template #default="{ row }">{{ row.events_count }} 个</template>
      </el-table-column>
      <el-table-column label="快照日期" width="120">
        <template #default="{ row }">
          <span class="meta">{{ row.snapshot?.trade_date ?? '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="added_at" label="加入时间" />
      <el-table-column label="操作" width="90">
        <template #default="{ row }">
          <el-button size="small" link type="danger" @click.stop="remove(row)">移除</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { store } from '../store'
import { fmtPct } from '../format'

const router = useRouter()
const items = ref([])
const newCode = ref('')
const newName = ref('')

function goEvents(row) {
  router.push(`/?thscode=${row.thscode}`)
}

async function load() {
  items.value = (await api.watchlist()).items
}

async function add() {
  if (!newCode.value.trim()) return
  try {
    await api.addWatch({ thscode: newCode.value.trim(), stock_name: newName.value.trim() || null })
    newCode.value = ''
    newName.value = ''
    await Promise.all([load(), store.refreshOverview()])
    ElMessage.success('已添加关注')
  } catch (err) {
    ElMessage.error(`添加失败：${err.message}`)
  }
}

async function remove(row) {
  await api.removeWatch(row.thscode)
  await Promise.all([load(), store.refreshOverview()])
}

onMounted(load)
</script>
