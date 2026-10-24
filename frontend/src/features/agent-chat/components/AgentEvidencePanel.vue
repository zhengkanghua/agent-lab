<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import { formatPublishedAt } from '@/shared/model/readable-result'
import type { DocumentEvidence } from '@/api/agent-evidence'

/* 证据面板：点一条证据引用就地展开的「当初那段」。
 *
 * 它只是展示层——切哪段正文由 `model/citation-excerpt.ts` 决定，打开当前全文由调用方
 * 沿用既有的阅读链路（emit 出去，上层不动）。主体用文本插值渲染，不用 v-html：
 * 这段是文档正文，来自抓取的网页或上传文件。
 *
 * 正文用可读正文的字体与 `pre-wrap`（不是等宽字体）：它是拿来读的一段话，不是技术输出。
 * 等宽 + `pre` 那套是工具轨迹的形态，这里套上去等于把「原始输出」的印象搬过来。
 */
const props = defineProps<{
  evidence: DocumentEvidence
  /** 当初那段正文。取自同一轮工具轨迹里这条引用对应的块，原样显示。 */
  body: string
  /** 面板的 DOM id，由调用方给出并写进触发按钮的 aria-controls。 */
  panelId: string
  /** 从答案正文里的内联小丸打开时置 true：正文中间的小丸离引用列表可能很远，不滚进去等于点了没反应。 */
  scrollIntoView?: boolean
}>()

const emit = defineEmits<{ 'open-fulltext': [trigger: HTMLElement] }>()

const root = ref<HTMLElement | null>(null)

/* 头部四项只用结构化元数据，不去解析工具正文里的「知识库:」「来源:」那些行：
   元数据本来就在 `DocumentEvidence` 里，再解析一遍文本等于给自己多一条会漂移的契约。
   来源的回退顺序与全文阅读界面一致：来源名 → 上传文件名 → 无外部来源。 */
const sourceLabel = computed(
  () => props.evidence.source_name ?? props.evidence.upload_filename ?? '无外部来源',
)
const publishedLabel = computed(() => formatPublishedAt(props.evidence.published_at))

onMounted(() => {
  if (props.scrollIntoView) root.value?.scrollIntoView({ block: 'nearest' })
})
</script>

<template>
  <div :id="panelId" ref="root" class="evidence-panel">
    <p class="evidence-meta">
      <span>{{ evidence.knowledge_base_name }}</span>
      <span>{{ sourceLabel }}</span>
      <span>{{ publishedLabel }}</span>
    </p>
    <p class="evidence-title">{{ evidence.title }}</p>
    <!-- 文本插值：这段正文来自文档，注入 HTML 会直接把抓来的脚本执行掉。 -->
    <p class="evidence-body">{{ body }}</p>
    <BaseButton
      class="evidence-fulltext"
      variant="outline"
      size="sm"
      @click="emit('open-fulltext', $event.currentTarget as HTMLElement)"
    >
      查看当前全文
    </BaseButton>
  </div>
</template>

<style scoped>
.evidence-panel {
  display: grid;
  gap: 8px;
  margin-top: 6px;
  padding: 11px 12px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--surface-raised);
}

/* 元数据一行三项：知识库 · 来源 · 发布时间。颜色与字号同工具轨迹的次要信息，
   它是「这条引用指向哪一篇」的注脚，不该比标题更响。 */
.evidence-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.evidence-title {
  color: var(--text-primary);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  line-height: var(--lh-heading);
  overflow-wrap: anywhere;
}

/* 限高 + 内部滚动：`read_document` 来源的整篇正文可以很长，撑开面板等于把对话流拉长，
   回来还得再找刚才那条引用。数值与工具轨迹的 `.trace-output pre` 同一档（260px）。 */
.evidence-body {
  max-height: 260px;
  overflow: auto;
  color: var(--text-primary);
  font-size: var(--fs-sm);
  line-height: var(--lh-reading);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.evidence-fulltext {
  justify-self: start;
}
</style>
