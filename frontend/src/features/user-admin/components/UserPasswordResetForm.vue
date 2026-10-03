<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Check } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import PasswordInput from '@/shared/ui/PasswordInput.vue'
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

const root = ref<HTMLFormElement | null>(null)

/* 打开就把焦点送进密码框。用户点「重置密码」本来就是为了输密码，让他再 Tab 一次是多余的；
   顺带解决 Esc——keydown 只在焦点位于表单内时才冒泡到这里，不送焦点的话
   「按 Esc 取消」等于没接线。取第一个可用输入框的写法与知识库编辑器一致。 */
onMounted(() => root.value?.querySelector<HTMLInputElement>('input:not([disabled])')?.focus())

const passwordDraft = computed({
  get: () => props.password,
  set: (value: string) => emit('update:password', value),
})

const passwordHint = `${PASSWORD_MIN_LENGTH}–${PASSWORD_MAX_LENGTH} 个字符`
</script>

<template>
  <!-- Esc 与「取消」同一条路径。监听挂在 form 上而不是 document：useModalLayer 的 Esc
       也是 document 级、且不拦截冒泡，挂 document 会在弹层开着时把两层一起关掉；
       挂在 form 上则只有焦点落在这个表单里才会触发。 -->
  <form
    ref="root"
    class="reset-editor"
    style="container-type: inline-size"
    @submit.prevent="emit('submit')"
    @keydown.esc="emit('cancel')"
  >
    <!-- 标签/接线走 BaseField：aria-invalid 与 aria-describedby 由它算一次，
         不再各字段手写。 -->
    <BaseField id="reset-password" :label="`为 ${email} 设置新密码`">
      <template #default="{ control }">
        <PasswordInput
          v-bind="control"
          v-model="passwordDraft"
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
