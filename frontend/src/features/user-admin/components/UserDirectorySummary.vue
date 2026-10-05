<script setup lang="ts">
import type { DirectoryStats } from '../model/user-account'

/* 账号概况。三个数字加一句常驻说明，只读，不带任何操作。
 *
 * `pending` 堵的是一个「先给错数字」的窗口：这三个数由目录列表算出来，列表没读回来时
 * 它是三个 0，而概况是无条件渲染的——加载中用户会读到「0 全部账号 / 0 启用 / 0 超级用户」，
 * 读回后才跳成真实数字。先闪一个错值比转圈更糟：转圈是「等一下」，闪 0 是「这里没账号」。
 *
 * 数字位改渲染破折号，与用量页「上游没报这一项」用同一个符号——它说的是「这个数我们
 * 还没有」，不是「这个数是 0」。标签与布局不动，所以读回时也不会有跳动。
 * 读失败同样落进 pending：那时我们一样没有这三个数。
 */
withDefaults(defineProps<{ stats: DirectoryStats; pending?: boolean }>(), { pending: false })
</script>

<template>
  <section class="account-summary" aria-label="账号概况" style="container-type: inline-size">
    <span>
      <strong>{{ pending ? '—' : stats.total }}</strong>
      全部账号
    </span>
    <span>
      <strong>{{ pending ? '—' : stats.active }}</strong>
      启用
    </span>
    <span>
      <strong>{{ pending ? '—' : stats.superusers }}</strong>
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
  font-size: var(--fs-xs);
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
  font-size: var(--fs-lg);
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
