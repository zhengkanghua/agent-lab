<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { Moon, Sun } from '@lucide/vue'

type Theme = 'light' | 'dark' | 'auto'

const theme = ref<Theme>('auto')
const resolvedTheme = ref<'light' | 'dark'>('light')

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
  <button
    type="button"
    class="theme-toggle"
    :aria-label="resolvedTheme === 'light' ? '切换到深色模式' : '切换到浅色模式'"
    @click="toggleTheme"
  >
    <Moon v-if="resolvedTheme === 'light'" :size="20" />
    <Sun v-else :size="20" />
  </button>
</template>

<style scoped>
.theme-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  padding: 0;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--surface-base);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out-smooth);
}

.theme-toggle:hover {
  background: var(--surface-hover);
  color: var(--text-primary);
  border-color: var(--border-strong);
}

.theme-toggle:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--accent-ring);
}

.theme-toggle:active {
  transform: scale(0.95);
}

@media (prefers-reduced-motion: reduce) {
  .theme-toggle {
    transition: color var(--duration-fast);
  }
  .theme-toggle:active {
    transform: none;
  }
}
</style>
