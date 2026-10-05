/* 浮层定位的纯计算：知道触发键和面板各多大、视口多大，算出面板该落在哪。
 *
 * 为什么要有这一层：这两个选择器（知识库范围、Agent 模型）原来都把面板写成
 * `position: absolute; top: calc(100% + 4px)`，也就是**永远朝下展开**。它们挂在
 * 检索页 / Agent 页贴底的输入坞里，坞停在视口底边——面板于是整块落到屏幕外面。
 * 2026-10 实测：范围面板 58px 高，只有 17px 露在视口内（71% 被切掉），
 * 用户点开什么都看不见。
 *
 * 「永远朝下」这个假设在多数位置都成立，只有靠近底边时崩掉；而靠近底边恰恰是
 * 输入坞的常态。所以判据不是「朝上还是朝下」，而是**哪边放得下**：下方余量不够
 * 就翻到上方，两边都不够（面板比可用高度还高）才夹进视口。
 *
 * 2026-10 又收了第二个消费方进来：Agent 答案里引用小丸的悬停预览卡。它是纯 CSS 的
 * `::after` 浮层，挂在正文中间，被 `table { overflow-x: auto }` 这类祖先裁掉一半——
 * 和上面是同一类毛病。它需要的偏好与下拉不同（朝上、以触发点为中线），所以那两个
 * 偏好是参数，不是写死的。
 *
 * 这里只有算术，没有 DOM 也没有 Vue——量尺寸、挂监听在 usePopoverPanel 里。
 * 分开是为了让翻转与夹取这两个最容易写错的边界能被直接喂数字测出来。
 */

/** 视口坐标系里触发键的样子。字段名取自 getBoundingClientRect()，读起来不用换算。 */
export interface PopoverRect {
  top: number
  bottom: number
  left: number
  right: number
}

export interface PopoverSize {
  width: number
  height: number
}

export interface PopoverViewport {
  width: number
  height: number
}

export interface PopoverPosition {
  /** 视口坐标，直接写进面板 position: fixed 的 top / left。 */
  top: number
  left: number
}

/** 优先展开到触发键的哪一侧。放不下、且另一侧更宽裕时才翻过去。 */
export type PopoverPrefer = 'below' | 'above'

/** 水平对齐方式：start 与触发键左对齐，center 以触发键中线为准。 */
export type PopoverAlign = 'start' | 'center'

export interface PopoverPositionInput {
  trigger: PopoverRect
  panel: PopoverSize
  viewport: PopoverViewport
  /** 面板与触发键之间的空隙。 */
  gap: number
  /** 面板与视口边缘的最小距离，四条边都算。 */
  margin: number
  /* 两个偏好各有真实的第二种消费方，不是预留：下拉面板（知识库范围、Agent 模型）
     坐在输入坞底栏、以自己左缘对齐，默认的 below + start 就是它们；
     引用悬停卡挂在正文中间的一枚小丸上，习惯上朝上、并以小丸为中线。 */
  prefer?: PopoverPrefer
  align?: PopoverAlign
}

function clamp(value: number, min: number, max: number): number {
  // max 可能小于 min（面板比可用空间还大），先抬到 min 再取小，保证结果不小于 min。
  return Math.min(Math.max(value, min), Math.max(min, max))
}

export function computePopoverPosition(input: PopoverPositionInput): PopoverPosition {
  const { trigger, panel, viewport, gap, margin } = input

  /* 翻转判据用「余量够不够」，不是「会不会越界」：贴着视口边只剩 2px 的余地也算放得下，
     但面板的四角会顶到边。留出 margin 之后，优先侧放得下就用优先侧。 */
  const roomBelow = viewport.height - trigger.bottom - gap - margin
  const roomAbove = trigger.top - gap - margin
  const prefer = input.prefer ?? 'below'
  const preferredRoom = prefer === 'below' ? roomBelow : roomAbove
  const alternateRoom = prefer === 'below' ? roomAbove : roomBelow
  // 两侧都不够时留在优先侧，交给下面的夹取——翻过去也只是从「压住上边」变成「压住下边」。
  const flip = panel.height > preferredRoom && alternateRoom > preferredRoom
  const opensUpward = prefer === 'below' ? flip : !flip

  const preferredTop = opensUpward ? trigger.top - gap - panel.height : trigger.bottom + gap
  /* 兜底夹取：两边都放不下时（面板比视口可用高度还高）宁可让面板压住触发键，
     也不能让它跑到屏幕外——跑到屏幕外的部分没有任何办法看到。 */
  const top = clamp(preferredTop, margin, viewport.height - panel.height - margin)

  /* 水平：start 与触发键左对齐，右边放不下就改成右对齐（面板右缘贴触发键右缘）；
     center 以触发键中线为准。两边都放不下才夹进视口——夹取是最后手段，
     因为它会让面板与触发键错位。 */
  const preferredLeft =
    input.align === 'center'
      ? (trigger.left + trigger.right) / 2 - panel.width / 2
      : trigger.left + panel.width + margin > viewport.width
        ? trigger.right - panel.width
        : trigger.left
  const left = clamp(preferredLeft, margin, viewport.width - panel.width - margin)

  return { top, left }
}
