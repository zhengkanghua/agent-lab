<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ChevronRight } from '@lucide/vue'

/* 折叠区。包的是原生 `<details>`，不是自己拿 button + div 拼一个。
 *
 * 原生的白拿好处：键盘操作、读屏的展开状态播报、禁用 JS 时仍能展开，
 * 以及浏览器的页内查找命中折叠内容时会自动展开它——这条自己实现不了。
 *
 * 加的是「受控」能力：第四步要让工具轨迹在流式中自动展开、落定后自动收起，
 * 那需要从外部改开合。原生 `<details>` 的 open 属性会被浏览器直接改写，
 * 所以这里监听 toggle 把状态同步回去，v-model:open 才不会和用户点击打架。
 */

const props = withDefaults(
  defineProps<{
    summary: string
    /** 不传就是非受控：完全交给浏览器。传了才进受控模式。 */
    open?: boolean
    /** 摘要右侧的次要文字，比如条数或耗时。 */
    meta?: string
    size?: 'md' | 'sm'
    /* 摘要行的语气。accent 用于「点开看某样东西」这种近似链接的场合；
       plain 用于设置项——那种行的标签要和同组其他控件的标签同色，
       只有图标带强调色，否则一组里会有一行突兀地变青。 */
    tone?: 'accent' | 'plain'
  }>(),
  { open: undefined, meta: undefined, size: 'md', tone: 'accent' },
)

const emit = defineEmits<{ 'update:open': [boolean] }>()

const isControlled = computed(() => props.open !== undefined)
const internalOpen = ref(props.open ?? false)

watch(
  () => props.open,
  (next) => {
    if (next !== undefined) internalOpen.value = next
  },
)

/* 浏览器改完 open 才派发 toggle，所以这里读 DOM 的真实状态而不是猜。
   非受控时也要同步，否则箭头方向会和内容对不上。 */
function onToggle(event: Event): void {
  const next = (event.target as HTMLDetailsElement).open
  internalOpen.value = next
  if (isControlled.value && next !== props.open) emit('update:open', next)
}
</script>

<template>
  <details
    class="base-disclosure"
    :class="[`is-${size}`, `tone-${tone}`]"
    :open="internalOpen"
    @toggle="onToggle"
  >
    <summary class="disclosure-summary">
      <ChevronRight class="disclosure-chevron" :size="size === 'md' ? 15 : 13" aria-hidden="true" />
      <!-- 摘要前的图标位。收起时箭头与图标并排，和同级控件的「图标 + 标签」对齐。 -->
      <slot name="icon" />
      <span class="disclosure-title">{{ summary }}</span>
      <span v-if="meta" class="disclosure-meta">{{ meta }}</span>
    </summary>
    <div class="disclosure-body">
      <slot />
    </div>
  </details>
</template>

<style scoped>
.base-disclosure {
  display: block;
}

.disclosure-summary {
  display: flex;
  align-items: center;
  gap: 7px;
  cursor: pointer;
  font-weight: 700;
  /* 去掉默认三角，改用可旋转的箭头；list-style 与 ::marker 两条都要写，
     Safari 只认后者。 */
  list-style: none;
}

.disclosure-summary::-webkit-details-marker {
  display: none;
}

.is-md .disclosure-summary {
  min-height: 32px;
  font-size: 0.78rem;
}

.is-sm .disclosure-summary {
  min-height: 24px;
  font-size: 0.7rem;
}

.tone-accent .disclosure-summary {
  color: var(--accent);
}

.tone-accent .disclosure-summary:hover {
  color: var(--accent-hover);
}

/* plain 的标签色跟着调用方给的上下文走，不自己定色，这样它和同组其他控件
   的标签天然一致。hover 只加深一档，不换色相。 */
.tone-plain .disclosure-summary:hover {
  color: var(--text-primary);
}

.disclosure-chevron {
  flex: none;
  transition: transform 150ms ease;
}

/* 箭头和图标位始终是强调色：它们是「这行能点开」的提示，与标签的语气无关。
   :slotted 命中的是插槽内容的根节点，lucide 图标的根就是 svg。 */
.tone-plain .disclosure-chevron,
.tone-plain .disclosure-summary :slotted(svg) {
  color: var(--accent);
}

[open] > .disclosure-summary .disclosure-chevron {
  transform: rotate(90deg);
}

.disclosure-title {
  min-width: 0;
}

/* meta 常常是用户自己设过的值（条数、耗时），不是可以眼扫过去的装饰，
   所以用 --text-secondary；--text-muted 在这个字号下对比度不够。 */
.disclosure-meta {
  margin-left: auto;
  color: var(--text-secondary);
  font-family: var(--mono-font);
  font-size: 0.68rem;
  font-weight: 400;
}

.disclosure-body {
  margin-top: 7px;
}

@media (prefers-reduced-motion: reduce) {
  .disclosure-chevron {
    transition: none;
  }
}
</style>
