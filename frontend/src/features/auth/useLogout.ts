import { ref, type Ref } from 'vue'
import { useRouter } from 'vue-router'
import { queryClient } from '@/app/query-client'
import { authSession } from './auth-session'

/* 退出登录的唯一实现。
 *
 * 收编前三页各写一份，四个动作（掐流、退登、清缓存、跳登录页）的顺序靠人肉记住。
 * 顺序不是随意的：
 *   1. beforeLogout —— 掐掉在途请求。留着它会在退出后继续读一条已无权限的连接。
 *   2. authSession.logout() —— 服务端销毁会话。
 *   3. afterLogout —— 清本地敏感状态。放在这里而不是第 1 步：退登失败意味着
 *      会话还在，用户可能就地重试，这时不该把他刚输的密码抹掉。
 *   4. queryClient.clear() —— 清掉缓存里属于上一个身份的数据。
 *   5. router.replace —— 用 replace 而非 push，避免后退回到已登出的页面。
 *
 * 两个钩子位置不同、含义不同，所以不合成一个：一个在退登前，一个在退登成功后。
 */

export interface UseLogoutOptions {
  /** 退登请求发出前执行。用于取消在途请求（Agent 页的流式对话）。 */
  beforeLogout?: () => void
  /** 退登成功后执行。用于清空本地敏感状态（账号页的密码输入框）。 */
  afterLogout?: () => void
}

export interface UseLogoutResult {
  loggingOut: Ref<boolean>
  /** 退登失败。三页都只渲染一句「退出失败」，所以是布尔而非错误对象。 */
  logoutError: Ref<boolean>
  logout: () => Promise<void>
}

export function useLogout(options: UseLogoutOptions = {}): UseLogoutResult {
  const router = useRouter()
  const loggingOut = ref(false)
  const logoutError = ref(false)

  async function logout(): Promise<void> {
    // 连点两次会发两次退登请求，第二次必然 401。
    if (loggingOut.value) return

    loggingOut.value = true
    logoutError.value = false
    try {
      options.beforeLogout?.()
      await authSession.logout()
      options.afterLogout?.()
      queryClient.clear()
      await router.replace({ name: 'login' })
    } catch {
      logoutError.value = true
    } finally {
      loggingOut.value = false
    }
  }

  return { loggingOut, logoutError, logout }
}
