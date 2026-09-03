<script setup lang="ts">
import { usePasswordChangeForm } from '../composables/usePasswordChangeForm'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'

const { form, validationErrors, canSubmit, submit, isPending, errorMessage, successMessage } =
  usePasswordChangeForm()

function getFieldError(field: string): string | undefined {
  return validationErrors.value.find((e) => e.field === field)?.message
}
</script>

<template>
  <form class="password-form" @submit.prevent="submit">
    <div class="form-header">
      <h2 class="form-title">修改密码</h2>
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
        <input
          v-bind="control"
          v-model="form.currentPassword"
          class="account-input"
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
        <input
          v-bind="control"
          v-model="form.newPassword"
          class="account-input"
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
        <input
          v-bind="control"
          v-model="form.confirmPassword"
          class="account-input"
          type="password"
          name="confirm-password"
          autocomplete="new-password"
          :disabled="isPending"
        />
      </BaseField>
    </div>

    <div v-if="errorMessage" class="message message-error" role="alert">
      {{ errorMessage }}
    </div>

    <div v-if="successMessage" class="message message-success" role="status">
      {{ successMessage }}
    </div>

    <BaseButton type="submit" :disabled="!canSubmit" class="submit-button">
      <BaseSpinner v-if="isPending" class="spinner" />
      <span>{{ isPending ? '提交中...' : '修改密码' }}</span>
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

/* 输入框样式与登录页 .login-input 同款：输入框没抽成基础组件（type/autocomplete
   属性面差别太大），各页面自带样式是仓库现状，见 LoginPage.vue 同名注释。 */
.account-input {
  width: 100%;
  height: 42px;
  padding: 0 12px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  background: var(--surface-raised);
  font-size: 0.84rem;
  font-weight: 450;
  outline: none;
  transition:
    border-color 140ms ease,
    box-shadow 140ms ease;
}

.account-input::placeholder {
  color: var(--text-tertiary);
}

.account-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 4px var(--accent-soft);
}

.account-input:disabled {
  color: var(--text-tertiary);
  background: var(--surface-sunken);
}

.message {
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
}

.message-error {
  background: var(--surface-error);
  color: var(--text-error);
}

.message-success {
  background: var(--surface-success);
  color: var(--text-success);
}

.submit-button {
  align-self: flex-start;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.spinner {
  width: 1rem;
  height: 1rem;
}
</style>
