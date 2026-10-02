/**
 * 输入上界的展示规则：超上限、临近上限、常态三档。
 *
 * 三处曾经各写一遍同一组阈值——Agent 输入条、检索输入条各一个 computed，
 * 设置页那段提示词直接在 :class 里写 `remainingCharacters < 200`。改一处就得记得
 * 另外两处，而漏掉的表现是「字数变红了但样式没跟上」这类只有肉眼看才发现的问题。
 *
 * 阈值 200 是「还能再写一小段」的经验值，不是业务约束：真正的上界由各自
 * MAX_*_CHARACTERS 决定（见 agent-validation / search-validation）。
 */

/** 剩余字数少于它就算临近上限，胶囊转成提醒色。 */
export const NEAR_LIMIT_CHARACTERS = 200

/** 返回字数胶囊的档位类名（与 styles/components/character-count.css 的两个修饰类对应）。 */
export function counterTone(remainingCharacters: number): string {
  if (remainingCharacters < 0) return 'is-over'
  if (remainingCharacters < NEAR_LIMIT_CHARACTERS) return 'is-near'
  return ''
}
