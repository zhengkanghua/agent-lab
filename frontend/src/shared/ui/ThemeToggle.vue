<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { Moon, Sun } from '@lucide/vue'

type Theme = 'light' | 'dark' | 'auto'

const theme = ref<Theme>('auto')
const resolvedTheme = ref<'light' | 'dark'>('light')

function applyTheme(t: Theme) {
  if (t === 'auto') {
    delete document.documentElement.dataset.theme
    // jsdom 不支持 matchMedia，测试环境默认 light
    const systemPrefersDark =
      typeof window.matchMedia === 'function'
        ? window.matchMedia('(prefers-color-scheme: dark)').matches
        : false
    resolvedTheme.value = systemPrefersDark ? 'dark' : 'light'
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
