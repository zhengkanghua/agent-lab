<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { AlertTriangle, Clock3, ExternalLink, FileText, RotateCcw, X } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseIconButton from '@/shared/ui/BaseIconButton.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import SafeMarkdown from '@/shared/ui/SafeMarkdown.vue'
import type { ApiError } from '@/api/client'
import type { DocumentEvidence } from '@/api/agent-evidence'
import { resolveErrorCopy, type ErrorCopy } from '@/api/error-copy'
import type { NewsDocumentDetail } from '../model/document-detail'
import { formatPublishedAt, type NewsReadableResult } from '../model/search-result'

const props = defineProps<{
  open: boolean
  result: NewsReadableResult | null
  detail: NewsDocumentDetail | null
  loading: boolean
  error: ApiError | null
  hashMismatch: boolean
  evidence?: DocumentEvidence | null
}>()

const emit = defineEmits<{
  close: []
  closed: []
  retry: []
}>()

const panel = ref<HTMLElement | null>(null)
const closeButton = ref<InstanceType<typeof BaseIconButton> | null>(null)
let previousBodyOverflow = ''
let effectsActive = false

// 全文接口的失败按 HTTP 状态分类就够：它不像检索链路那样有一串上游 code，
// 「没这篇」和「服务不可用」正好对应 404 与 503。
const COPY_BY_STATUS: Readonly<Partial<Record<number, ErrorCopy>>> = {
  404: {
    title: '原文档已删除',
    description: '当前原文已不可用，已有检索记录和回答仍保留。',
  },
  409: {
    title: '知识库已停用',
    description: '当前知识库暂停提供资料，请在启用后重新读取。',
  },
  503: {
    title: '全文服务暂时不可用',
    description: '可以稍后重试当前文档。',
  },
}

const FALLBACK_COPY: ErrorCopy = {
  title: '全文加载未完成',
  description: '当前文档的完整正文没有载入，已有记录仍保留在页面中。',
}

const errorCopy = computed(() =>
  resolveErrorCopy(props.error, { byStatus: COPY_BY_STATUS, fallback: FALLBACK_COPY }),
)

// 详情接口的来源与链接都是可选字段：详情缺省时回退搜索结果行，两边都没有就不渲染链接。
const readerUrl = computed(() => (props.detail ? props.detail.url : (props.result?.url ?? null)))
const readerSourceName = computed(() =>
  props.detail
    ? (props.detail.sourceName ?? props.detail.uploadFilename ?? '未指定来源')
    : (props.result?.sourceName ?? props.result?.uploadFilename ?? '未指定来源'),
)

watch(
  () => props.open,
  async (open) => {
    if (open) {
      previousBodyOverflow = document.body.style.overflow
      effectsActive = true
      document.body.style.overflow = 'hidden'
      document.addEventListener('keydown', handleKeydown)
      await nextTick()
      closeButton.value?.focus()
      return
    }
    releaseDialogEffects()
  },
  { immediate: true },
)

onBeforeUnmount(releaseDialogEffects)

function releaseDialogEffects(): void {
  if (!effectsActive) return
  effectsActive = false
  document.removeEventListener('keydown', handleKeydown)
  document.body.style.overflow = previousBodyOverflow
}

