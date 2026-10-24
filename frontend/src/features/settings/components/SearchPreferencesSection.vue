<script setup lang="ts">
import { computed, ref } from 'vue'
import { ListFilter, Layers3 } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import BaseSelect from '@/shared/ui/BaseSelect.vue'
import {
  DEFAULT_PREFERENCES,
  DOCUMENT_LIMIT_OPTIONS,
  MATCHES_PER_DOCUMENT_OPTIONS,
} from '../model/preferences'
import { usePreferences } from '../composables/usePreferences'

/**
 * 设置中心 · 检索偏好分区。
 *
 * 数量参数从检索输入条的折叠区迁来：它们本来就是全局一份、影响之后所有检索的设置，
 * 埋在输入条 popover 里既不可发现、也不能持久。设置页里改动即生效并保存在账号上。
 *
 * 每次改动立即提交，没有保存按钮——两个下拉就是两个值，加一个显式的「保存」只会让人
 * 忘记点。提交失败时把本地值退回改动前那一份并给出提示，避免界面显示一个没存进去的值。
 *
 * **读成功之前两个下拉都禁用。** 提交是整体覆盖，此时 store 里装的是默认值，改一下就会把
 * 账号上的真实配置（另一个数量参数）一起覆盖成默认——用户只是调了个下拉，另一个却被改回去了。
 * 加载中禁用是暂时的，读失败则要说明原因，否则用户会以为页面坏了。
 *
 * **加载中不另发一条「正在读取」的提示**（原来这里是这么写的，理由成立：那只是一瞬间，
 * 闪一句比不说更烦）。但下拉里那几个数字得撤掉——它们此刻是契约默认值，摆在
 * 「每次检索返回的文档数量」下面就是一句关于用户设置的假话。数字位改成一个破折号，
 * 与用量页「上游没报这一项」同一个符号，控件本来就是灰的，不需要再解释一句。
 *
 * **读失败给「重试」。** 只有刷新整个页面才能重来是不合理的：这次失败多半是一次网络抖动，
 * 而用户已经在这个分区里了。`load()` 在 failed 状态下会真的重发请求（ready 时才短路）。
 */
const { preferences, loadState, canEdit, save, load } = usePreferences()

const saveError = ref('')

/* 重试期间单独记一个标志，而不是直接看 loadState：重试一开始 loadState 就变成 loading，
   那条「读失败」的说明会连同重试键一起消失，用户点完反而看不到任何反馈。
   只有这个键自己发起的加载才算「正在重试」。 */
const retrying = ref(false)

async function retryLoad(): Promise<void> {
  retrying.value = true
  try {
    await load()
  } finally {
    retrying.value = false
  }
}

const showLoadFailure = computed(() => loadState.value === 'failed' || retrying.value)

/** 提交一份新值；失败则回滚并提示。 */
async function commit(patch: Partial<{ documentLimit: number; matchesPerDocument: number }>) {
  const previous = {
    documentLimit: preferences.documentLimit,
    matchesPerDocument: preferences.matchesPerDocument,
  }
  Object.assign(preferences, patch)
  saveError.value = ''
  try {
    await save({ ...preferences })
  } catch {
    Object.assign(preferences, previous)
    saveError.value = '保存失败，请稍后重试。'
  }
}

const documentLimit = computed({
  get: () => String(preferences.documentLimit),
  set: (value: string) => {
    void commit({ documentLimit: Number(value) })
  },
})

const matchesPerDocument = computed({
  get: () => String(preferences.matchesPerDocument),
  set: (value: string) => {
    void commit({ matchesPerDocument: Number(value) })
  },
})

/* 「恢复默认」键上的那串数字直接从 DEFAULT_PREFERENCES 拼出来，不写死。
   原来是手写的「10 篇 · 每篇 3 条」：默认值一旦调整，按钮文案不会跟着变，
   于是它在按钮上撒谎——而按钮本身管的就是「恢复成哪两个数」。 */
const defaultSummary = `${DEFAULT_PREFERENCES.documentLimit} 篇 · 每篇 ${DEFAULT_PREFERENCES.matchesPerDocument} 条`

/** 两个数量参数一起恢复默认。 */
function resetSearchDefaults(): void {
  void commit({
    documentLimit: DEFAULT_PREFERENCES.documentLimit,
    matchesPerDocument: DEFAULT_PREFERENCES.matchesPerDocument,
  })
}

