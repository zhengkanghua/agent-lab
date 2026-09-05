import { reactive, watch } from 'vue'
import {
  DEFAULT_PREFERENCES,
  PREFERENCES_STORAGE_KEY,
  sanitizePreferences,
  type UserPreferences,
} from '../model/preferences'

/**
 * 用户偏好的应用级单例 store，读写本浏览器的 localStorage。
 *
 * 为什么是模块级单例而不是每次 new 一份：偏好与 authSession 一样是应用事实——检索页发
 * 请求时读它，设置页写它，两边必须看到同一份数据。每次调用造新实例会让「设置里改成 20 篇、
 * 检索还在发 10 篇」这类割裂成为可能。
 *
 * 持久化是写入即存（watch 同步触发），不做防抖：单条偏好就是几个数字或一段 4KB 上限的
 * 文本，localStorage 的写开销可以忽略；防抖反而引入「关页面前最后一笔没落盘」的窗口。
 * 读盘失败（隐私模式、JSON 损坏）静默落回默认值——偏好丢了可以重设，页面不能打不开。
 */

function loadFromStorage(): UserPreferences {
  if (typeof localStorage === 'undefined') {
    return sanitizePreferences(undefined)
  }
  try {
    const raw = localStorage.getItem(PREFERENCES_STORAGE_KEY)
    return sanitizePreferences(raw === null ? undefined : JSON.parse(raw))
  } catch {
    return sanitizePreferences(undefined)
  }
}

const preferences = reactive<UserPreferences>(loadFromStorage())

if (typeof localStorage !== 'undefined') {
  watch(
    preferences,
    (value) => {
      try {
        // spread 把 reactive 代理还原成普通对象再序列化。
        localStorage.setItem(PREFERENCES_STORAGE_KEY, JSON.stringify({ ...value }))
      } catch {
        // 写入失败（隐私模式配额等）不打扰用户：本次会话内偏好仍然生效。
      }
    },
    // 嵌套对象当前没有，但开 deep 是给未来字段留的保险：漏了 deep 的 watch 只盯引用替换，
    // 字段级修改会静默不落盘。flush 用 sync 是写穿语义——不依赖调度器时机，
    // 「关页面前最后一笔没落盘」的窗口不存在。
    { deep: true, flush: 'sync' },
  )
}

/** 数量参数恢复默认。提示词不跟进：清空提示词是设置页里一个显式动作，不搭车。 */
function resetSearchPreferences(): void {
  preferences.documentLimit = DEFAULT_PREFERENCES.documentLimit
  preferences.matchesPerDocument = DEFAULT_PREFERENCES.matchesPerDocument
}

export function usePreferences() {
  return { preferences, resetSearchPreferences }
}
