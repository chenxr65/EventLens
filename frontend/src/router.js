import { createRouter, createWebHashHistory } from 'vue-router'

import EventList from './views/EventList.vue'
import EventDetail from './views/EventDetail.vue'
import HotTopics from './views/HotTopics.vue'
import Notifications from './views/Notifications.vue'
import Watchlist from './views/Watchlist.vue'

// hash 路由：构建产物可被任意静态服务直接托管（无需服务端 fallback）
export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'events', component: EventList },
    { path: '/hot', name: 'hot', component: HotTopics },
    { path: '/events/:id', name: 'event-detail', component: EventDetail, props: true },
    { path: '/notifications', name: 'notifications', component: Notifications },
    { path: '/watchlist', name: 'watchlist', component: Watchlist },
  ],
})
