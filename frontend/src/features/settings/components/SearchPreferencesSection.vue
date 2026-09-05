<script setup lang="ts">
import { computed } from 'vue'
import { ListFilter, Layers3 } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
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
 * 埋在输入条 popover 里既不可发现、也不能持久。设置页里改动即生效并落在本浏览器。
 */
const { preferences, resetSearchPreferences } = usePreferences()

const documentLimit = computed({
  get: () => String(preferences.documentLimit),
  set: (value: string) => {
    preferences.documentLimit = Number(value)
  },
})

const matchesPerDocument = computed({
  get: () => String(preferences.matchesPerDocument),
  set: (value: string) => {
    preferences.matchesPerDocument = Number(value)
  },
})

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
      这两个参数是全局默认，影响之后每一次检索。更改立即生效，并自动保存在当前浏览器。
    </p>

    <div class="field-group">
      <BaseField
        id="pref-document-limit"
        label="每次检索返回的新闻数量"
        hint="一次检索覆盖多少篇不同的新闻。数量越多，单次检索越慢。"
      >
        <template #default="{ control }">
          <BaseSelect v-bind="control" v-model="documentLimit" class="narrow-select">
            <option v-for="option in DOCUMENT_LIMIT_OPTIONS" :key="option" :value="option">
              {{ option }} 篇
            </option>
          </BaseSelect>
        </template>
      </BaseField>

      <BaseField
        id="pref-matches-per-document"
        label="每篇新闻保留的相关片段"
        hint="折叠面板里每篇新闻最多展开多少条原文片段。"
      >
        <template #default="{ control }">
          <BaseSelect v-bind="control" v-model="matchesPerDocument" class="narrow-select">
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
        :disabled="!hasCustomized"
        @click="resetSearchPreferences"
      >
        <template #icon><ListFilter :size="15" aria-hidden="true" /></template>
        恢复默认（10 篇 · 每篇 3 条）
      </BaseButton>
      <span class="footer-note">
        <Layers3 :size="13" aria-hidden="true" />
        检索输入条右下角可随时跳回这里调整
      </span>
    </div>
  </section>
</template>

<style scoped>
.section-heading {
  margin: 0 0 var(--space-3);
  color: var(--text-primary);
  font-size: var(--text-2xl);
  font-weight: 760;
}

.section-intro {
  margin: 0 0 var(--space-6);
  max-width: 46ch;
  color: var(--text-secondary);
  font-size: 0.88rem;
  line-height: 1.7;
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
  font-size: 0.74rem;
}

@media (max-width: 560px) {
  .section-footer {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-2);
  }
}
</style>
