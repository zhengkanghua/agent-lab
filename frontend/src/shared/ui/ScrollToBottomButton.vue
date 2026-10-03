<script setup lang="ts">
import { ArrowDown } from '@lucide/vue'

/* 「回到最新」。浮在输入坞上沿外侧，只在用户上翻之后出现。Agent 对话页与检索页共用。
 *
 * 不常驻：贴着底看回答时它是废按钮，而输入坞上方那块正是视线落点。
 * hasNewContent 为真时换语气色并点一枚小圆点——告诉用户「离开底部的这段时间里又来东西了」，
 * 而不只是「可以往下滚」。
 *
 * 定位与进出场都收在组件内部（根元素就是那个定位锚点）：它必须贴在外部输入坞的上沿，
 * 所以使用方只要把它放进一个 position 非 static 的坞里即可（两页的输入坞都是 sticky），
 * 不必各自再抄一份定位与过渡——两份 scoped 样式改一处漏一处，正是这类浮层最容易失配的地方。
 */

defineProps<{
  /** 是否显示。由使用方的「已上翻」状态决定。 */
  open: boolean
  /** 上翻期间有新内容到达。 */
  hasNewContent?: boolean
}>()

const emit = defineEmits<{ jump: [] }>()
</script>

<template>
  <!-- open 为假时锚点留在 DOM 里但尺寸为 0（绝对定位），不影响输入坞的布局。 -->
  <div class="scroll-hint-anchor">
    <Transition name="scroll-hint">
      <button
        v-if="open"
        type="button"
        class="scroll-to-bottom"
        :class="{ 'has-new': hasNewContent }"
        :aria-label="hasNewContent ? '有新内容，回到最新' : '回到最新'"
        :title="hasNewContent ? '有新内容，回到最新' : '回到最新'"
        @click="emit('jump')"
      >
        <ArrowDown :size="18" aria-hidden="true" />
        <span v-if="hasNewContent" class="new-dot" aria-hidden="true"></span>
      </button>
    </Transition>
  </div>
</template>

<style scoped>
.scroll-hint-anchor {
  position: absolute;
  left: 50%;
  bottom: 100%;
  z-index: var(--z-local);
  margin-bottom: var(--space-2-5);
  transform: translateX(-50%);
}

/* 进场只改透明度与一小段下移：这片浮在正文上，直接闪现会打断阅读节奏。
   位移带上水平居中那一半，否则浮层会在动的时候横着跳一下。 */
.scroll-hint-enter-active,
.scroll-hint-leave-active {
  transition:
    opacity var(--duration-fast) var(--ease-out-smooth),
    transform var(--duration-fast) var(--ease-out-smooth);
}

.scroll-hint-enter-from,
.scroll-hint-leave-to {
  opacity: 0;
  transform: translateY(6px);
}

.scroll-to-bottom {
  position: relative;
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  padding: 0;
  border: 1px solid var(--border-strong);
  border-radius: 50%;
  color: var(--text-secondary);
  background: var(--surface-raised);
  box-shadow: var(--shadow-raised);
  cursor: pointer;
  transition:
    color var(--duration-fast) var(--ease-out-smooth),
    border-color var(--duration-fast) var(--ease-out-smooth),
    background-color var(--duration-fast) var(--ease-out-smooth),
    transform var(--duration-fast) var(--ease-out-smooth);
}

.scroll-to-bottom:hover {
  color: var(--accent);
  border-color: var(--accent);
}

/* 按下时收一档缩并且底色沉下来。这一枚点下去会把视口带到底部，
   目标可能只在几十像素外——位移小到看不出，没有这档就分不清是点中了还是没反应。 */
.scroll-to-bottom:active {
  border-color: var(--accent);
  background: var(--accent-soft);
  transform: scale(0.94);
}

/* 有新内容：描边与图标换强调色，让「有东西来了」在不读文字时也看得出来。 */
.scroll-to-bottom.has-new {
  color: var(--accent);
  border-color: var(--accent);
}

.new-dot {
  position: absolute;
  top: -1px;
  right: -1px;
  width: 8px;
  height: 8px;
  border: 2px solid var(--surface-raised);
  border-radius: 50%;
  background: var(--accent);
}

/* 触屏没有悬停，38px 也偏小：撑到可点高度那一档。 */
@media (pointer: coarse) {
  .scroll-to-bottom {
    width: var(--tap-target);
    height: var(--tap-target);
  }
}

/* 按下时的缩放属于动效，reduce 下撤掉，只留底色与描边的变化。 */
@media (prefers-reduced-motion: reduce) {
  .scroll-to-bottom:active {
    transform: none;
  }
}
</style>
