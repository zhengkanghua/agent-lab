import { reactive, readonly, ref } from 'vue'
import { fetchPreferences, savePreferences } from '@/api/preferences'
import { isAbortError } from '@/api/client'
import {
  DEFAULT_PREFERENCES,
  fromRemotePreferences,
  sanitizePreferences,
  toRemotePreferences,
  type UserPreferences,
} from '../model/preferences'

/**
 * 个人偏好的应用级单例 store，读写后端 `/auth/me/preferences`。
 *
 * 为什么是模块级单例而不是每次 new 一份：偏好与 authSession 一样是应用事实——检索页发
 * 请求时读它，设置页写它，两边必须看到同一份数据。每次调用造新实例会让「设置里改成 20 篇、
 * 检索还在发 10 篇」这类割裂成为可能。
 *
 * **读接口失败时用契约默认值，不阻塞页面、不弹错。** 偏好是自身操作默认值，读不到不影响
 * 任何数据的正确性；为它换掉整个功能（页面打不开）是不划算的。这条也是从 localStorage 迁移
 * 之后必须有的一支——以前本地读不出来就落回默认，现在这个兜底改由这里承担。
 *
 * **检索页仍是「改动即生效」。** store 里的 `preferences` 在加载完成后同步可读，检索页每次
 * 提交时现读（`SearchPage.vue` 注入 getter），不需要 await。加载中与加载失败时它就是默认值，
 * 检索照常发出——只是这一轮用的是默认数量参数，而不是把用户卡在等待上。
 */

/** 存储接口的一次读取状态，供设置页展示「正在读取 / 读取失败」。 */
const loadState = ref<'idle' | 'loading' | 'ready' | 'failed'>('idle')

const preferences = reactive<UserPreferences>({ ...DEFAULT_PREFERENCES })

/** 正在飞的那次加载，用来让并发的 load() 复用同一次请求而不是各发一条。 */
let pendingLoad: Promise<void> | null = null

/** 已经为哪个账号加载过。账号换了要重新读，否则会看到上一个账号的偏好。 */
let loadedFor: string | null = null

/**
 * 读取指定账号的偏好，同一个账号只读一次。
 *
 * **每次进页面都重读是错的**：检索页、对话页、设置页都会调它，逐页重读会让每个页面切换
 * 都多一次往返，而偏好在一段会话里几乎不变（变也是经由本 store 的 save）。
 * 传了不同的 `userId` 才重读——退出登录再换账号登录时必须读新的那份。
 */
async function load(userId?: string): Promise<void> {
  const identity = userId ?? null
  // 没给账号 id 时按「同一个身份」处理：调用方多半是不关心身份的组件，重读只会白跑一趟。
  if (pendingLoad) return pendingLoad
  if (loadState.value === 'ready' && (identity === null || identity === loadedFor)) return

  loadState.value = 'loading'
  loadedFor = identity
  pendingLoad = (async () => {
    try {
      const remote = await fetchPreferences({ notifyUnauthorized: false })
      // 用 Object.assign 而不是替换整个 reactive：替换会让别处已经拿到的引用指向旧对象。
      Object.assign(preferences, fromRemotePreferences(remote))
      loadState.value = 'ready'
    } catch (error) {
      // 取消（页面离开）不算失败，状态停在 idle 让下次进来重试。
      if (isAbortError(error)) {
        loadState.value = 'idle'
        return
      }
      Object.assign(preferences, sanitizePreferences(undefined))
      loadState.value = 'failed'
    } finally {
      pendingLoad = null
    }
  })()
  return pendingLoad
}

/**
 * 把一份偏好写到后端。
 *
 * 成功时用服务端回读的那份覆盖本地（而不是把入参直接当结果），这样「服务端归一化过的值」
 * 才是界面显示的值——比如提交了空白提示词，服务端存的是「未配置」，界面就该显示成空。
 * 失败时抛出，由调用方决定怎么提示；本地值不动，避免「界面显示保存成功、实际没存」。
 */
async function save(next: UserPreferences): Promise<void> {
  const stored = await savePreferences(toRemotePreferences(next), { notifyUnauthorized: false })
  Object.assign(preferences, fromRemotePreferences(stored))
  loadState.value = 'ready'
}

/** 测试用：把 store 恢复到初始状态，避免用例之间互相漏数据。 */
function resetForTests(): void {
  Object.assign(preferences, { ...DEFAULT_PREFERENCES })
  loadState.value = 'idle'
  pendingLoad = null
  loadedFor = null
}

export function usePreferences() {
  return {
    preferences,
    loadState: readonly(loadState),
    load,
    save,
    resetForTests,
  }
}
