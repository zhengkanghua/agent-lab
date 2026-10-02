<script setup lang="ts">
import { computed } from 'vue'
import { Check } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import BaseInput from '@/shared/ui/BaseInput.vue'
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
    <!-- 标签/接线走 BaseField：aria-invalid 与 aria-describedby 由它算一次，
         不再各字段手写。 -->
    <BaseField id="reset-password" :label="`为 ${email} 设置新密码`">
      <template #default="{ control }">
        <BaseInput
          v-bind="control"
          v-model="passwordDraft"
          type="password"
          name="reset-password"
          autocomplete="new-password"
          :placeholder="passwordHint"
          :disabled="submitting"
        />
      </template>
    </BaseField>
    <BaseButton class="submit-command" variant="primary" type="submit" :loading="submitting">
      <template #icon><Check :size="16" aria-hidden="true" /></template>
      确认重置
    </BaseButton>
    <BaseButton class="cancel-command" variant="secondary" size="md" @click="emit('cancel')">
      取消
    </BaseButton>
    <BaseCallout v-if="error" class="editor-error" tone="danger" :description="error" />
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

/* 字段外壳（标签/错误/说明）归 BaseField；这里只剩本表单自己的排布。 */

/* 输入框皮肤在 BaseInput；取消键走 BaseButton（secondary），与提交键同高同源。 */
.editor-error {
  grid-column: 1 / -1;
}

@container (max-width: 720px) {
  .reset-editor {
    grid-template-columns: 1fr auto;
    padding: 15px 0 1px;
  }

  .base-field,
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
