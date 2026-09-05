<script setup lang="ts">
withDefaults(
  defineProps<{
    /** danger 报错 / info 强调提示 / neutral 中性说明（非故障的状态说明用它）。 */
    tone?: 'danger' | 'info' | 'neutral'
    title?: string
    description?: string
  }>(),
  { tone: 'info', title: undefined, description: undefined },
)

/**
 * 全站状态面板的唯一实现，收编此前 6 份同构的「色底 + 标题 + 说明」块
 * （Agent 轮内错误、侧栏错误、会话打不开、任务行错误、账号行错误、压缩说明）。
 *
 * danger 渲染成 role="alert" 让读屏立即播报；neutral 是「这不是故障」的状态说明，
 * 用中性色而不是警告色，避免用户以为出了问题。
 */
const slots = defineSlots<{
  /** 替代 description 的富内容。 */
  default?: () => unknown
  /** 面板内的操作（如「重试」按钮），渲染在正文之后。 */
  actions?: () => unknown
  icon?: () => unknown
}>()
</script>

<template>
  <div class="base-callout" :class="`is-${tone}`" :role="tone === 'danger' ? 'alert' : undefined">
    <span v-if="slots.icon" class="callout-icon" aria-hidden="true">
      <slot name="icon" />
    </span>
    <div class="callout-body">
      <p v-if="title" class="callout-title">{{ title }}</p>
      <p v-if="description || slots.default" class="callout-description">
        <slot>{{ description }}</slot>
      </p>
      <div v-if="slots.actions" class="callout-actions">
        <slot name="actions" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.base-callout {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  padding: 12px 13px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
}

.callout-icon {
  display: inline-flex;
  flex: 0 0 auto;
  margin-top: 2px;
}

.callout-title {
  color: var(--text-primary);
  font-size: 0.85rem;
  font-weight: 720;
}

.callout-description {
  margin-top: 2px;
  color: var(--text-secondary);
  font-size: 0.8rem;
  line-height: 1.6;
}

/* 标题与说明同时存在时才需要顶开一点。 */
.callout-title + .callout-description {
  margin-top: 5px;
}

.callout-actions {
  margin-top: 10px;
}

.is-danger {
  border-color: var(--danger-soft);
  background: var(--danger-soft);
}

.is-danger .callout-title {
  color: var(--danger);
}

.is-info {
  border-color: var(--accent-soft);
  background: var(--accent-soft);
}

.is-info .callout-title {
  color: var(--accent);
}

.is-neutral {
  background: var(--surface-sunken);
}

.is-neutral .callout-title {
  color: var(--text-secondary);
}

.is-neutral .callout-description {
  color: var(--text-tertiary);
  font-size: 0.75rem;
  line-height: 1.55;
}
</style>
