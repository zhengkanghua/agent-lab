<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from 'vue'
import type { DocumentPreviewDto } from '@/api/document-review'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import SafeMarkdown from '@/shared/ui/SafeMarkdown.vue'

const props = defineProps<{ preview: DocumentPreviewDto; stale?: boolean }>()
const view = ref('body')
const views = [
  ['body', '正文'],
  ['structure', '结构目录'],
  ['chunks', 'Chunk'],
] as const
const prefix = useId()
const located = ref<string[]>([])
const chunkOffset = ref(0)
const chunks = computed(() => props.preview.chunk_result.chunks)
const shownChunks = computed(() => chunks.value.slice(chunkOffset.value, chunkOffset.value + 20))
const headingMap = computed(
  () => new Map(props.preview.document.outline.map((item) => [item.id, item.title])),
)
const blockNames: Record<string, string> = {
  heading: '标题',
  paragraph: '正文',
  list_item: '列表项',
  code: '代码',
  table: '表格',
  image: '图片说明',
  group: '内容组',
  formula: '公式',
}

async function locate(ids: string[]) {
  view.value = 'structure'
  located.value = ids
  await nextTick()
  const element = ids.map((id) => document.getElementById(prefix + '-' + id)).find(Boolean)
  element?.scrollIntoView({ block: 'nearest' })
  element?.focus({ preventScroll: true })
}

watch(
  () => props.preview,
  () => {
    located.value = []
    chunkOffset.value = 0
  },
)
</script>

<template>
  <section class="document-preview" aria-label="解析与 Chunk 预览">
    <div class="preview-tabs" role="group" aria-label="预览内容">
      <button
        v-for="[key, label] in views"
        :key="key"
        type="button"
        :aria-pressed="view === key"
        @click="view = key"
      >
        {{ label }}<span v-if="key === 'chunks'">（{{ chunks.length }}）</span>
      </button>
    </div>
    <BaseCallout
      v-if="stale"
      tone="neutral"
      description="正文已有未保存修改；下面仍是上次预览，保存并重新生成后才能采用。"
    />
    <div v-if="view === 'body'" class="preview-body">
      <SafeMarkdown
        v-if="preview.document.text_format === 'markdown'"
        :markdown="preview.document.body"
      />
      <pre v-else class="review-text">{{ preview.document.body }}</pre>
    </div>
    <div v-else-if="view === 'structure'" class="structure-view">
      <nav aria-label="标题目录" class="outline">
        <p v-if="!preview.document.outline.length" class="muted">
          没有章节标题，内容按阅读顺序排列。
        </p>
        <ol v-else>
          <li
            v-for="entry in preview.document.outline"
            :key="entry.id"
            :style="{ paddingInlineStart: Math.min(entry.level - 1, 5) * 12 + 'px' }"
          >
            <button type="button" @click="locate([entry.id])">
              <span class="heading-level">H{{ entry.level }}</span
              >{{ entry.title }}
            </button>
          </li>
        </ol>
      </nav>
      <ol class="structure-blocks" aria-label="结构内容">
        <li
          v-for="block in preview.document.blocks"
          :id="prefix + '-' + block.id"
          :key="block.id"
          tabindex="-1"
          :class="{ located: located.includes(block.id) }"
        >
          <p class="block-meta">
            {{ blockNames[block.kind] || block.kind }}
            <span v-if="block.heading_ids.length">
              ·
              {{
                block.heading_ids
                  .map((id) => headingMap.get(id))
                  .filter(Boolean)
                  .join(' / ')
              }}</span
            >
          </p>
          <pre class="review-text">{{
            block.text || (block.kind === 'table' ? '表格正文见正文视图和对应 Chunk。' : '结构容器')
          }}</pre>
        </li>
      </ol>
    </div>
    <div v-else class="chunk-view">
      <p class="muted">标题路径也计入长度。采用时使用下面这份 Chunk 清单和向量化文本。</p>
      <article v-for="chunk in shownChunks" :key="chunk.sequence" class="preview-chunk">
        <header>
          <strong>Chunk {{ chunk.sequence + 1 }}</strong>
          <span
            :class="{ over: chunk.token_count > preview.chunk_result.specification.max_tokens }"
          >
            {{ chunk.token_count }} / {{ preview.chunk_result.specification.max_tokens }} token
          </span>
        </header>
        <p class="chunk-path">
          {{ chunk.headings.length ? chunk.headings.join(' / ') : '无章节标题' }}
        </p>
        <pre class="review-text">{{ chunk.text }}</pre>
        <div class="chunk-tools">
          <BaseButton
            variant="ghost"
            size="sm"
            :disabled="!chunk.block_ids.length"
            @click="locate(chunk.block_ids)"
            >定位结构内容</BaseButton
          >
          <details>
            <summary>实际向量化文本</summary>
            <pre class="review-text">{{ chunk.embedding_text }}</pre>
          </details>
        </div>
      </article>
      <p v-if="!chunks.length" class="muted">没有可采用的 Chunk，请修正正文并重新生成预览。</p>
      <div v-if="chunks.length > 20" class="chunk-pagination">
        <BaseButton
          variant="outline"
          size="sm"
          :disabled="chunkOffset === 0"
          @click="chunkOffset -= 20"
          >前一组</BaseButton
        >
        <span
          >{{ chunkOffset + 1 }}–{{ Math.min(chunkOffset + 20, chunks.length) }} /
          {{ chunks.length }}</span
        >
        <BaseButton
          variant="outline"
          size="sm"
          :disabled="chunkOffset + 20 >= chunks.length"
          @click="chunkOffset += 20"
          >后一组</BaseButton
        >
      </div>
    </div>
  </section>
