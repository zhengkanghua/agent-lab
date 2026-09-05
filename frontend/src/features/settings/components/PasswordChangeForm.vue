<script setup lang="ts">
import { usePasswordChangeForm } from '../composables/usePasswordChangeForm'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import BaseInput from '@/shared/ui/BaseInput.vue'

const { form, validationErrors, canSubmit, submit, isPending, errorMessage, successMessage } =
  usePasswordChangeForm()

function getFieldError(field: string): string | undefined {
  return validationErrors.value.find((e) => e.field === field)?.message
}
</script>

<template>
  <form class="password-form" @submit.prevent="submit">
    <div class="form-header">
      <h3 class="form-title">修改密码</h3>
      <p class="form-description">修改成功后,其他设备的登录将自动失效。</p>
    </div>

    <div class="form-fields">
      <!-- 控件由本组件经插槽渲染：BaseField 只管外壳与 aria 接线（见其注释）。
           之前把 v-model/type 直接传给 BaseField，插槽没内容，页面上只剩三个
           标签、没有输入框，表单整个不可用。 -->
      <BaseField
        id="current-password"
        v-slot="{ control }"
        label="当前密码"
        :error="getFieldError('currentPassword')"
      >
        <BaseInput
          v-bind="control"
          v-model="form.currentPassword"
          type="password"
          name="current-password"
          autocomplete="current-password"
          :disabled="isPending"
        />
      </BaseField>

      <BaseField
        id="new-password"
        v-slot="{ control }"
        label="新密码"
        :error="getFieldError('newPassword')"
      >
        <BaseInput
          v-bind="control"
          v-model="form.newPassword"
          type="password"
          name="new-password"
          autocomplete="new-password"
          :disabled="isPending"
        />
      </BaseField>

      <BaseField
        id="confirm-password"
        v-slot="{ control }"
        label="确认新密码"
        :error="getFieldError('confirmPassword')"
      >
        <BaseInput
          v-bind="control"
          v-model="form.confirmPassword"
          type="password"
          name="confirm-password"
          autocomplete="new-password"
          :disabled="isPending"
        />
      </BaseField>
    </div>

    <BaseCallout v-if="errorMessage" tone="danger" :description="errorMessage" />

    <!-- role="status" 经 attrs 落到根元素：成功提示对读屏是礼貌播报，不是警报。 -->
    <BaseCallout v-if="successMessage" tone="info" role="status" :description="successMessage" />

    <!-- loading 期间文案保留，按钮宽度不跳动（见 BaseButton 注释）。 -->
    <BaseButton type="submit" class="submit-button" :loading="isPending" :disabled="!canSubmit">
      修改密码
    </BaseButton>
  </form>
</template>

<style scoped>
.password-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  max-width: 28rem;
}

.form-header {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.form-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.form-description {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  margin: 0;
}

.form-fields {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.submit-button {
  align-self: flex-start;
}
</style>
