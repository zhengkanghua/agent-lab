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
      // 登录即可用，与后端 /agent/* 的路由级依赖（current_active_user）一致。
      // 跨账号读对话由会话归属挡住，那道判断按账号、不按角色（见 ADR 0030）。
      path: '/agent',
      name: 'agent-chat',
      component: () => import('../pages/AgentChatPage.vue'),
      meta: { requiresAuth: true },
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
      meta: { requiresAuth: true },
    },
    {
      /*
       * 设置中心：账号安全、检索偏好、Agent 偏好三个分区由路径参数区分。
       * 分区放进路径而不是 query，理由与 /agent/:threadId 相同——这个地址可分享、
       * 可收藏、可刷新。参数不收窄成枚举：非法值由 SettingsPage 重定向到 account，
       * 前端再拦一道校验属于重复实现。
       *
       * 三个分区都对所有登录账号开放：Agent 对话已对所有登录账号可用，它的偏好
       * （自定义提示词）也就不再是超级用户专有（见 ADR 0030）。
       */
      path: '/settings/:section?',
      name: 'settings',
      component: () => import('../pages/SettingsPage.vue'),
      meta: { requiresAuth: true },
    },
    {
      // 旧地址整体并入设置中心：重定向让旧收藏与旧文档链接继续有效。
      path: '/account',
      redirect: { name: 'settings', params: { section: 'account' } },
    },
    {
      /*
       * 后台控制台：只有一条路由，板块由路径参数区分（users=账号管理、
       * scheduled-jobs=定时任务），与 /settings/:section? 同一取舍——地址可分享、
       * 可刷新、可收藏；旧的两条子路由地址原样有效，不必加重定向。分区注册表
       * 与顶栏标题在 pages/AdminPage.vue。参数不收窄成枚举：非法值由 AdminPage
       * 重定向回账号管理，前端再拦一道校验属于重复实现。
       * 权限守卫 requiresAuth + requiresSuperuser 挂在本路由上，新增分区自动继承。
       */
      path: '/admin/:section?',
      name: 'admin',
      component: () => import('../pages/AdminPage.vue'),
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
