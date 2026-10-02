import { computed, type ComputedRef } from 'vue'
import { counterTone } from '@/shared/model/character-count'

/**
 * 输入坞里的输入行为：草稿转发、Enter 提交、焦点归还、字数档位。
 *
 * Agent 输入条与检索输入条共用。这两处原先各写一份，连「输入法组合期间不提交」这条
 * 守卫都是抄过去的——那种守卫一旦在一侧改对、另一侧没跟上，表现是中文输入按回车
 * 把半个词发出去，只在特定输入法下复现。
 *
 * 分成这么多 getter 而不是直接收 prop，是因为两条输入条的提交条件和上界都不同
 * （Agent 看 canSend，检索看 loading/disabled），共用的是行为而不是判据。
 */
export interface ComposerInputOptions {
  /* 输入框的模板 ref，由调用方用 useTemplateRef 建好传进来。
     不由本函数创建：模板 ref 的名字属于模板，两边各持一份容易对不上；
     只读它的 value，所以收结构类型而不是 Ref，Ref 与 Readonly<ShallowRef> 都能传。 */
  inputRef: { readonly value: HTMLTextAreaElement | null }
  /** 受控草稿的当前值。 */
  value: () => string
  /** 草稿回写（通常是 emit('update:modelValue', v)）。 */
  onChange: (value: string) => void
  /** 此刻能不能提交。条件归调用方，两条输入条不同。 */
  canSubmit: () => boolean
  submit: () => void
  /** 剩余可输入字数，用于字数胶囊的配色档位。 */
  remainingCharacters: () => number
}

export interface ComposerInput {
  draft: ComputedRef<string>
  /** 字数胶囊的档位类名。 */
  tone: ComputedRef<string>
  onEnter: (event: KeyboardEvent) => void
  focusInput: () => void
}

export function useComposerInput(options: ComposerInputOptions): ComposerInput {
  const draft = computed({
    get: () => options.value(),
    set: (value: string) => options.onChange(value),
  })

  const tone = computed(() => counterTone(options.remainingCharacters()))

  /**
   * Enter 提交、Shift+Enter 换行。
   *
   * 输入法组合期间不能提交：中文输入按 Enter 是「确认候选词」，此时 isComposing 为真，
   * 不拦住会把半个词发出去。
   */
  function onEnter(event: KeyboardEvent): void {
    if (event.isComposing || event.shiftKey) return
    event.preventDefault()
    if (options.canSubmit()) options.submit()
  }

  /** 把光标送回输入框：提交后、落地页、「新对话」之后。 */
  function focusInput(): void {
    options.inputRef.value?.focus()
  }

  return { draft, tone, onEnter, focusInput }
}
