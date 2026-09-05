<script setup lang="ts">
/* 全局只挂路由出口。历史上这里还挂过 ToastContainer，但全站没有任何业务调用
   useToast——一个零调用方的通知体系是死代码，随商业化重构一并移除（消融）。 */
</script>

<template>
  <RouterView />
</template>
