<template>
  <div class="page">
    <div class="flex-between mb12">
      <h3 style="margin: 0">
        通知中心
        <span class="meta">共 {{ items.length }} 条 · 未读 {{ unreadCount }}</span>
      </h3>
      <el-button size="small" :disabled="!unreadCount" @click="readAll">全部标为已读</el-button>
    </div>

    <el-card
      v-for="n in items"
      :key="n.id"
      shadow="never"
      class="mb12 notif-card"
      :class="{ unread: !n.read }"
    >
      <div class="flex-between">
        <div class="flex-gap">
          <el-tag
            size="small"
            :type="n.severity === 'important' ? 'danger' : 'info'"
            effect="dark"
          >
            {{ n.severity === 'important' ? '重要变化' : '一般更新' }}
          </el-tag>
          <el-tag size="small" effect="plain">{{ CHANGE_TYPE[n.type]?.label || n.type }}</el-tag>
          <span class="meta">{{ n.diff_summary }}</span>
        </div>
        <span class="meta">{{ fmtTime(n.created_at) }}</span>
      </div>

      <p class="notif-title cursor-pointer" @click="$router.push(`/events/${n.event_id}`)">
        {{ n.event_title }} →
      </p>
      <p class="notif-body">{{ n.body }}</p>

      <div class="flex-between">
        <el-button size="small" link type="primary" @click="$router.push(`/events/${n.event_id}`)">
          查看事件与版本差异
        </el-button>
        <el-button v-if="!n.read" size="small" link @click="markRead(n)">标为已读</el-button>
      </div>
    </el-card>

    <el-empty v-if="!items.length" description="暂无通知" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { store } from '../store'
import { CHANGE_TYPE } from '../constants'
import { fmtTime } from '../format'

const items = ref([])
const unreadCount = computed(() => items.value.filter((n) => !n.read).length)

async function load() {
  items.value = (await api.notifications()).items
}

async function markRead(n) {
  await api.markRead(n.id)
  await Promise.all([load(), store.refreshOverview()])
}

async function readAll() {
  await api.readAll()
  await Promise.all([load(), store.refreshOverview()])
}

onMounted(load)
</script>

<style scoped>
.notif-card.unread {
  border-left: 3px solid #d03050;
}
.notif-title {
  font-size: 15px;
  font-weight: 600;
  margin: 10px 0 6px;
  color: #2f6bff;
}
.notif-body {
  color: #4a5262;
  font-size: 13px;
  line-height: 1.7;
  margin: 0 0 10px;
}
</style>
