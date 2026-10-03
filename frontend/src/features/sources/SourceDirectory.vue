<script setup lang="ts">
import { Check, RefreshCw, Rss } from '@lucide/vue'
import type { SourceDto } from '@/api/sources'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseIconButton from '@/shared/ui/BaseIconButton.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import { useSources } from './useSources'

const {
  items,
  knowledgeBaseOptions,
  loadError,
  loading,
  refreshing,
  refresh,
  feedback,
  actionError,
  binding,
  changeBinding,
} = useSources()

function bindingValue(item: SourceDto): string {
  return item.knowledge_base_id ?? ''
}

function bindingOptions(item: SourceDto) {
  return knowledgeBaseOptions.value.filter(
    (option) => option.is_active || option.id === item.knowledge_base_id,
  )
}

function missingBinding(item: SourceDto): boolean {
  return (
    item.knowledge_base_id !== null &&
    !knowledgeBaseOptions.value.some((option) => option.id === item.knowledge_base_id)
  )
}

function onBindingChange(event: Event, item: SourceDto): void {
  const select = event.target as HTMLSelectElement
  const requested = select.value === '' ? null : select.value
  // 先恢复显示值：成功时缓存更新驱动重渲染，失败时行内保持原绑定。
  select.value = bindingValue(item)
  void changeBinding(item, requested)
}

function formatCheckpoint(item: SourceDto): string {
  if (item.sync_checkpoint_updated_at === null) return '未同步'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(item.sync_checkpoint_updated_at))
}
</script>

<template>
  <section class="source-directory" aria-label="来源目录">
    <div class="directory-toolbar">
      <div class="directory-count">
        <Rss :size="20" aria-hidden="true" />
        <span
          >外部来源 <strong>{{ loading ? '-' : items.length }}</strong></span
        >
      </div>
      <div class="toolbar-actions">
        <BaseIconButton label="刷新来源" :disabled="refreshing || binding" @click="refresh()">
          <RefreshCw :size="17" aria-hidden="true" />
        </BaseIconButton>
      </div>
    </div>

    <p v-if="feedback" class="feedback" role="status">
      <Check :size="16" aria-hidden="true" />{{ feedback }}
    </p>
    <BaseCallout v-if="actionError" tone="danger" :description="actionError" />
    <BaseCallout v-if="loadError" tone="danger">
      <span>{{ loadError }}</span>
      <template #actions>
        <BaseButton size="sm" variant="outline" :disabled="refreshing" @click="refresh()">
          <template #icon><RefreshCw :size="15" aria-hidden="true" /></template>
          重试
        </BaseButton>
      </template>
    </BaseCallout>
    <div v-if="loading" class="directory-state" role="status">
      <BaseSpinner :size="20" />正在读取来源
    </div>
    <div v-else-if="!loadError && items.length === 0" class="directory-state">
      暂无来源，等待同步发现订阅
    </div>

    <table v-if="items.length" class="source-table" :aria-busy="refreshing">
      <caption class="sr-only">
        外部来源列表，包括订阅地址、知识库绑定和同步状态
      </caption>
      <thead>
        <tr>
          <th scope="col">来源</th>
          <th scope="col">订阅地址</th>
          <th scope="col">知识库绑定</th>
          <th scope="col">同步游标</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in items" :key="item.id" :data-source-id="item.id">
          <td class="name-cell">
            <strong>{{ item.name }}</strong>
            <p>
              <code>{{ item.provider }}/{{ item.external_id }}</code>
            </p>
          </td>
          <td class="feed-cell">
            <a
              v-if="item.home_url"
              class="text-link"
              :href="item.home_url"
              target="_blank"
              rel="noreferrer"
            >
              {{ item.home_url }}
            </a>
            <span v-else>未提供地址</span>
          </td>
          <td class="binding-td">
            <!-- flex 排在里层的 div 上，不能直接写在 <td> 上：td 一旦变成 flex
                 容器就脱离表格布局，盒子高度由内容（约 81px）决定而不是行高（约 92px），
                 它那条 border-bottom 会比同排其他单元格高出一截，整行的分隔线在
                 「知识库绑定」这一列断成两段并错开——看起来像表格画坏了。
                 单元格自己保持 table-cell，垂直居中交给 td 的 vertical-align。 -->
            <div class="binding-cell">
              <span class="cell-label">知识库绑定</span>
              <select
                :value="bindingValue(item)"
                :aria-label="`绑定来源 ${item.name}`"
                :disabled="binding"
                @change="onBindingChange($event, item)"
              >
                <option value="">未配置</option>
                <option v-if="missingBinding(item)" :value="item.knowledge_base_id!" disabled>
                  {{ item.knowledge_base_key ?? item.knowledge_base_id }}（信息不可用）
                </option>
                <option
                  v-for="option in bindingOptions(item)"
                  :key="option.id"
                  :value="option.id"
                  :disabled="!option.is_active"
                >
                  {{ option.name }}{{ !option.is_active ? '（已停用）' : '' }}
                </option>
              </select>
              <span v-if="item.knowledge_base_id === null" class="unbound-badge">待配置</span>
            </div>
          </td>
          <td class="checkpoint-cell">
            <span class="cell-label">同步游标</span>
            {{ formatCheckpoint(item) }}
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.source-directory {
  container-type: inline-size;
  letter-spacing: 0;
}
/* 目录工具栏（.directory-toolbar / .toolbar-actions / .directory-count 及其图标、
   数字，含窄容器换行）归共享层 directory.css——与知识库目录那份逐字相同。 */
