<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { RefreshCw } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import BaseInput from '@/shared/ui/BaseInput.vue'
import BaseSelect from '@/shared/ui/BaseSelect.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import { useUsage } from '../composables/useUsage'
import {
  formatDuration,
  formatOccurredAt,
  formatTokens,
  localDayEndExclusiveIso,
  localDayStartIso,
} from '../model/usage'

/**
 * 设置中心 · 用量分区。
 *
 * 三段：筛选（时间范围与模型）、汇总数字、调用明细。筛选只有一份状态，汇总与明细都从它派生，
 * 所以改任一个筛选两处一起变——不是两次独立请求各管各的。
 *
 * 日期控件收的是**本地日期**，发请求前才换算成 UTC 时刻（换算集中在 `model/usage.ts`）：
 * 后端只按 UTC 时刻比较，而用户想的是「3 月 1 日到 5 日」。
 *
 * 不做图表、不做「今日」「本周」预置口径、不做后台视角——那些都在本期范围之外。
 */
const dates = reactive({ start: '', end: '' })
const model = ref('')

const filters = computed(() => ({
  model: model.value,
  start: localDayStartIso(dates.start),
  end: localDayEndExclusiveIso(dates.end),
}))

const {
  summary,
  records,
  models,
  state,
  errorMessage,
  hasMore,
  hasPrevious,
  isEmpty,
  nextPage,
  previousPage,
  refresh,
} = useUsage(filters)
</script>

<template>
  <section class="usage" aria-labelledby="usage-heading">
    <h2 id="usage-heading" class="section-heading">用量</h2>
    <p class="section-intro">
      这是你自己的模型调用账本：每一次调用一行，成功与失败都记。刚问完的那次可能还没出现——
      写入先入队、按秒批量落库，最多约一秒延迟。
    </p>

    <div class="filters">
      <BaseField id="usage-start" label="起始日期" hint="含这一天（按你的本地时区）。">
        <template #default="{ control }">
          <BaseInput v-bind="control" v-model="dates.start" type="date" />
        </template>
      </BaseField>

      <BaseField id="usage-end" label="结束日期" hint="含这一天（按你的本地时区）。">
        <template #default="{ control }">
          <BaseInput v-bind="control" v-model="dates.end" type="date" />
        </template>
      </BaseField>

      <BaseField id="usage-model" label="模型" hint="选项来自你实际用过的模型。">
        <template #default="{ control }">
          <BaseSelect v-bind="control" v-model="model">
            <option value="">全部模型</option>
            <option v-for="name in models" :key="name" :value="name">{{ name }}</option>
          </BaseSelect>
        </template>
      </BaseField>
    </div>

    <BaseCallout
      v-if="state === 'error'"
      class="load-error"
      tone="danger"
      :description="errorMessage"
    />

    <p v-else-if="state === 'loading'" class="loading">
      <BaseSpinner :size="15" label="正在读取用量" />
      正在读取用量…
    </p>

    <template v-else>
      <dl class="totals">
        <div class="total">
          <dt>调用次数</dt>
          <dd>{{ formatTokens(summary?.callCount ?? 0) }}</dd>
        </div>
        <div class="total">
          <dt>输入 token</dt>
          <dd>{{ formatTokens(summary?.inputTokens ?? 0) }}</dd>
        </div>
        <div class="total">
          <dt>输出 token</dt>
          <dd>{{ formatTokens(summary?.outputTokens ?? 0) }}</dd>
        </div>
        <div class="total">
          <dt>缓存 token</dt>
          <dd>{{ formatTokens(summary?.cachedTokens ?? null) }}</dd>
        </div>
        <div class="total">
          <dt>合计 token</dt>
          <dd>{{ formatTokens(summary?.totalTokens ?? 0) }}</dd>
        </div>
      </dl>
      <p class="totals-note">
        缓存那一列只累加上游报过的调用；显示「—」表示这段时间上游一次都没报缓存， 与「报了
        0」不是一回事。输入 token 已经包含缓存命中的部分，三者不能相加当消耗。
      </p>

      <div v-if="isEmpty" class="empty-state">
        <h3>这段时间没有调用</h3>
        <p>换一个时间范围，或者去掉模型筛选再看。</p>
      </div>

      <template v-else>
        <table class="records">
          <caption class="visually-hidden">
            用量明细
          </caption>
          <thead>
            <tr>
              <th scope="col">发生时刻</th>
              <th scope="col">模型</th>
              <th scope="col">状态</th>
              <th scope="col" class="numeric">输入</th>
              <th scope="col" class="numeric">输出</th>
              <th scope="col" class="numeric">缓存</th>
              <th scope="col" class="numeric">合计</th>
              <th scope="col" class="numeric">耗时</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="record in records" :key="record.callId">
              <td class="occurred">{{ formatOccurredAt(record.occurredAt) }}</td>
              <td>{{ record.modelName ?? '—' }}</td>
              <td>
                <span class="status" :class="{ 'is-failed': record.status === 'failed' }">
                  {{ record.status === 'failed' ? '失败' : '完成' }}
                </span>
              </td>
              <td class="numeric">{{ formatTokens(record.inputTokens) }}</td>
              <td class="numeric">{{ formatTokens(record.outputTokens) }}</td>
              <td class="numeric">{{ formatTokens(record.cachedTokens) }}</td>
              <td class="numeric">{{ formatTokens(record.totalTokens) }}</td>
              <td class="numeric">{{ formatDuration(record.durationMs) }}</td>
            </tr>
          </tbody>
        </table>

        <div class="pager">
          <BaseButton variant="ghost" size="sm" :disabled="!hasPrevious" @click="previousPage">
            上一页
          </BaseButton>
          <BaseButton variant="ghost" size="sm" :disabled="!hasMore" @click="nextPage">
            下一页
          </BaseButton>
          <BaseButton variant="ghost" size="sm" @click="refresh">
            <template #icon><RefreshCw :size="14" aria-hidden="true" /></template>
            刷新
          </BaseButton>
        </div>
      </template>
    </template>
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
  max-width: 52ch;
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  line-height: 1.7;
}

.filters {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
  gap: var(--space-4);
  max-width: 40rem;
  margin-bottom: var(--space-6);
}

.load-error {
  max-width: 32rem;
}

.loading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-tertiary);
  font-size: var(--fs-sm);
}

.totals {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(6.5rem, 1fr));
  gap: var(--space-3);
  margin: 0;
  padding: var(--space-4);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  background: var(--surface-raised);
}

.total dt {
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
}

.total dd {
  margin: 2px 0 0;
  color: var(--text-primary);
  font-size: var(--fs-lg);
  font-weight: var(--fw-bold);
  font-variant-numeric: tabular-nums;
}

.totals-note {
  margin: var(--space-3) 0 var(--space-6);
  max-width: 52ch;
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
  line-height: 1.7;
}

.empty-state {
  padding: var(--space-6) var(--space-4);
  border: 1px dashed var(--border-subtle);
  border-radius: var(--radius-lg);
  text-align: center;
}

.empty-state h3 {
  margin: 0 0 4px;
  font-size: var(--fs-base);
  font-weight: var(--fw-bold);
}

.empty-state p {
  margin: 0;
  color: var(--text-tertiary);
  font-size: var(--fs-sm);
}

.records {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-sm);
  font-variant-numeric: tabular-nums;
}

.records th,
.records td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--border-subtle);
  text-align: left;
  white-space: nowrap;
}

.records th {
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
}

.records .numeric {
  text-align: right;
}

.records .occurred {
  color: var(--text-secondary);
}

.status.is-failed {
  color: var(--danger);
}

.pager {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-4);
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}
</style>