</template>

<style scoped>
.document-preview {
  display: grid;
  gap: 16px;
  min-width: 0;
}
.preview-tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--border-subtle);
}
.preview-tabs button {
  padding: 11px 13px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--text-secondary);
  font: inherit;
  font-size: var(--fs-sm);
  cursor: pointer;
}
.preview-tabs button[aria-pressed='true'] {
  color: var(--accent);
  border-bottom-color: var(--accent);
  font-weight: var(--fw-semibold);
}
.preview-tabs span {
  font-size: var(--fs-xs);
}
.preview-body {
  min-width: 0;
  overflow-wrap: anywhere;
}
.review-text {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
  font-size: var(--fs-sm);
  line-height: 1.8;
}
.muted,
.block-meta,
.chunk-path {
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  line-height: 1.7;
}
.structure-view {
  display: grid;
  gap: 20px;
}
.outline {
  padding: 12px;
  background: var(--surface-sunken);
  border-radius: var(--radius-sm);
}
.outline ol {
  list-style: none;
  padding: 0;
  margin: 0;
}
.outline button {
  display: flex;
  gap: 10px;
  padding: 7px 4px;
  text-align: left;
  border: 0;
  background: transparent;
  color: var(--text-primary);
  font: inherit;
  font-size: var(--fs-sm);
  overflow-wrap: anywhere;
  cursor: pointer;
}
.heading-level {
  flex-shrink: 0;
  color: var(--accent);
  font-size: var(--fs-xs);
  padding-top: 2px;
}
.structure-blocks {
  padding-left: 24px;
  margin: 0;
}
.structure-blocks li {
  padding: 12px 8px;
  border-bottom: 1px solid var(--border-subtle);
  scroll-margin-top: 90px;
}
.structure-blocks li::marker {
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
}
.structure-blocks li.located {
  background: var(--accent-soft);
  outline: 1px solid var(--accent);
  border-radius: var(--radius-sm);
}
.block-meta {
  margin-bottom: 7px;
}
.chunk-view {
  display: grid;
  gap: 16px;
}
.preview-chunk {
  padding: 16px 0;
  border-bottom: 1px solid var(--border-subtle);
  min-width: 0;
}
.preview-chunk header {
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  font-size: var(--fs-xs);
}
.preview-chunk header strong {
  font-weight: var(--fw-semibold);
}
.preview-chunk header span {
  color: var(--text-secondary);
}
.preview-chunk header .over {
  color: var(--danger);
}
.chunk-path {
  margin: 10px 0;
  color: var(--accent);
  overflow-wrap: anywhere;
}
.chunk-tools {
  margin-top: 12px;
  display: grid;
  gap: 8px;
  justify-items: start;
}
.chunk-tools details {
  width: 100%;
  font-size: var(--fs-xs);
}
.chunk-tools summary {
  cursor: pointer;
  padding: 8px 0;
  color: var(--text-secondary);
}
.chunk-tools details pre {
  padding: 12px;
  background: var(--surface-sunken);
  border-radius: var(--radius-sm);
}
.chunk-pagination {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  font-size: var(--fs-xs);
}
</style>
