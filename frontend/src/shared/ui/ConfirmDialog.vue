<script setup lang="ts">
import { computed, ref } from 'vue'
import BaseButton from './BaseButton.vue'
import BaseDialog from './BaseDialog.vue'
import { pendingConfirm, settleConfirm } from '@/shared/composables/confirm'

/* 破坏性操作的确认框。全站唯一一处，挂在 App.vue 上，由 requestConfirm() 驱动。
 *
 * 与 window.confirm 的差别不只是长相：原生弹窗属于浏览器 chrome，样式改不了、品牌断了、
 * 移动端在页面顶部悬浮一条窄栏，而且它的文案是纯文本。自绘之后确认键能真的写「删除会话」，
 * 后果说明能分段，危险态有颜色。
 *
 * 两条有意为之的细节：
 *   1. 初始焦点落在「取消」上。默认落在确认键上的话，一路敲回车（或键盘用户的习惯性确认）
 *      就会执行一个不可恢复的动作。
 *   2. 遮罩点击不关（BaseDialog 的既定行为）。这与「点外面=取消」相反，但取消是安全方向，
 *      关掉它只是少一条路径，代价远小于误触遮罩丢掉一次已经想清楚的确认。
 */

const request = pendingConfirm
const cancelRef = ref<InstanceType<typeof BaseButton> | null>(null)

/**
 * 读屏在 alertdialog 出现时只播报对话框名，说明文字不会自动念出来。
 * 所以把标题与后果合并成名称——「删除这个会话？对话历史会一起清除，且无法恢复」
 * 必须整体到达，只说上半句等于让用户在一个不知道后果的确认框上做决定。
 */
const announcement = computed(() => {
  const current = request.value
  if (!current) return ''
  return [current.title, current.description].filter(Boolean).join('。')
})

function accept(): void {
  settleConfirm(true)
}

function cancel(): void {
  settleConfirm(false)
}
</script>

<template>
  <BaseDialog
    :open="request !== null"
    role="alertdialog"
    :label="announcement"
    :initial-focus="() => cancelRef?.$el ?? null"
    @close="cancel"
  >
    <div v-if="request" class="confirm">
      <h2 class="confirm-title">{{ request.title }}</h2>
      <p v-if="request.description" class="confirm-description">{{ request.description }}</p>

      <div class="confirm-actions">
        <BaseButton ref="cancelRef" variant="outline" @click="cancel">
          {{ request.cancelLabel ?? '取消' }}
        </BaseButton>
        <BaseButton :variant="request.tone === 'danger' ? 'danger' : 'primary'" @click="accept">
          {{ request.confirmLabel }}
        </BaseButton>
      </div>
    </div>
  </BaseDialog>
</template>

<style scoped>
.confirm {
  padding: var(--space-6) var(--space-6) var(--space-5);
}

.confirm-title {
  color: var(--text-primary);
  font-size: var(--fs-lg);
  font-weight: var(--fw-semibold);
  line-height: var(--lh-heading);
  overflow-wrap: anywhere;
}

.confirm-description {
  margin-top: var(--space-3);
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  line-height: 1.65;
  overflow-wrap: anywhere;
}

/* 取消在左、确认在右：与全站按钮「主操作靠右」的方向一致，
   也让键盘 Tab 从初始焦点（取消）出发正好按「先安全后危险」的顺序走。 */
.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-5);
}
</style>
