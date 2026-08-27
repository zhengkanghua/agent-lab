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
