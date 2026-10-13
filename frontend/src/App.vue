<template>
  <el-container direction="vertical">
    <el-header class="app-header">
      <div class="header-inner">
        <div class="brand" @click="$router.push('/')">
          <span class="logo">EventLens</span>
          <span class="subtitle">投资事件证据追踪台</span>
        </div>
        <el-menu
          mode="horizontal"
          :default-active="activeMenu"
          class="top-menu"
          :ellipsis="false"
          router
        >
          <el-menu-item index="/">事件总览</el-menu-item>
          <el-menu-item index="/hot">热点榜</el-menu-item>
          <el-menu-item index="/notifications">
            通知中心
            <el-badge
              v-if="unread"
              :value="unread"
              :max="99"
              class="menu-badge"
              type="danger"
            />
          </el-menu-item>
          <el-menu-item index="/watchlist">关注标的</el-menu-item>
        </el-menu>
      </div>
    </el-header>

    <el-main class="app-main">
      <router-view />
    </el-main>

    <el-footer class="app-footer">
      <p>
        数据来源：同花顺 iFinD（新闻/公告语义检索）· 扶摇（行情/财务）
        —— 全部为真实采集数据（data_origin=real），采集时段 2026-09-10 ~ 2026-10-13
      </p>
      <p>
        事件结论由 AI 基于证据链生成并通过引用回溯校验；传闻类信息均已标注。
        本产品为演示用途，不构成任何投资建议。
      </p>
    </el-footer>
  </el-container>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { store } from './store'

const route = useRoute()
const unread = computed(() => store.overview?.unread_notifications || 0)

const activeMenu = computed(() => {
  if (route.path.startsWith('/hot')) return '/hot'
  if (route.path.startsWith('/notifications')) return '/notifications'
  if (route.path.startsWith('/watchlist')) return '/watchlist'
  return '/'
})

onMounted(() => store.refreshOverview())
</script>

<style scoped>
.app-header {
  background: #fff;
  border-bottom: 1px solid #e8ebf0;
  padding: 0;
  height: 56px;
  position: sticky;
  top: 0;
  z-index: 100;
}
.header-inner {
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 24px;
  display: flex;
  align-items: center;
  gap: 32px;
  height: 100%;
}
.brand {
  display: flex;
  align-items: baseline;
  gap: 8px;
  cursor: pointer;
  white-space: nowrap;
}
.logo {
  font-size: 20px;
  font-weight: 700;
  color: #2f6bff;
  letter-spacing: 0.5px;
}
.subtitle {
  font-size: 13px;
  color: #8a919f;
}
.top-menu {
  flex: 1;
  border-bottom: none !important;
}
.menu-badge {
  margin-left: 6px;
  margin-top: -4px;
}
.app-main {
  padding: 0;
}
.app-footer {
  height: auto;
  background: #fafbfc;
  border-top: 1px solid #e8ebf0;
  padding: 14px 24px;
  text-align: center;
  color: #8a919f;
  font-size: 12px;
  line-height: 1.8;
}
.app-footer p {
  margin: 2px 0;
}
</style>
