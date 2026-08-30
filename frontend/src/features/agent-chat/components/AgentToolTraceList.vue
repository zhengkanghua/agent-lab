<script setup lang="ts">
import { CircleAlert, LoaderCircle, Search, Wrench } from '@lucide/vue'
import type { AgentToolTrace } from '../model/conversation'

defineProps<{ traces: AgentToolTrace[] }>()

/** 工具名到中文说明的映射。未知工具名原样显示，不猜。 */
const TOOL_LABELS: Readonly<Partial<Record<string, string>>> = {
  search_news: '检索新闻',
  read_document: '读取全文',
}

function labelFor(tool: string): string {
  return TOOL_LABELS[tool] ?? tool
}

/**
 * 把工具参数拍成一行可读文本。
 *
 * 只展示标量值：参数是模型生成的检索词，用来让用户看懂「它查了什么」。嵌套结构对这个
 * 目的没有帮助，塞进一行反而不可读，所以折叠成类型名。
 */
function formatArguments(args: Record<string, unknown>): string {
  const parts = Object.entries(args).map(([key, value]) => {
    if (typeof value === 'string') return `${key}=${value}`
    if (typeof value === 'number' || typeof value === 'boolean') return `${key}=${String(value)}`
    if (value === null || value === undefined) return `${key}=空`
    return `${key}=…`
  })
  return parts.join('，')
}
</script>

<template>
  <ol v-if="traces.length > 0" class="trace-list" aria-label="工具调用轨迹">
    <li
      v-for="trace in traces"
      :key="trace.id"
      class="trace-item"
      :class="{ 'is-failed': trace.failed }"
    >
      <span class="trace-icon" aria-hidden="true">
        <LoaderCircle v-if="trace.content === null" class="spin" :size="15" />
        <CircleAlert v-else-if="trace.failed" :size="15" />
        <Search v-else-if="trace.tool === 'search_news'" :size="15" />
        <Wrench v-else :size="15" />
      </span>

      <div class="trace-body">
        <p class="trace-heading">
          <b>{{ labelFor(trace.tool) }}</b>
          <span v-if="trace.content === null" class="trace-state">执行中</span>
          <span v-else-if="trace.failed" class="trace-state">未成功</span>
        </p>
        <p v-if="Object.keys(trace.arguments).length > 0" class="trace-arguments">
          {{ formatArguments(trace.arguments) }}
        </p>
        <details v-if="trace.content !== null" class="trace-output">
          <summary>查看返回内容</summary>
          <pre>{{ trace.content }}</pre>
        </details>
      </div>
    </li>
  </ol>
</template>

<style scoped>
.trace-list {
  display: grid;
  gap: 8px;
  margin: 0 0 14px;
  padding: 12px 14px;
  border: 1px solid var(--paper-200);
  border-radius: var(--radius-sm);
  background: var(--paper-100);
  list-style: none;
}

.trace-item {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr);
  gap: 9px;
  align-items: start;
}

.trace-icon {
  display: grid;
  place-items: center;
  padding-top: 1px;
  color: var(--source-600);
}

.trace-item.is-failed .trace-icon {
  color: var(--warning-600);
}

.trace-body {
  min-width: 0;
}

.trace-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--ink-800);
  font-size: 0.78rem;
}

.trace-heading b {
  font-weight: 720;
}

.trace-state {
  color: var(--ink-500);
  font-size: 0.68rem;
}

.trace-arguments {
  margin-top: 2px;
  overflow-wrap: anywhere;
  color: var(--ink-700);
  font-family: var(--mono-font);
  font-size: 0.7rem;
  line-height: 1.5;
}

.trace-output {
  margin-top: 5px;
  font-size: 0.7rem;
}

.trace-output summary {
  cursor: pointer;
  color: var(--signal-600);
  font-weight: 700;
}

.trace-output pre {
  max-height: 260px;
  overflow: auto;
  margin-top: 7px;
  padding: 10px 11px;
  border: 1px solid var(--paper-300);
  border-radius: 4px;
  color: var(--ink-700);
  background: var(--paper-50);
  font-family: var(--mono-font);
  font-size: 0.7rem;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

/* .spin 见 styles/components/motion.css。 */
</style>
