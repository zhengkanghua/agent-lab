<script setup lang="ts">
import { type Component } from 'vue'
import { Bot, Gauge, SlidersHorizontal, UserRound } from '@lucide/vue'
import { RouterLink } from 'vue-router'

export type SettingsSection = 'account' | 'search' | 'agent' | 'usage'

defineProps<{
  /** 当前激活的分区，由路由参数决定。 */
  section: SettingsSection
}>()

/**
 * 设置中心的分区导航。
 *
 * 桌面端是左侧竖排列表（商业产品的设置页标准形态：导航常驻、内容随分区切换），
 * 窄屏收成顶部横向滚动条——设置分区只有三五个，横向一排放得下，不值得为它开抽屉。
 *
 * 这里刻意不放「后台管理」直达链接：后台的唯一入口是顶栏那枚仅超管可见的入口图标
 * （见 AppShell）。曾经并排放过「账号管理」「定时任务」两条，但只覆盖五个后台分区里的
 * 两个——知道地址的人用不上它，不知道的人被它误导以为后台只有这两块。入口收成一个，
 * 「去后台」就只有一个答案。
 */

interface SectionItem {
  key: SettingsSection
  label: string
  description: string
  icon: Component
}

// 四个分区都对所有登录账号可见：Agent 对话本身已对所有登录账号开放，
// 它的偏好（自定义提示词）就不再是超级用户专有（见 ADR 0030）；
// 用量分区只展示当前账号自己的调用记录，也不需要额外角色。
const sections: SectionItem[] = [
  { key: 'account', label: '账号安全', description: '登录信息与密码', icon: UserRound },
  { key: 'search', label: '检索偏好', description: '每次检索的数量参数', icon: SlidersHorizontal },
  { key: 'agent', label: 'Agent 偏好', description: '自定义系统提示词', icon: Bot },
  { key: 'usage', label: '用量', description: '模型调用与 token 消耗', icon: Gauge },
]
</script>

<template>
  <nav class="settings-nav" aria-label="设置分区">
    <ul class="section-list">
      <li v-for="item in sections" :key="item.key">
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
  </nav>
</template>

<style scoped>
.settings-nav {
  container-type: inline-size;
}

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

/* 当前分区：用与前台/后台侧栏同一套「当前导航态」（浅强调底 + 强调色字）。
   原来这里是 `background: --surface-raised`，而设置面板本身就是 --surface-raised
   （SettingsPage 的 .settings-panel），实测两者完全同色（浅色都是 #ffffff，
   深色都是 #1f1f1d）——选中项只剩 1px 描边，而且比「悬停」还弱：
   悬停用的是 --surface-hover，看得见；选中的反而看不见。
   与账号目录行内操作那次是同一种反相，2026-10 一并修正。 */
.section-link.is-active {
  color: var(--accent);
  background: var(--accent-soft);
}

/* 按下：两类项一起收深一档。写在 .is-active 之后，同特异度时这一条胜出，
   于是「当前分区被按下」也有回执，不会成为这个导航里唯一没反应的一项。 */
.section-link:active {
  background: var(--surface-sunken-hover);
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
  font-size: var(--fs-sm);
  font-weight: var(--fw-bold);
}

.section-description {
  margin-top: 1px;
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
}

/* 浮层里的左导航只有 160px：描述文字放不下，收成图标 + 名称（容器查询，
   由所在容器宽度决定，与视口断点解耦）。 */
@container (max-width: 190px) {
  .section-link {
    padding: 9px 10px;
    gap: 9px;
  }

  .section-description {
    display: none;
  }
}

/* 窄屏：横向滚动条。描述文字撤掉，只留图标 + 名称。 */
@media (max-width: 720px) {
  .settings-nav {
    overflow-x: auto;
    min-width: 0;
  }

  .section-list {
    grid-auto-flow: column;
    grid-auto-columns: max-content;
    justify-content: start;
    gap: 6px;
  }

  .section-link {
    min-height: 44px;
    padding: 8px 10px;
    gap: 7px;
    white-space: nowrap;
  }

  .section-copy {
    flex-direction: row;
    align-items: center;
  }

  .section-description {
    display: none;
  }
}
</style>
