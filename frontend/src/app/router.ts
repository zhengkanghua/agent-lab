import { createRouter, createWebHistory } from 'vue-router'
import { authSession } from '../features/auth/auth-session'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('../pages/LoginPage.vue'),
    },
    {
      path: '/',
      name: 'search',
      component: () => import('../pages/SearchPage.vue'),
      meta: { requiresAuth: true },
    },
    {
      // 与后端一致地限超级用户：/agent/* 的路由级依赖是 current_superuser，
      // 非超级用户进来只会看到 403。前端提前挡住，避免让用户走到一个必然失败的页面。
      path: '/agent',
      name: 'agent-chat',
      component: () => import('../pages/AgentChatPage.vue'),
      meta: { requiresAuth: true, requiresSuperuser: true },
    },
    {
      /*
       * 带会话 id 的对话页。与 `agent-chat` 共用同一个组件，靠 `threadId` 参数区分
       * 「新对话」和「打开某个会话」。
       *
       * 单独一条路由而不是把 id 塞进 query：这个 URL 是可分享、可收藏、可刷新的——刷新后
       * 还在同一个会话里是基本预期。放 query 里也能做到，但那会让「有没有会话」看起来像个
       * 可选筛选条件，而它决定的是整页的内容。
       *
       * 不校验 id 格式：格式对不对由后端说，前端拦一道只是重复实现，而且拦错了会让合法
       * 链接打不开。非法 id 走的是「读历史失败」那条路径，界面上是一句可操作的说明。
       */
      path: '/agent/:threadId',
      name: 'agent-thread',
      component: () => import('../pages/AgentChatPage.vue'),
      meta: { requiresAuth: true, requiresSuperuser: true },
    },
    {
      path: '/admin/users',
      name: 'user-admin',
      component: () => import('../pages/UserAdminPage.vue'),
      meta: { requiresAuth: true, requiresSuperuser: true },
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: { name: 'search' },
    },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  await authSession.initialize()

  if (to.meta.requiresAuth && authSession.status.value !== 'authenticated') {
    return {
      name: 'login',
      query: { redirect: to.fullPath },
    }
  }

  if (to.meta.requiresSuperuser && !authSession.user.value?.is_superuser) {
    return { name: 'search' }
  }

  if (to.name === 'login' && authSession.status.value === 'authenticated') {
    return { name: 'search' }
  }

  return true
})

export default router
