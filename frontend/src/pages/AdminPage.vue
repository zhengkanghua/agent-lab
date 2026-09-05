<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AdminShell from '@/layouts/AdminShell.vue'
import UserAdminPage from './UserAdminPage.vue'
import ScheduledJobsPage from './ScheduledJobsPage.vue'

/**
 * 后台控制台：一条路由（/admin/:section?），板块由路径参数区分。
 *
 * 与设置中心（/settings/:section?）同一个取舍——板块地址可分享、可刷新、可收藏，
 * 旧的两条子路由地址（/admin/users、/admin/scheduled-jobs）原样有效。分区组件按需
 * 渲染；标题与分区说明随分区传给外壳，不再走 route.meta（一条路由没有逐子 meta 了）。
 */
const route = useRoute()
const router = useRouter()

const SECTIONS = ['users', 'scheduled-jobs'] as const

type AdminSection = (typeof SECTIONS)[number]

const SECTION_META: Record<AdminSection, { title: string; subtitle: string }> = {
  users: { title: '账号管理', subtitle: '访问控制' },
  'scheduled-jobs': { title: '定时任务', subtitle: '数据自动化' },
}

const section = computed<AdminSection>(() => {
  const value = route.params.section
  const key = Array.isArray(value) ? value[0] : value
  return SECTIONS.includes(key as AdminSection) ? (key as AdminSection) : 'users'
})

// 非法分区重定向回账号管理：地址栏与界面内容对齐，收藏与分享才不会落空（同 SettingsPage）。
watch(
  section,
  (value) => {
    if (route.params.section !== value) {
      void router.replace({ name: 'admin', params: { section: value } })
    }
  },
  { immediate: true },
)

const heading = computed(() => SECTION_META[section.value])
</script>

<template>
  <AdminShell :heading-title="heading.title" :heading-subtitle="heading.subtitle">
    <UserAdminPage v-if="section === 'users'" />
    <ScheduledJobsPage v-else-if="section === 'scheduled-jobs'" />
  </AdminShell>
</template>
