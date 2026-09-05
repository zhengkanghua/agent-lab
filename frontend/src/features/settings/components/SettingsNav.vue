<script setup lang="ts">
import type { Component } from 'vue'
import { Bot, CalendarClock, SlidersHorizontal, UserRound, Users } from '@lucide/vue'
import { RouterLink } from 'vue-router'

export type SettingsSection = 'account' | 'search' | 'agent'

const props = defineProps<{
  /** 当前激活的分区，由路由参数决定。 */
  section: SettingsSection
  isSuperuser: boolean
}>()

/**
 * 设置中心的分区导航。
 *
 * 桌面端是左侧竖排列表（商业产品的设置页标准形态：导航常驻、内容随分区切换），
 * 窄屏收成顶部横向滚动条——设置分区只有三五个，横向一排放得下，不值得为它开抽屉。
 *
 * 超管另有一组「后台管理」直达链接：原来藏在账号页一张卡片里，现在跟着设置导航走，
 * 「我自己」到「管别人」的路径仍在一页之内（ADR 0011 的接续要求）。
 */

interface SectionItem {
  key: SettingsSection
  label: string
  description: string
  icon: Component
  superuserOnly?: boolean
}

const sections: SectionItem[] = [
  { key: 'account', label: '账号安全', description: '登录信息与密码', icon: UserRound },
  { key: 'search', label: '检索偏好', description: '每次检索的数量参数', icon: SlidersHorizontal },
  {
    key: 'agent',
    label: 'Agent 偏好',
    description: '自定义系统提示词',
    icon: Bot,
    superuserOnly: true,
  },
]

const visibleSections = sections.filter((item) => !item.superuserOnly || props.isSuperuser)

const adminLinks = [
  { label: '账号管理', to: { name: 'user-admin' }, icon: Users },
  { label: '定时任务', to: { name: 'scheduled-jobs' }, icon: CalendarClock },
]
</script>

<template>
  <nav class="settings-nav" aria-label="设置分区">
    <ul class="section-list">
      <li v-for="item in visibleSections" :key="item.key">
        <RouterLink
          class="section-link"
          :class="{ 'is-active': section === item.key }"
          :to="{ name: 'settings', params: { section: item.key } }"
          :aria-current="section === item.key ? 'page' : undefined"
        >
          <component :is="item.icon" class="section-icon" :size="17" aria-hidden="true" />
          <span class="section-copy">
            <span class="section-label">{{ item.label }}</span>
            <span class="section-description">{{ item.description }}</span>
          </span>
        </RouterLink>
      </li>
    </ul>

    <template v-if="isSuperuser">
      <div class="admin-divider" role="presentation"></div>
      <p class="admin-heading">后台管理</p>
      <ul class="section-list">
        <li v-for="link in adminLinks" :key="link.label">
          <RouterLink class="section-link is-tertiary" :to="link.to">
            <component :is="link.icon" class="section-icon" :size="17" aria-hidden="true" />
            <span class="section-copy">
              <span class="section-label">{{ link.label }}</span>
            </span>
          </RouterLink>
        </li>
      </ul>
    </template>
  </nav>
</template>

<style scoped>
.section-list {
  display: grid;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.section-link {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 10px 12px;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  text-decoration: none;
  transition:
    border-color var(--duration-fast) var(--ease-out-smooth),
    color var(--duration-fast) var(--ease-out-smooth),
    background-color var(--duration-fast) var(--ease-out-smooth);
}

.section-link:hover {
  color: var(--text-primary);
  background: var(--surface-hover);
}

.section-link.is-active {
  border-color: var(--border-subtle);
  color: var(--text-primary);
  background: var(--surface-raised);
}

.section-icon {
  flex: 0 0 auto;
  color: var(--text-tertiary);
  transition: color var(--duration-fast) var(--ease-out-smooth);
}

.section-link:hover .section-icon,
.section-link.is-active .section-icon {
  color: var(--accent);
}

.section-copy {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.section-label {
  font-size: 0.86rem;
  font-weight: 720;
}

.section-description {
  margin-top: 1px;
  color: var(--text-tertiary);
  font-size: 0.72rem;
}

.admin-divider {
  margin: 14px 0 10px;
  border-top: 1px solid var(--border-subtle);
}

.admin-heading {
  margin: 0 0 6px;
  padding: 0 12px;
  color: var(--text-tertiary);
  font-size: 0.68rem;
  font-weight: 720;
  letter-spacing: 0.08em;
}

/* 窄屏：横向滚动条。描述文字撤掉，只留图标 + 名称。 */
@media (max-width: 720px) {
  .settings-nav {
    overflow-x: auto;
  }

  .section-list {
    grid-auto-flow: column;
    justify-content: start;
    gap: 6px;
  }

  .section-link {
    padding: 8px 12px;
  }

  .section-copy {
    flex-direction: row;
    align-items: center;
  }

  .section-description,
  .admin-divider,
  .admin-heading {
    display: none;
  }
}
</style>
