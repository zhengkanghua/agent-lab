<script setup lang="ts">
import ConfirmDialog from '@/shared/ui/ConfirmDialog.vue'

/* 全局只挂路由出口，外加一个确认框宿主。
 *
 * 历史上这里还挂过 ToastContainer，但全站没有任何业务调用 useToast——一个零调用方的
 * 通知体系是死代码，随商业化重构一并移除（消融）。确认框不一样：它由 feature 里的
 * composable（删会话、注销账号、撤销会话）主动请求，是这些调用点唯一的视图层，
 * 因此必须挂在一个它们都能用到的地方。 */
</script>

<template>
  <RouterView />
  <ConfirmDialog />
</template>
