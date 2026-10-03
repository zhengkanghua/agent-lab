<script setup lang="ts">
import BaseButton from './BaseButton.vue'

/* 分页器：上一页 / 第 N 页 / 下一页。目录类视图共用的一种形态。
 *
 * 收编前这一段在六个地方各写一遍，其中三处还把页大小 25 硬编码了两遍——点击时减 25、
 * 页号再除以 25；改一处漏一处，页号就开始说谎。页大小属于数据层（一次取多少条），所以这里
 * 只负责呈现：翻页的步长由调用方决定，本组件只发「上一页 / 下一页」两个事件。
 *
 * page 可以不给（侧栏那种窄容器里塞不下页码）：给了才渲染「第 N 页」。
 */

defineProps<{
  /** 当前页，从 1 开始。省略则不显示页码。 */
  page?: number
  hasPrevious: boolean
  hasMore: boolean
  /** 请求在途时禁用两枚键，避免连点翻页。 */
  busy?: boolean
  /** 对齐：表格类的页脚贴右（默认），侧栏与设置分区里贴左。 */
  align?: 'start' | 'end'
}>()

const emit = defineEmits<{ previous: []; next: [] }>()
</script>

<template>
  <div class="pager" :class="{ 'is-start': align === 'start' }" :aria-busy="busy || undefined">
    <BaseButton
      variant="outline"
      size="sm"
      :disabled="busy || !hasPrevious"
      @click="emit('previous')"
    >
      上一页
    </BaseButton>
    <span v-if="page !== undefined" class="page-number">第 {{ page }} 页</span>
    <BaseButton variant="outline" size="sm" :disabled="busy || !hasMore" @click="emit('next')">
      下一页
    </BaseButton>
    <!-- 额外动作（例如用量表的「刷新」）排在翻页键之后。 -->
    <slot />
  </div>
</template>

<style scoped>
.pager {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-2);
  transition: opacity var(--duration-fast) var(--ease-out-smooth);
}

/* 在途：两条翻页键本来就禁用了，但「按了没反应」与「请求在跑」在界面上长得一模一样。
   整条退半步并挂 aria-busy，读屏与眼睛都能分辨；拿到结果后自动回来。
   只降不隐，页码与当前页仍要读得清。 */
.pager[aria-busy='true'] {
  opacity: 0.6;
}

.pager.is-start {
  justify-content: flex-start;
}

.page-number {
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
}
</style>
