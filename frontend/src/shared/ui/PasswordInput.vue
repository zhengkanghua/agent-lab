<script setup lang="ts">
import { ref } from 'vue'
import { Eye, EyeOff } from '@lucide/vue'
import BaseIconButton from './BaseIconButton.vue'
import BaseInput from './BaseInput.vue'

/* 带「显示密码」开关的输入框。
 *
 * 密码框里只看得到一排圆点，敲错了自己发现不了——这是登录、改密、建号最常见的挫败源。
 * 登录页早就做了这枚开关，而设置中心的改密码、后台的建号与重置密码三处都没有：
 * 同一个人在同一个产品里，一处能核对，另一处不能，而且没有任何理由说明为什么。
 *
 * 所以把「密码框 + 开关」收成一份。开关的图标、aria 文案、以及它和输入框的尺寸耦合
 * 都只写在这里，四个调用方不再各写一遍。
 *
 * 尺寸耦合：开关是 BaseIconButton 的 sm（34px），输入框 42px，上右各内缩 4px，
 * 4 + 34 + 4 = 42 才正好嵌在框里。输入框右侧因此要留出 42px 内边距。
 * 触屏下 BaseIconButton 会把 sm 撑到 44px，框也要跟着撑高，否则开关会溢出边框。
 *
 * 用法与 BaseInput 一致，配合 BaseField 的 control 插槽：
 *   <BaseField v-slot="{ control }" label="密码">
 *     <PasswordInput v-bind="control" v-model="password" autocomplete="current-password" />
 *   </BaseField>
 * 除 v-model 与 disabled 外，其余属性（id / name / autocomplete / required / aria-*）
 * 都经 attrs 透传给输入框。这里不提供 type：明文/密文由开关自己切换。
 */

// 根节点是 <span>，attrs 必须全部转到输入框上；不关掉自动继承，
// id / aria-describedby 会同时落到 span 上，读屏会把同一个 id 读两遍。
defineOptions({ inheritAttrs: false })

withDefaults(defineProps<{ modelValue?: string | number; disabled?: boolean }>(), {
  modelValue: '',
  disabled: false,
})

const emit = defineEmits<{ 'update:modelValue': [value: string | number] }>()

// 每次挂载都从密文开始：记住上一次的明文状态，等于在别人接手时先把密码摊开。
const visible = ref(false)
</script>

<template>
  <span class="password-input">
    <!-- :type 放在 v-bind 之后：attrs 里若有 type（调用方误传），也不能盖掉密文类型，
         否则密码会以明文显示。重复的键以靠后的为准。 -->
    <BaseInput
      :model-value="modelValue"
      :disabled="disabled"
      v-bind="$attrs"
      :type="visible ? 'text' : 'password'"
      @update:model-value="emit('update:modelValue', $event)"
    />
    <BaseIconButton
      class="password-toggle"
      size="sm"
      :disabled="disabled"
      :label="visible ? '隐藏密码' : '显示密码'"
      @click="visible = !visible"
    >
      <EyeOff v-if="visible" :size="17" aria-hidden="true" />
      <Eye v-else :size="17" aria-hidden="true" />
    </BaseIconButton>
  </span>
</template>

<style scoped>
.password-input {
  position: relative;
  display: block;
}

/* BaseInput 的圆角框在这里给开关让位：只收右侧内边距，与上面那组尺寸对齐。 */
.password-input :deep(.base-input) {
  padding-right: 42px;
}

.password-toggle {
  position: absolute;
  top: 4px;
  right: 4px;
}

/* 触屏：开关被 BaseIconButton 撑到 44px，而输入框默认只有 42px，会顶出边框。
   把框一并撑高到 44 + 上下各 4px，并同步加宽右侧让位的内边距。 */
@media (pointer: coarse) {
  .password-input :deep(.base-input) {
    height: calc(var(--tap-target) + 8px);
    padding-right: calc(var(--tap-target) + 12px);
  }
}
</style>