function handleKeydown(event: KeyboardEvent): void {
  if (!props.open) return
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
    return
  }
  if (event.key !== 'Tab' || !panel.value) return

  const focusable = [
    ...panel.value.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ].filter((element) => !element.hasAttribute('hidden'))
  if (!focusable.length) {
    event.preventDefault()
    panel.value.focus()
    return
  }

  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition name="reader" @after-leave="emit('closed')">
      <div
        v-if="open && result"
        class="reader-backdrop"
        style="container-type: inline-size"
        @click.self="emit('close')"
      >
        <aside
          ref="panel"
          class="reader-panel"
          role="dialog"
          aria-modal="true"
          aria-labelledby="reader-title"
          :aria-busy="loading"
          tabindex="-1"
        >
          <div class="reader-signal" aria-hidden="true"></div>

          <header class="reader-header">
            <div class="reader-kicker">
              <FileText :size="15" aria-hidden="true" />
              <span>文档全文</span>
            </div>
            <BaseIconButton ref="closeButton" size="lg" label="关闭全文" @click="emit('close')">
              <X :size="20" aria-hidden="true" />
            </BaseIconButton>
          </header>

          <div class="reader-scroll">
            <div class="reader-title-block">
              <div class="reader-meta">
                <span>{{ detail?.knowledgeBaseName ?? result.knowledgeBaseName }}</span>
                <span class="reader-source">{{ readerSourceName }}</span>
                <span>
                  <Clock3 :size="13" aria-hidden="true" />
                  {{ formatPublishedAt(detail ? detail.publishedAt : result.publishedAt) }}
                </span>
              </div>
              <h2 id="reader-title">{{ detail?.title ?? result.title }}</h2>
              <a
                v-if="readerUrl"
                class="reader-origin"
                :href="readerUrl"
                target="_blank"
                rel="noopener noreferrer"
              >
                访问原文
                <ExternalLink :size="15" aria-hidden="true" />
              </a>
              <span v-else class="reader-origin is-missing">未提供原文链接</span>
            </div>

            <section v-if="evidence" class="reader-evidence" aria-label="当时引用的片段">
              <h3>当时引用的片段</h3>
              <p class="evidence-source">
                {{ evidence.knowledge_base_name }} · {{ evidence.title }}
              </p>
              <blockquote>{{ evidence.excerpt }}</blockquote>
              <p v-if="evidence.truncated" class="evidence-note">当时仅读取了部分正文。</p>
              <p class="evidence-note">下方读取当前原文，旧回答和这段引用保持原样。</p>
            </section>

            <div v-if="hashMismatch" class="version-warning" role="status">
              <AlertTriangle :size="17" aria-hidden="true" />
              <p>原文已更新，当前内容与取得检索片段或引用时不同。</p>
            </div>

            <div v-if="loading" class="reader-loading" aria-live="polite">
              <BaseSpinner :size="22" />
              <div>
                <strong>正在读取全文</strong>
                <span>从知识库载入当前版本</span>
              </div>
              <span v-for="index in 7" :key="index" class="reader-skeleton skeleton-line"></span>
            </div>

            <div v-else-if="error" class="reader-error" role="alert">
              <span class="reader-error-icon">
                <AlertTriangle :size="22" aria-hidden="true" />
              </span>
              <div>
                <h3>{{ errorCopy.title }}</h3>
                <p>{{ errorCopy.description }}</p>
                <BaseButton
                  v-if="error.retryable"
                  class="reader-retry"
                  variant="outline"
                  size="sm"
                  @click="emit('retry')"
                >
                  <template #icon><RotateCcw :size="15" aria-hidden="true" /></template>
                  重试全文
                </BaseButton>
              </div>
            </div>

            <article v-else-if="detail" class="reader-article">
              <SafeMarkdown
                v-if="detail.mimeType === 'text/markdown'"
                :markdown="detail.contentText"
              />
              <p v-else>{{ detail.contentText }}</p>
            </article>
          </div>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style>
.reader-backdrop {
  position: fixed;
  z-index: var(--z-reader);
  inset: 0;
  display: flex;
  justify-content: flex-end;
  background: var(--surface-overlay);
  backdrop-filter: blur(2px);
}

.reader-panel {
  position: relative;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  width: min(720px, 52vw);
  min-width: 520px;
  height: 100dvh;
  outline: 0;
  overflow: hidden;
  color: var(--text-primary);
  background: var(--surface-raised);
  box-shadow: var(--shadow-drawer);
}

.reader-signal {
  position: absolute;
  z-index: var(--z-local);
  inset: 0 auto 0 0;
  width: 4px;
  background: var(--accent);
}

.reader-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 68px;
  padding: 0 22px 0 26px;
  border-bottom: 1px solid var(--border-subtle);
}

.reader-kicker {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
}

.reader-scroll {
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 38px clamp(30px, 5vw, 70px) 80px;
}

.reader-title-block {
  padding-bottom: 28px;
  border-bottom: 1px solid var(--text-primary);
}

.reader-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 13px;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
}

.reader-meta > span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.reader-source {
  max-width: 100%;
  padding: 3px 7px;
  border-radius: var(--radius-sm);
  overflow-wrap: anywhere;
  color: var(--accent);
  background: var(--accent-soft);
  font-weight: var(--fw-bold);
}