const hasCustomized = computed(
  () =>
    preferences.documentLimit !== DEFAULT_PREFERENCES.documentLimit ||
    preferences.matchesPerDocument !== DEFAULT_PREFERENCES.matchesPerDocument,
)
</script>

<template>
  <section class="search-prefs" aria-labelledby="search-prefs-heading">
    <h2 id="search-prefs-heading" class="section-heading">检索偏好</h2>
    <p class="section-intro">
      这两个参数是全局默认，影响之后每一次检索。更改立即生效，并保存在你的账号上，
      换浏览器或换设备登录都保持一致。
    </p>

    <BaseCallout v-if="saveError" class="save-error" tone="danger" :description="saveError" />

    <!-- 读失败时说明原因：控件是灰的，不说一句用户会以为页面坏了。加载中不提示，
         那只是一瞬间，闪一句「正在读取」比什么都不说更烦人。 -->
    <BaseCallout v-if="showLoadFailure" class="load-error" tone="danger">
      <span>读取账号偏好失败，暂时不能修改。</span>
      <template #actions>
        <BaseButton variant="outline" size="sm" :loading="retrying" @click="retryLoad">
          重试
        </BaseButton>
      </template>
    </BaseCallout>

    <div class="field-group">
      <BaseField
        id="pref-document-limit"
        label="每次检索返回的文档数量"
        hint="一次检索覆盖多少篇不同的文档。数量越多，单次检索越慢。"
      >
        <template #default="{ control }">
          <BaseSelect
            v-bind="control"
            v-model="documentLimit"
            :disabled="!canEdit"
            class="narrow-select"
          >
            <!-- 读到之前不摆一个数字：此刻 store 里装的是契约默认值，摆出来就是一句
                 关于用户设置的假话。破折号说的是「还没有」，不是「是 10」。 -->
            <option v-if="!canEdit" :value="documentLimit">—</option>
            <option v-for="option in DOCUMENT_LIMIT_OPTIONS" :key="option" :value="option">
              {{ option }} 篇
            </option>
          </BaseSelect>
        </template>
      </BaseField>

      <BaseField
        id="pref-matches-per-document"
        label="每篇文档保留的相关片段"
        hint="折叠面板里每篇文档最多展开多少条原文片段。"
      >
        <template #default="{ control }">
          <BaseSelect
            v-bind="control"
            v-model="matchesPerDocument"
            :disabled="!canEdit"
            class="narrow-select"
          >
            <option v-if="!canEdit" :value="matchesPerDocument">—</option>
            <option v-for="option in MATCHES_PER_DOCUMENT_OPTIONS" :key="option" :value="option">
              {{ option }} 条
            </option>
          </BaseSelect>
        </template>
      </BaseField>
    </div>

    <div class="section-footer">
      <BaseButton
        variant="ghost"
        size="sm"
        :disabled="!canEdit || !hasCustomized"
        @click="resetSearchDefaults"
      >
        <template #icon><ListFilter :size="15" aria-hidden="true" /></template>
        恢复默认（{{ defaultSummary }}）
      </BaseButton>
      <span class="footer-note">
        <Layers3 :size="13" aria-hidden="true" />
        检索输入条左下角可随时跳回这里调整
      </span>
    </div>
  </section>
</template>

<style scoped>
.section-heading {
  margin: 0 0 var(--space-3);
  color: var(--text-primary);
  font-size: var(--fs-2xl);
  font-weight: var(--fw-bold);
}

.section-intro {
  margin: 0 0 var(--space-6);
  max-width: 46ch;
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  line-height: 1.7;
}

/* 与下拉同宽，别在窄栏里拉成一条通栏横幅。 */
.load-error {
  max-width: 26rem;
  margin-bottom: var(--space-5);
}

.field-group {
  display: grid;
  gap: var(--space-5);
  max-width: 26rem;
}

/* 参数就两个，下拉不需要占满整栏：窄一点更像一个「值」，而不是一行表格。 */
.narrow-select {
  max-width: 11rem;
}

.section-footer {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin-top: var(--space-6);
}

.footer-note {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
}

@media (max-width: 560px) {
  .section-footer {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-2);
  }
}
</style>
