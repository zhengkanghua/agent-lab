<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { ChevronDown } from '@lucide/vue'
import { usePopoverPanel } from '@/shared/composables/usePopoverPanel'

/* 浮层：一枚安静的胶囊触发键 + 点开才出现的面板。
 *
 * 形状来自两处既有选择器（知识库范围、Agent 模型）——它们本来就是同一个东西的
 * 两份实现，连面板的圆角、描边、阴影、内外边距都一模一样。收编成一份之后，
 * 「触发键长什么样」「面板长什么样」各只有一处定义，两个选择器只填内容。
 *
 * 三件事归这里：
 *   1. 定位。面板 Teleport 到 body、position: fixed，落点由 usePopoverPanel 算，
 *      下方放不下就翻到上方。**这是本组件存在的首要理由**：原来写死朝下展开，
 *      在贴底的输入坞里整块跑到视口外面（2026-10 实测 71% 被切掉）。
 *   2. 开合。点触发键、点面板外、按 Esc 三条路都通向「关闭」，Esc 关掉后焦点回到
 *      触发键（面板已被移除，焦点留在原地的话 Tab 会从文档开头重来）。
 *   3. 无障碍接线。aria-expanded / aria-haspopup / aria-controls 由这里统一挂上，
 *      面板带名字，读屏知道点开的是一块什么面板。
 *
 * 不归这里：面板里放什么、开着时要不要重新拉数据、选择怎么落库。前两者由调用方
 * 经默认插槽与 `open` 事件实现，后者是调用方自己的事。
 *
 * 触发键的内容由 #trigger 插槽给（图标、文字、状态小徽章），样式写在调用方自己的
 * scoped 块里——插槽内容在调用方的模板里编译，带调用方的 scope 属性，规则生效。
 * 触发键**元素本身**由本组件渲染，所以它的皮肤在共享层 popover.css；
 * 调用方要改它的外观，用 tone 或 triggerClass，别指望自己的 scoped 选择器命中它。
 */

const props = withDefaults(
  defineProps<{
    open: boolean
    /** 面板的无障碍名字。读屏在打开时会把它和 aria-expanded 一起播报。 */
    label: string
    /** 面板与触发键的间距。 */
    gap?: number
    /** 触发键的语气。warning 用于「当前选择有问题，不点开也该看得出来」。 */
    tone?: 'default' | 'warning'
  }>(),
  { gap: 6, tone: 'default' },
)

const emit = defineEmits<{
  'update:open': [value: boolean]
  /** 面板刚打开。调用方据此重新拉一次数据（比如模型目录）。 */
  open: []
}>()

const panelId = useId()

/* 两个 ref 都交给 usePopoverPanel：触发键是定位锚点与焦点归还目标，面板是量尺寸的对象。
   它们的元素分别由本组件渲染（触发键）和 Teleport 出去（面板），所以 ref 必须在这里。 */
const triggerRef = ref<HTMLButtonElement | null>(null)
const panelRef = ref<HTMLElement | null>(null)

const { position, positioned } = usePopoverPanel({
  open: () => props.open,
  triggerRef,
  panelRef,
  gap: props.gap,
  onRequestClose: (reason) => {
    emit('update:open', false)
    // 点外面不抢焦点：那一下点击本该落在用户真正点的东西上。
    if (reason === 'escape') triggerRef.value?.focus()
  },
})

function toggle(): void {
  if (props.open) {
    emit('update:open', false)
    return
  }
  emit('update:open', true)
  emit('open')
}

/* 位置用内联样式而不是 CSS 自定义属性：top / left 是量出来的像素，没有别的地方
   需要复用它们；发成变量反而多一层「谁覆盖谁」的疑问。 */
const panelStyle = computed(() => ({
  top: `${position.value.top}px`,
  left: `${position.value.left}px`,
}))
</script>

<template>
  <button
    ref="triggerRef"
    type="button"
    class="popover-trigger"
    :class="[`tone-${tone}`]"
    :aria-expanded="open"
    aria-haspopup="dialog"
    :aria-controls="panelId"
    @click="toggle"
  >
    <slot name="trigger" />
    <ChevronDown class="popover-chevron" :size="15" aria-hidden="true" />
  </button>

  <!-- 面板挂到 body：留在原地要穿过一串 overflow 祖先（检索流、会话列表、表格滚动区），
       而且触发键所在的坞自己是层叠上下文，里面的 z-index 出不去。 -->
  <Teleport to="body">
    <div
      v-if="open"
      :id="panelId"
      ref="panelRef"
      class="popover-panel"
      :class="{ 'is-positioned': positioned }"
      :style="panelStyle"
      role="dialog"
      :aria-label="label"
    >
      <slot />
    </div>
  </Teleport>
</template>
