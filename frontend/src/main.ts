import { createApp } from 'vue'
import { VueQueryPlugin } from '@tanstack/vue-query'
import router from './app/router'
import { queryClient } from './app/query-client'
import { setUnauthorizedHandler } from './api/client'
import { authSession } from './features/auth/auth-session'
import './styles/tokens.css'
// style.css 先引入，由它的首行 @layer 语句确立层序；下面三个文件再并入 components 层。
import './style.css'
import './styles/components/topbar.css'
import './styles/components/motion.css'
import App from './App.vue'

setUnauthorizedHandler(() => {
  const currentRoute = router.currentRoute.value
  authSession.expire()
  queryClient.clear()
  if (currentRoute.name !== 'login') {
    void router.replace({
      name: 'login',
      query: { redirect: currentRoute.fullPath },
    })
  }
})

createApp(App).use(router).use(VueQueryPlugin, { queryClient }).mount('#app')
