<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { Moon, Sun } from '@lucide/vue'
import BaseIconButton from './BaseIconButton.vue'

type Theme = 'light' | 'dark' | 'auto'

const theme = ref<Theme>('auto')
const resolvedTheme = ref<'light' | 'dark'>('light')

/* 移动端浏览器把 theme-color 画在地址栏/状态栏上，而它不跟随 data-theme：
   深色页面顶上会横着一条浅色栏。取值直接读语义 token，tokens.css 改色时这里不用跟着改。
   jsdom 下自定义属性取到空串，跳过即可（测试环境不关心这条 meta）。 */
function syncThemeColor(): void {
  const meta = document.querySelector('meta[name="theme-color"]')
  if (!meta) return
  const surface = getComputedStyle(document.documentElement)
    .getPropertyValue('--surface-base')
    .trim()
  if (surface) meta.setAttribute('content', surface)
}

function applyTheme(t: Theme) {
  // data-theme 永远落「解析后的结果值」：'auto' 在这里解析成 light/dark 再写进 DOM，
  // 而不是删属性交给 CSS 媒体查询。这样 tokens.css 的深色覆盖只需要 [data-theme='dark']
  // 一份，不用再维护 prefers-color-scheme 的重复块；index.html 的防闪烁脚本用同一套规则。
  if (t === 'auto') {
    // jsdom 不支持 matchMedia，测试环境默认 light
    const systemPrefersDark =
      typeof window.matchMedia === 'function'
        ? window.matchMedia('(prefers-color-scheme: dark)').matches
        : false
    const resolved = systemPrefersDark ? 'dark' : 'light'
    document.documentElement.dataset.theme = resolved
    resolvedTheme.value = resolved
  } else {
    document.documentElement.dataset.theme = t
    resolvedTheme.value = t
  }
  syncThemeColor()
}

function toggleTheme() {
  const next: Theme = resolvedTheme.value === 'light' ? 'dark' : 'light'
  theme.value = next
  localStorage.setItem('theme', next)
  applyTheme(next)
}

onMounted(() => {
  const saved = localStorage.getItem('theme') as Theme | null
  if (saved && ['light', 'dark', 'auto'].includes(saved)) {
    theme.value = saved
  }
  applyTheme(theme.value)

  // 监听系统偏好变化（测试环境跳过）
  if (typeof window.matchMedia === 'function') {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    mediaQuery.addEventListener('change', () => {
      if (theme.value === 'auto') {
        applyTheme('auto')
      }
    })
  }
})

watch(theme, (newTheme) => {
  applyTheme(newTheme)
})
</script>

<template>
  <!-- 图标键的外观（40px 方框、悬停、按下、焦点环、触屏撑高，以及 title）全归
       shared/ui/BaseIconButton.vue。这里原来自己写了一份：那份皮肤与 BaseIconButton 逐项重复，
       而且它带描边+底色，顶栏另外两枚（账号、退出）不带——同一排图标里三种观感。
       现在三枚同款，都是「静止无框、悬停浮出描边与底」。 -->
  <BaseIconButton
    :label="resolvedTheme === 'light' ? '切换到深色模式' : '切换到浅色模式'"
    @click="toggleTheme"
  >
    <Moon v-if="resolvedTheme === 'light'" :size="19" aria-hidden="true" />
    <Sun v-else :size="19" aria-hidden="true" />
  </BaseIconButton>
</template>
