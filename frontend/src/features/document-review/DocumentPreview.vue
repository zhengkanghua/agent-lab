<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from 'vue'
import type { DocumentPreviewDto } from '@/api/document-review'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseDisclosure from '@/shared/ui/BaseDisclosure.vue'
import SafeMarkdown from '@/shared/ui/SafeMarkdown.vue'

const props = defineProps<{ preview: DocumentPreviewDto; stale?: boolean }>()
const view = ref('body')

/* 界面文案一律用「片段」，这是 CONTEXT.md 对 Chunk 词条的指派：
   「面向用户的界面文案可以说『片段』，代码、文档和讨论里用 Chunk」。
   检索页早就在写「片段 1 / 2」，这里原来写 Chunk，同一个概念在两页两个词。
   变量名、类名、接口字段仍然是 chunk*，那属于「代码」那一侧，不跟着改。 */
const views = [
  ['body', '正文'],
  ['structure', '结构目录'],
  ['chunks', '片段'],
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
  <section class="document-preview" aria-label="解析与片段预览">
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
            block.text || (block.kind === 'table' ? '表格正文见正文视图和对应片段。' : '结构容器')
          }}</pre>
        </li>
      </ol>
    </div>
    <div v-else class="chunk-view">
      <p class="muted">标题路径也计入长度。采用时使用下面这份片段清单和向量化文本。</p>
      <article v-for="chunk in shownChunks" :key="chunk.sequence" class="preview-chunk">
        <header>
          <strong>片段 {{ chunk.sequence + 1 }}</strong>
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
          <BaseDisclosure class="chunk-embedding" summary="实际向量化文本">
            <pre class="review-text">{{ chunk.embedding_text }}</pre>
          </BaseDisclosure>
        </div>
      </article>
      <p v-if="!chunks.length" class="muted">没有可采用的片段，请修正正文并重新生成预览。</p>
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
  transition:
    color var(--duration-fast) var(--ease-out-smooth),
    border-color var(--duration-fast) var(--ease-out-smooth);
}
/* 未选中的 tab 也要回应鼠标：只在选中态换色，未选中时整排看起来是死的。 */
.preview-tabs button:hover {
  color: var(--text-primary);
}
/* 按下时补一层底。这一排是「切换视图」而不是「跳转」，没有位移可做，
   颜色从悬停态停住就开始发钝，按下换底才收得到一次明确的回执。 */
.preview-tabs button:active {
  background: var(--surface-sunken);
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
  /* 悬停底比文字各宽 8px：pill 有呼吸空间，标题仍与上面的目录项左对齐。
     .outline 自身有 12px 内边距，8px 的外溢收得进去。 */
  width: calc(100% + 16px);
  margin: 0 -8px;
  padding: 7px 8px;
  text-align: left;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-primary);
  font: inherit;
  font-size: var(--fs-sm);
  overflow-wrap: anywhere;
  cursor: pointer;
  transition: background-color var(--duration-fast) var(--ease-out-smooth);
}
/* 目录项是「跳过去看这一段」的入口，和周围的静态文字同色，不补底就看不出能点。
   底衬在 .outline 的下沉底之上，所以取抬升色而不是再深一档。 */
.outline button:hover {
  background: var(--surface-raised);
}
/* 这一排是跳转：按下之后视口会滚走，回执本来由位移提供；万一目标就在眼前、
   页面没动，没有这一档就完全看不出点中了。 */
.outline button:active {
  background: var(--surface-raised);
  box-shadow: inset 2px 0 0 var(--accent);
}
/* 这两排都是自造按钮，触屏下都要撑到可点高度，但横向内边距不同，
   所以分成两条写——合成一条会逼出「先给 8px 再改回 13px」那种覆盖。 */
@media (pointer: coarse) {
  /* 结构目录的每一项是整行按钮：桌面 34px 左右，手指点不准。 */
  .outline button {
    min-height: var(--tap-target);
    padding: 10px 8px;
  }

  /* 视图切换的三个 tab 同理；水平的 13px 是它的常态内边距，只抬高度、不动横向。 */
  .preview-tabs button {
    min-height: var(--tap-target);
  }
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
  border-radius: var(--radius-sm);
  scroll-margin-top: 90px;
  /* 从目录跳过来时高亮的是这一块。没有过渡的话它硬切一下，
     在长文档里会让人错以为滚错了位置。 */
  transition:
    background-color var(--duration-normal) var(--ease-out-smooth),
    box-shadow var(--duration-normal) var(--ease-out-smooth);
}
.structure-blocks li::marker {
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
}
.structure-blocks li.located {
  background: var(--accent-soft);
  box-shadow: inset 0 0 0 1px var(--accent);
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
/* 折叠区本体走 BaseDisclosure（箭头、悬停、按下、reduce-motion 都归它一份），
   这里只补一条：.chunk-tools 是 justify-items: start 的网格，不撑开就会缩成内容宽。 */
.chunk-embedding {
  width: 100%;
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
