<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AdminShell from '@/layouts/AdminShell.vue'
import UserAdminPage from './UserAdminPage.vue'
import ScheduledJobsPage from './ScheduledJobsPage.vue'
import KnowledgeBasesPage from './KnowledgeBasesPage.vue'
import SourcesPage from './SourcesPage.vue'
import FileDocumentsPage from './FileDocumentsPage.vue'
import DocumentManagementPage from './DocumentManagementPage.vue'

/**
 * 后台控制台：一条路由（/admin/:section?），板块由路径参数区分。
 *
 * 与设置中心（/settings/:section?）同一个取舍——板块地址可分享、可刷新、可收藏，
 * 旧的两条子路由地址（/admin/users、/admin/scheduled-jobs）原样有效。分区组件按需
 * 渲染；标题与分区说明随分区传给外壳，不再走 route.meta（一条路由没有逐子 meta 了）。
 */
const route = useRoute()
const router = useRouter()

const SECTIONS = [
  'users',
  'scheduled-jobs',
  'knowledge-bases',
  'sources',
  'files',
  'documents',
] as const

type AdminSection = (typeof SECTIONS)[number]

const SECTION_META: Record<AdminSection, { title: string; subtitle: string }> = {
  users: { title: '账号管理', subtitle: '访问控制' },
  'scheduled-jobs': { title: '任务管理', subtitle: '周期配置与任务执行' },
  'knowledge-bases': { title: '知识库', subtitle: '知识管理' },
  sources: { title: '来源管理', subtitle: '订阅绑定' },
  files: { title: '文件资料', subtitle: '知识管理' },
  documents: { title: '文档审核', subtitle: '解析与采用' },
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
    <KnowledgeBasesPage v-else-if="section === 'knowledge-bases'" />
    <SourcesPage v-else-if="section === 'sources'" />
    <FileDocumentsPage v-else-if="section === 'files'" />
    <DocumentManagementPage v-else-if="section === 'documents'" />
  </AdminShell>
</template>
