import { createApp } from 'vue'
import { VueQueryPlugin } from '@tanstack/vue-query'
import router from './app/router'
import { queryClient } from './app/query-client'
import { setUnauthorizedHandler } from './api/client'
import { authSession } from '@/features/auth'
import './styles/tokens.css'
// style.css 先引入，由它的首行 @layer 语句确立层序；下面几个文件再并入 components 层。
import './style.css'
import './styles/components/topbar.css'
import './styles/components/motion.css'
import './styles/components/directory.css'
import './styles/components/chip.css'
import './styles/components/character-count.css'
import './styles/components/drawer.css'
import './styles/components/stream.css'
import './styles/components/form-controls.css'
import './styles/components/badge-link.css'
import './styles/components/nav-item.css'
import './styles/components/admin-page.css'
import './styles/components/text-link.css'
import './styles/components/popover.css'
import App from './App.vue'

/* 任一请求拿到 401 时的收尾：清会话、清缓存、送回登录页。
 *
 * 「登录已过期」这句话只在**真的把用户从某个页面上弹走**时才挂上（下面 if 里面）：
 * 已经站在登录页时的 401 是陌生访客的探测，那句提示对他来说是在描述一个不存在的会话。
 * 地址带 redirect，登录页会用它把用户送回原来的地方（见 LoginPage 的 safeRedirect）。 */
setUnauthorizedHandler(() => {
  const currentRoute = router.currentRoute.value
  authSession.expire()
  queryClient.clear()
  if (currentRoute.name !== 'login') {
    authSession.markExpiredMidSession()
    void router.replace({
      name: 'login',
      query: { redirect: currentRoute.fullPath },
    })
  }
})

createApp(App).use(router).use(VueQueryPlugin, { queryClient }).mount('#app')
