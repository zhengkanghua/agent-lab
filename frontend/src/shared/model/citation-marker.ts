/* 引用标记（`[[E0123456789ab]]`）的唯一定义。
 *
 * 两个使用者：`shared/ui/SafeMarkdown.vue` 把答案正文里的标记渲染成可点的小丸，
 * `features/agent-chat/model/citation-excerpt.ts` 按同一个标记去工具轨迹里认回当初那段正文。
 * 两处一旦漂移，表现是点了小丸却静默回落成「打开当前全文」，而且不会有任何报错，
 * 所以它只允许有一份来源。
 *
 * 放在 shared 而不是 feature 里：两个使用者分属 shared 与 feature，按本仓库的依赖方向
 * （shared 是被依赖的底层，不能反过来 import feature），底层那一侧不能去 feature 取。
 *
 * 给的是「模式字符串 + 工厂函数」而不是直接导出 RegExp：带 `g` 标志的正则对象自带 lastIndex
 * 状态，被两处（甚至同一处的多次 matchAll）复用时会从上次停下的位置继续找，表现为漏匹配。
 */
export const CITATION_MARKER_PATTERN = '\\[\\[(E[0-9a-f]{12})\\]\\]'

/** 每次调用给一个干净的、带 `g` 的新正则，供 `matchAll` 之类的全局遍历使用。 */
export function citationMarkerRegExp(): RegExp {
  return new RegExp(CITATION_MARKER_PATTERN, 'g')
}
