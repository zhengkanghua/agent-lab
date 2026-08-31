<script setup lang="ts">
import { computed } from 'vue'
import { Check, X } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseIconButton from '@/shared/ui/BaseIconButton.vue'
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from '../model/admin-validation'

/* 创建账号的表单。
 *
 * 密码是受控值而不是本组件的内部 ref：页面在退出登录成功后要清掉它，
 * 而组件内部的 ref 页面碰不到。
 */

const props = defineProps<{
  email: string
  password: string
  superuser: boolean
  error: string
  submitting: boolean
}>()

const emit = defineEmits<{
  'update:email': [value: string]
  'update:password': [value: string]
  'update:superuser': [value: boolean]
  submit: []
  close: []
}>()

const emailDraft = computed({
  get: () => props.email,
  set: (value: string) => emit('update:email', value),
})

const passwordDraft = computed({
  get: () => props.password,
  set: (value: string) => emit('update:password', value),
})

const superuserDraft = computed({
  get: () => props.superuser,
  set: (value: boolean) => emit('update:superuser', value),
})

const passwordHint = `${PASSWORD_MIN_LENGTH}–${PASSWORD_MAX_LENGTH} 个字符`
</script>

<template>
  <section class="create-editor" aria-labelledby="create-title">
    <div class="editor-heading">
      <div>
        <p>新账号</p>
        <h2 id="create-title">授予平台访问权限</h2>
      </div>
      <BaseIconButton
        label="关闭创建账号表单"
        busy-cursor
        :disabled="submitting"
        @click="emit('close')"
      >
        <X :size="18" aria-hidden="true" />
      </BaseIconButton>
    </div>

    <form class="create-form" novalidate @submit.prevent="emit('submit')">
      <label class="field-control">
        <span>账号邮箱</span>
        <input
          v-model="emailDraft"
          name="new-email"
          type="email"
          autocomplete="off"
          placeholder="name@example.com"
          :disabled="submitting"
        />
      </label>
      <label class="field-control">
        <span>初始密码</span>
        <input
          v-model="passwordDraft"
          name="new-password"
          type="password"
          autocomplete="new-password"
          :placeholder="passwordHint"
          :disabled="submitting"
        />
      </label>
      <label class="check-control">
        <input v-model="superuserDraft" type="checkbox" :disabled="submitting" />
        <span>
          <strong>超级用户</strong>
          <small>可管理账号并执行手动 Pipeline</small>
        </span>
      </label>
      <!-- loading 一并给出转圈、禁用、wait 指针与 aria-busy；原来手写只有前两样。 -->
      <BaseButton class="submit-command" variant="primary" type="submit" :loading="submitting">
        <template #icon><Check :size="17" aria-hidden="true" /></template>
        {{ submitting ? '正在创建' : '确认创建' }}
      </BaseButton>
      <p v-if="error" class="editor-error" role="alert">{{ error }}</p>
    </form>
  </section>
</template>

<style scoped>
.create-editor {
  padding: 25px 28px 27px;
  border-top: 3px solid var(--accent);
  border-bottom: 1px solid var(--border-subtle);
  background: var(--surface-base);
}

.editor-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}

.editor-heading p {
  color: var(--text-secondary);
  font-size: 0.74rem;
  font-weight: 760;
}

.editor-heading h2 {
  margin-top: 4px;
  font-size: 1.24rem;
  font-weight: 760;
}

.create-form {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr) minmax(200px, 0.8fr) auto;
  align-items: end;
  gap: 18px;
  margin-top: 22px;
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

.check-control {
  display: flex;
  align-items: center;
  min-height: 42px;
  gap: 10px;
  color: var(--text-secondary);
  font-size: 0.76rem;
}

.check-control input {
  width: 17px;
  height: 17px;
  accent-color: var(--accent);
}

.check-control span {
  display: grid;
  gap: 1px;
}

.check-control small {
  color: var(--text-muted);
  font-size: 0.67rem;
  font-weight: 450;
}

.editor-error {
  grid-column: 1 / -1;
  padding: 9px 11px;
  border-left: 3px solid var(--danger);
  color: var(--danger);
  background: var(--danger-soft);
  font-size: 0.76rem;
}

@media (max-width: 1040px) {
  .create-form {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .create-form .submit-command {
    justify-self: start;
  }
}

@media (max-width: 720px) {
  .create-editor {
    padding: 22px 17px 24px;
  }

  .create-form {
    grid-template-columns: 1fr;
  }
}
</style>
