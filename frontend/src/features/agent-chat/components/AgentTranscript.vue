<script setup lang="ts">
import type { AgentTurn } from '../model/conversation'
import AgentTurnCard from './AgentTurnCard.vue'
import BaseSuggestionList from '@/shared/ui/BaseSuggestionList.vue'

const props = defineProps<{
  turns: AgentTurn[]
  streaming: boolean
  examples: readonly string[]
}>()

const emit = defineEmits<{
  retry: []
  'choose-example': [value: string]
}>()

/** 只有最后一轮能重发：重发的是「接着当前历史再问一次」，中间轮次没有这个语义。 */
function isLast(index: number): boolean {
  return index === props.turns.length - 1
}
</script>

<template>
  <section
    class="transcript"
    aria-labelledby="transcript-title"
    :aria-busy="streaming"
    style="container-type: inline-size"
  >
    <h2 id="transcript-title" class="sr-only">对话记录</h2>

    <!-- 空态只有一句标题 + 建议卡（Q9）。原来那个圆形图标是装饰性品牌元素，撤掉了：
         老板明确说暂时没有品牌。说明性长段落压成一句——「它会自己检索」这件事，
         点一张建议卡看它真的去查，比读一段字有说服力。
         「回答可能有误、请核对原文」那条挪到输入区下方的细则行：它对每一轮都成立，
         只挂在空态等于答案出现后就不再提醒。 -->
    <div v-if="turns.length === 0" class="empty-state">
      <h3>今天想查什么？</h3>
      <p class="empty-lead">Agent 会自己决定检索哪些新闻、要不要读全文，然后基于查到的内容作答。</p>

      <BaseSuggestionList
        class="example-list"
        :examples="examples"
        aria-label="示例提问"
        @select="emit('choose-example', $event)"
      />
    </div>

    <TransitionGroup v-else name="list" tag="div" class="turn-list">
      <AgentTurnCard
        v-for="(turn, index) in turns"
        :key="turn.id"
        :turn="turn"
        :can-retry="isLast(index) && !streaming"
        @retry="emit('retry')"
      />
    </TransitionGroup>
  </section>
</template>

<style scoped>
.turn-list {
  display: grid;
  gap: 16px;
}

/* 空态不再是一张虚线卡片：单列布局里它就是这一列的全部内容，再画个框等于给
   整页描边。改成无框，靠垂直居中把它托在输入区上方。 */
.empty-state {
  padding: 28px 12px 12px;
}

.empty-state h3 {
  color: var(--text-primary);
  font-size: 1.72rem;
  font-weight: 780;
  line-height: 1.2;
  letter-spacing: -0.01em;
}

.empty-lead {
  max-width: 44ch;
  margin-top: 12px;
  color: var(--text-secondary);
  font-size: 0.9rem;
  line-height: 1.7;
}

.example-list {
  margin-top: 26px;
}

@container (max-width: 560px) {
  .empty-state h3 {
    font-size: 1.5rem;
  }

  .example-list {
    margin-top: 20px;
  }
}
</style>
