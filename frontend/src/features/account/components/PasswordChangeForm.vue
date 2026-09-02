<script setup lang="ts">
import { usePasswordChangeForm } from '../composables/usePasswordChangeForm'
import BaseButton from '../../../shared/ui/BaseButton.vue'
import BaseField from '../../../shared/ui/BaseField.vue'
import BaseSpinner from '../../../shared/ui/BaseSpinner.vue'

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
      <BaseField
        id="current-password"
        v-model="form.currentPassword"
        label="当前密码"
        type="password"
        :error="getFieldError('currentPassword')"
        :disabled="isPending"
        autocomplete="current-password"
      />

      <BaseField
        id="new-password"
        v-model="form.newPassword"
        label="新密码"
        type="password"
        :error="getFieldError('newPassword')"
        :disabled="isPending"
        autocomplete="new-password"
      />

      <BaseField
        id="confirm-password"
        v-model="form.confirmPassword"
        label="确认新密码"
        type="password"
        :error="getFieldError('confirmPassword')"
        :disabled="isPending"
        autocomplete="new-password"
      />
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