.reader-title-block h2 {
  margin-top: 14px;
  overflow-wrap: anywhere;
  color: var(--text-primary);
  font-size: var(--fs-3xl);
  font-weight: var(--fw-bold);
  letter-spacing: 0;
  line-height: 1.25;
}

.reader-origin {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 36px;
  margin-top: 15px;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
  text-decoration: none;
}

.reader-origin:hover {
  text-decoration: underline;
  text-underline-offset: 3px;
}

.reader-panel .version-warning {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  margin-top: 24px;
  padding: 13px 14px;
  border-left: 3px solid var(--warning);
  color: var(--text-secondary);
  background: var(--warning-soft);
  font-size: var(--fs-sm);
  line-height: 1.55;
}

.reader-panel .version-warning svg {
  margin-top: 2px;
  color: var(--warning);
}

.reader-loading {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 8px 12px;
  padding-top: 38px;
  color: var(--text-secondary);
}

.reader-loading > svg {
  margin-top: 2px;
  color: var(--accent);
}

.reader-loading div {
  display: grid;
  gap: 2px;
}

.reader-loading strong {
  color: var(--text-primary);
  font-size: var(--fs-sm);
}

.reader-loading span:not(.reader-skeleton) {
  font-size: var(--fs-xs);
}

/* shimmer 皮肤来自 motion.css 的共享 .skeleton-line（模板上两个类并用），
   这里只声明阅读层的摆放几何。 */
.reader-skeleton {
  grid-column: 1 / -1;
  width: 100%;
  height: 12px;
  margin-top: 8px;
}

.reader-skeleton:nth-of-type(3n) {
  width: 78%;
}

.reader-error {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 15px;
  padding-top: 42px;
}

.reader-error-icon {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  color: var(--danger);
  background: var(--danger-soft);
}

.reader-error h3 {
  color: var(--text-primary);
  font-size: var(--fs-lg);
  letter-spacing: 0;
}

.reader-error p {
  max-width: 48ch;
  margin-top: 5px;
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  line-height: 1.65;
}

/* 描边、字号、高度都归 BaseButton 的 outline + sm。留在这里的只有与错误正文的间距。 */
.reader-retry {
  margin-top: 17px;
}

.reader-article {
  padding-top: 32px;
}

.reader-evidence {
  margin-top: 24px;
  padding: 14px 16px;
  border-left: 3px solid var(--accent);
  background: var(--surface-base);
  font-size: var(--fs-sm);
  line-height: 1.65;
}
.reader-evidence h3 {
  font-size: var(--fs-sm);
}
.reader-evidence .evidence-source {
  margin-top: 5px;
  color: var(--text-secondary);
  overflow-wrap: anywhere;
}
.reader-evidence blockquote {
  max-height: 250px;
  overflow: auto;
  margin-top: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.reader-evidence .evidence-note {
  margin-top: 10px;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
}

.reader-article > p {
  max-width: 76ch;
  overflow-wrap: anywhere;
  color: var(--text-secondary);
  font-size: var(--fs-base);
  line-height: 1.95;
  white-space: pre-wrap;
}

.reader-enter-active,
.reader-leave-active {
  transition: opacity var(--duration-normal) var(--ease-out-smooth);
}

.reader-enter-active .reader-panel,
.reader-leave-active .reader-panel {
  transition: transform var(--duration-normal) var(--ease-out-smooth);
}

.reader-enter-from,
.reader-leave-to {
  opacity: 0;
}

.reader-enter-from .reader-panel,
.reader-leave-to .reader-panel {
  transform: translateX(100%);
}

/* @keyframes shimmer 见 styles/components/motion.css。 */

@container (max-width: 760px) {
  .reader-panel {
    width: 100%;
    min-width: 0;
  }

  .reader-header {
    min-height: 62px;
    padding-right: 14px;
    padding-left: 20px;
  }

  .reader-scroll {
    padding: 27px 20px 60px 24px;
  }

  .reader-title-block h2 {
    font-size: var(--fs-2xl);
  }

  .reader-article > p {
    font-size: var(--fs-base);
    line-height: 1.85;
  }
}
</style>
