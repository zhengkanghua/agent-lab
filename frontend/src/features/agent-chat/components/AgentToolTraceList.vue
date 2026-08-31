<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { CircleAlert, Search, Wrench } from '@lucide/vue'
import BaseDisclosure from '@/shared/ui/BaseDisclosure.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import type { AgentToolTrace } from '../model/conversation'

const props = defineProps<{
  traces: AgentToolTrace[]
  /** 本轮是否还在流式中。决定轨迹默认展开还是收起。 */
  streaming?: boolean
}>()

/** 工具名到中文说明的映射。未知工具名原样显示，不猜。 */
const TOOL_LABELS: Readonly<Partial<Record<string, string>>> = {
  search_news: '检索新闻',
  read_document: '读取全文',
}

function labelFor(tool: string): string {
  return TOOL_LABELS[tool] ?? tool
}

/* 折叠后这一行是用户唯一能看到的轨迹信息，所以要能独立回答「它干了什么」。
   写成「检索新闻 · 读取全文」而不是「2 次工具调用」：后者只有数量没有内容，
   用户还是得展开才知道发生了什么，那折叠就没有意义了。
   同名工具连着调多次时合并计数，否则查了五个词会刷出五个「检索新闻」。 */
const summaryText = computed(() => {
  const counts = new Map<string, number>()
  for (const trace of props.traces) {
    const label = labelFor(trace.tool)
    counts.set(label, (counts.get(label) ?? 0) + 1)
  }
  const parts = [...counts].map(([label, count]) => (count > 1 ? `${label} ×${count}` : label))
  return parts.join(' · ')
})

const failedCount = computed(() => props.traces.filter((trace) => trace.failed).length)
const runningCount = computed(() => props.traces.filter((trace) => trace.content === null).length)

/* 摘要右侧的状态。失败优先于执行中：一条已经失败的调用比「还有一条在跑」更需要被看见，
   而收起状态下只有这一个位置能报。都正常时不写字——「成功」是默认预期，写出来是噪音。 */
const summaryMeta = computed(() => {
  if (failedCount.value > 0) return `${failedCount.value} 项未成功`
  if (runningCount.value > 0) return '执行中'
  return undefined
})

/* 流式中展开、落定收起（Q10）。
 *
 * 用本地 ref 承接，而不是把 open 直接算成 `streaming`：后者会让用户在流式期间的手动收起
 * 立刻被下一个 token 掀开。这里只在 streaming 发生跳变时改写状态，跳变之间用户说了算。
 *
 * 失败的轨迹例外：落定时不收起。用户需要看到那一条为什么没成，而不是先展开再找。 */
const open = ref(props.streaming === true)

watch(
  () => props.streaming,
  (streaming) => {
    if (streaming) {
      open.value = true
      return
    }
    open.value = failedCount.value > 0
  },
)

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
  <BaseDisclosure
    v-if="traces.length > 0"
    v-model:open="open"
    class="trace-block"
    :summary="summaryText"
    :meta="summaryMeta"
    tone="plain"
    size="sm"
  >
    <template #icon>
      <BaseSpinner v-if="runningCount > 0" :size="13" />
      <CircleAlert v-else-if="failedCount > 0" :size="13" />
      <Wrench v-else :size="13" />
    </template>

    <ol class="trace-list" aria-label="工具调用轨迹">
      <li
        v-for="trace in traces"
        :key="trace.id"
        class="trace-item"
        :class="{ 'is-failed': trace.failed }"
      >
        <span class="trace-icon" aria-hidden="true">
          <BaseSpinner v-if="trace.content === null" :size="15" />
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
          <BaseDisclosure
            v-if="trace.content !== null"
            class="trace-output"
            summary="查看返回内容"
            size="sm"
          >
            <pre>{{ trace.content }}</pre>
          </BaseDisclosure>
        </div>
      </li>
    </ol>
  </BaseDisclosure>
</template>

<style scoped>
.trace-block {
  margin-bottom: 14px;
  color: var(--text-secondary);
}

.trace-list {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 12px 14px;
  border: 1px solid var(--surface-sunken);
  border-radius: var(--radius-sm);
  background: var(--surface-base);
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
  color: var(--accent);
}

.trace-item.is-failed .trace-icon {
  color: var(--warning);
}

.trace-body {
  min-width: 0;
}

.trace-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-secondary);
  font-size: 0.78rem;
}

.trace-heading b {
  font-weight: 720;
}

/* 原来是 --text-muted：它在 --surface-base 上只有 3.85:1，这个字号不到 AA 的 4.5。
   「执行中 / 未成功」是状态信息不是装饰，改用 7.57:1 的 --text-secondary。 */
.trace-state {
  color: var(--text-secondary);
  font-size: 0.68rem;
}

.trace-arguments {
  margin-top: 2px;
  overflow-wrap: anywhere;
  color: var(--text-secondary);
  font-family: var(--mono-font);
  font-size: 0.7rem;
  line-height: 1.5;
}

.trace-output {
  margin-top: 5px;
}

/* 摘要行的字号、颜色、箭头都归 BaseDisclosure。留在这里的只有 pre：
   它是插槽内容，作用域属性来自本组件，所以后代选择器仍然命中。
   上间距由 .disclosure-body 提供，这里不再重复。 */
.trace-output pre {
  max-height: 260px;
  overflow: auto;
  padding: 10px 11px;
  border: 1px solid var(--border-subtle);
  border-radius: 4px;
  color: var(--text-secondary);
  background: var(--surface-raised);
  font-family: var(--mono-font);
  font-size: 0.7rem;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
