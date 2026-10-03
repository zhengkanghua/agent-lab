<script setup lang="ts">
import { LoaderCircle } from '@lucide/vue'

/* 转圈图标。
 *
 * 只包一层是为了把「用哪个图标、转多快、无障碍怎么说」定在一处：原来 4 个组件
 * 各自 import LoaderCircle 再各自挂 .spin，其中一处计时还差了 100ms。
 *
 * @keyframes spin 定义在 styles/components/motion.css 的层外（关键帧名全局、
 * 不随 @layer 分层），这里只引用不重复定义。
 *
 * 无 label 时对读屏隐藏：外层通常已有 aria-busy 或状态文案，再念一遍「加载中」是噪音。
 * 这段说明写在这里而不是 template 里——模板里的注释会让组件变成多根，
 * 外部就拿不到根元素的属性了。
 */

withDefaults(defineProps<{ size?: number; label?: string }>(), {
  size: 16,
  label: undefined,
})
</script>

<template>
  <LoaderCircle
    class="base-spinner"
    :size="size"
    :aria-hidden="label === undefined || undefined"
    :aria-label="label"
    :role="label === undefined ? undefined : 'status'"
  />
</template>

<style scoped>
.base-spinner {
  flex: none;
  animation: spin 900ms linear infinite;
}

@media (prefers-reduced-motion: reduce) {
  /* 不停掉动画——它在传达「还在跑」。只放慢到不刺眼。
   *
   * 这两条必须带 !important，否则等于没写：style.css 的全局 reduce 块用
   * `* { animation-duration: .01ms !important; animation-iteration-count: 1 !important }`
   * 兜底所有动画，而带 !important 的声明恒定压过不带它的声明（与选择器特异度无关）。
   * 原来它们不带，于是「放慢」从未生效，转圈被冻成一个静止图标——
   * 恰好丢掉这个组件唯一要传达的东西。两条都要写：只改时长的话，
   * @keyframes 仍会在跑完第一圈（iteration-count: 1）后停住。
   * 全局那条的 `*` 特异度是 0，本组件是 (0,1,0)，同为重要声明时这里胜出。 */
  .base-spinner {
    animation-duration: 2s !important;
    animation-iteration-count: infinite !important;
  }
}
</style>
