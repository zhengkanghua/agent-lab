<script setup lang="ts">
import type { DirectoryStats } from '../model/user-account'

/* 账号概况。三个数字加一句常驻说明，只读，不带任何操作。 */

defineProps<{ stats: DirectoryStats }>()
</script>

<template>
  <section class="account-summary" aria-label="账号概况" style="container-type: inline-size">
    <span>
      <strong>{{ stats.total }}</strong>
      全部账号
    </span>
    <span>
      <strong>{{ stats.active }}</strong>
      启用
    </span>
    <span>
      <strong>{{ stats.superusers }}</strong>
      超级用户
    </span>
    <span class="summary-note">密码与会话仅保存在服务端</span>
  </section>
</template>

<style scoped>
.account-summary {
  display: flex;
  align-items: center;
  gap: 28px;
  min-height: 57px;
  border-bottom: 1px solid var(--border-subtle);
  color: var(--text-secondary);
  font-family: var(--mono-font);
  font-size: 0.68rem;
}

/* 每一格是一个 flex 行盒：数字与说明之间的距离由 gap 给出，
   所以模板里可以把它们分行写，不必贴在一起靠空白符维持间距。 */
.account-summary span {
  display: inline-flex;
  align-items: baseline;
  gap: 7px;
  white-space: nowrap;
}

.account-summary strong {
  color: var(--text-primary);
  font-size: 1.08rem;
}

.summary-note {
  margin-left: auto;
  color: var(--text-tertiary);
}

@container (max-width: 720px) {
  .account-summary {
    display: grid;
    grid-template-columns: repeat(3, auto);
    gap: 10px 18px;
    padding: 13px 0;
  }

  .summary-note {
    grid-column: 1 / -1;
    margin-left: 0;
  }
}

@container (max-width: 430px) {
  .account-summary {
    grid-template-columns: repeat(2, auto);
  }
}
</style>