.feedback {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--success);
  font-size: var(--fs-sm);
  overflow-wrap: anywhere;
  padding: 16px 0;
}
.feedback svg {
  flex: 0 0 auto;
}
/* .directory-state 归共享层：styles/components/directory.css（四个目录页同一套）。 */
.source-table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
}
th {
  padding: 18px 12px;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  text-align: left;
  border-bottom: 1px solid var(--border-subtle);
}
th:first-child {
  width: 30%;
  padding-left: 0;
}
th:nth-child(2) {
  width: 26%;
}
th:nth-child(3) {
  width: 26%;
}
th:last-child {
  width: 18%;
  padding-right: 0;
}
td {
  padding: 20px 12px;
  border-bottom: 1px solid var(--border-subtle);
  vertical-align: middle;
}

/* 行悬停：账号目录与任务目录两行都有，这两张表漏了。四列表摊开在 1400px 上，
   没有行底就等于没有横向参考线，从「来源」扫到右边的绑定下拉要自己数格子。
   行本身不可点（下拉才是入口），所以只用底色，不做指针。 */
.source-table tbody tr {
  transition: background-color var(--duration-fast) var(--ease-out-smooth);
}

.source-table tbody tr:hover {
  background: var(--surface-sunken);
}
.name-cell {
  padding-left: 0;
  overflow-wrap: anywhere;
}
.name-cell strong {
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
}
.name-cell p {
  margin-top: 5px;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
}
.name-cell code {
  font-family: var(--mono-font);
}
/* 三态色与下划线归共享层 text-link.css。这里只留这一格的增量：小一号字，
   长 URL 允许在任意位置断开。 */
.feed-cell a {
  font-size: var(--fs-xs);
  overflow-wrap: anywhere;
}
.feed-cell span {
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
}
.binding-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}
/* 绑定格里的下拉没有走 BaseSelect，是权衡后的例外，不是漏网：它要在后端确认
   之前一直显示旧绑定（保存失败就不显示用户刚选的），靠 onBindingChange 里
   「把原生值拨回去、等缓存回流」这一段实现——BaseSelect 没有这层受控回退。
   其余取值与 BaseSelect 逐项对齐（圆角原是 --radius-lg=16px，全站其他下拉都是
   10px，同一个控件在两张表里长得不一样；悬停加深一档、聚焦描边转强调色、
   状态变化有过渡，原本只有一条全局聚焦环，划过去毫无回应）。 */
.binding-cell select {
  min-width: 0;
  flex: 1 1 auto;
  max-width: 200px;
  padding: 8px 10px;
  font-size: var(--fs-sm);
  color: var(--text-primary);
  background: var(--surface-raised, transparent);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  transition: border-color var(--duration-fast) var(--ease-out-smooth);
}
.binding-cell select:where(:not(:disabled):hover) {
  border-color: var(--border-strong);
}
.binding-cell select:focus-visible {
  border-color: var(--accent);
}
.binding-cell select:disabled {
  cursor: wait;
  opacity: 0.7;
}
.unbound-badge {
  flex: 0 0 auto;
  padding: 3px 8px;
  border-radius: var(--radius-pill);
  color: var(--warning, var(--text-secondary));
  border: 1px solid var(--border-subtle);
  font-size: var(--fs-xs);
  white-space: nowrap;
}
.checkpoint-cell {
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  padding-right: 0;
  text-align: right;
}

/* 表头在窄屏整行隐藏之后，同步游标那一格只剩一个「09/08 08:00」：读者无从判断它是
   同步游标、创建时间还是最后更新时间，而这三件事指向的操作完全不同。移动态补一枚
   只在那时出现的标签（与账号目录 UserAccountRow 的 .cell-label 同一做法，
   宽度条件与表头消失严格对齐，见文件末尾的容器查询）。 */
.cell-label {
  display: none;
}
@container (max-width: 580px) {
  .source-table thead {
    display: none;
  }
  .source-table tbody,
  .source-table tr {
    display: block;
  }
  .source-table tr {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 8px 12px;
    padding: 18px 0;
    border-bottom: 1px solid var(--border-subtle);
  }
  .source-table td {
    padding: 0;
    border: 0;
  }
  .name-cell {
    grid-column: 1;
    grid-row: 1;
  }
  .feed-cell {
    grid-column: 1;
    grid-row: 2;
  }
  /* 排布落在单元格身上（tr 变成 grid 之后 td 才是 grid item），
     里层的 .binding-cell 只管 flex 排布。 */
  .binding-td {
    grid-column: 1 / -1;
    grid-row: 3;
  }
  .checkpoint-cell {
    grid-column: 2;
    grid-row: 1;
    text-align: right;
  }

  /* 表头没了，两个数据格的语义靠这一枚标签扛起来。 */
  .cell-label {
    display: block;
    color: var(--text-tertiary);
    font-weight: var(--fw-semibold);
  }

  /* 标签自己占一行，下拉另起一行：并排时「知识库绑定」会和 200px 的下拉抢宽度，
     390px 下必然把下拉挤到比标签还窄。 */
  .binding-cell {
    flex-wrap: wrap;
  }

  .binding-cell .cell-label {
    flex: 0 0 100%;
  }
}
</style>
