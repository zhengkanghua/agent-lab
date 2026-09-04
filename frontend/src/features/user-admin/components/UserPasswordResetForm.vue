<script setup lang="ts">
import { computed } from 'vue'
import { Check } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from '@/shared/model/password'

/* 展开在某一行下方的密码重置表单。
 *
 * 它落在 .user-row 的网格里，占满整行——横跨列的那条声明归父组件，
 * 因为「占几列」是父网格的事；这里只管表单自己的内部排布。
 */

const props = defineProps<{
  email: string
  password: string
  error: string
  submitting: boolean
}>()

const emit = defineEmits<{
  'update:password': [value: string]
  submit: []
  cancel: []
}>()

const passwordDraft = computed({
  get: () => props.password,
  set: (value: string) => emit('update:password', value),
})

const passwordHint = `${PASSWORD_MIN_LENGTH}–${PASSWORD_MAX_LENGTH} 个字符`
</script>

<template>
  <form class="reset-editor" style="container-type: inline-size" @submit.prevent="emit('submit')">
    <label class="field-control">
      <span>为 {{ email }} 设置新密码</span>
      <input
        v-model="passwordDraft"
        name="reset-password"
        type="password"
        autocomplete="new-password"
        :placeholder="passwordHint"
        :disabled="submitting"
      />
    </label>
    <BaseButton class="submit-command" variant="primary" type="submit" :loading="submitting">
      <template #icon><Check :size="16" aria-hidden="true" /></template>
      确认重置
    </BaseButton>
    <button class="cancel-command" type="button" @click="emit('cancel')">取消</button>
    <p v-if="error" class="editor-error" role="alert">{{ error }}</p>
  </form>
</template>

<style scoped>
.reset-editor {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) auto auto;
  align-items: end;
  gap: 10px;
  /* 左边这 45px 是头像宽度加间距：让输入框与上一行的邮箱对齐，
     看起来像是从那一行长出来的，而不是另起一段。 */
  padding: 16px 0 3px 45px;
  border-top: 1px dashed var(--border-subtle);
}

.field-control {
  display: grid;
  gap: 7px;
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 700;
}

.field-control input {
  width: 100%;
  height: 42px;
  padding: 0 12px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  background: var(--surface-raised);
  font-size: 0.84rem;
  outline: none;
}

.field-control input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

/* 提交键的高度归 BaseButton（44px）。取消键跟着它对齐，否则 align-items: end
   的这一行两个按钮会差 2px。 */
.cancel-command {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  padding: 0 13px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: var(--surface-raised);
  font-size: 0.7rem;
  font-weight: 650;
}

.cancel-command:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.editor-error {
  grid-column: 1 / -1;
  padding: 9px 11px;
  border-left: 3px solid var(--danger);
  color: var(--danger);
  background: var(--danger-soft);
  font-size: 0.76rem;
}

@container (max-width: 720px) {
  .reset-editor {
    grid-template-columns: 1fr auto;
    padding: 15px 0 1px;
  }

  .field-control,
  .editor-error {
    grid-column: 1 / -1;
  }
}

@container (max-width: 430px) {
  .reset-editor {
    grid-template-columns: 1fr;
  }

  .submit-command,
  .cancel-command {
    width: 100%;
  }
}
</style>
