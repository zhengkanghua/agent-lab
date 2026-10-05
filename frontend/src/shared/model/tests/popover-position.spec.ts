// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { computePopoverPosition, type PopoverPositionInput } from '../popover-position'

/* 浮层落点的算术。
 *
 * 这些边界不是设想，是 2026-10 实测出来的：范围面板与模型面板原先都写死
 * `top: calc(100% + 4px)`，触发键坐在贴底的输入坞里（1440×900 视口、触发键底边 879），
 * 面板于是整块展开到视口外，范围面板 58px 里只有 17px 露出来。
 *
 * 所以这里钉的是三件事：**下方放不下要翻上去**、**两边都放不下要夹进视口**、
 * **水平方向不要溢出**。三个都用喂数字的方式测，不挂组件。
 * 断言的是「面板落在哪」这个可观察结果，不是内部选了哪条分支。
 */

const VIEWPORT = { width: 1440, height: 900 }

/** 视口正中偏下、贴底输入坞里的那枚胶囊：就是实测里触发键的位置。 */
const DOCK_TRIGGER = { top: 841, bottom: 879, left: 357, right: 617 }

/** 页面上半部分的一枚触发键，用来对照「下方放得下」的正常情况。 */
const TOP_TRIGGER = { top: 100, bottom: 138, left: 357, right: 617 }

function position(overrides: Partial<PopoverPositionInput> = {}) {
  return computePopoverPosition({
    trigger: DOCK_TRIGGER,
    panel: { width: 260, height: 58 },
    viewport: VIEWPORT,
    gap: 6,
    margin: 8,
    ...overrides,
  })
}

describe('computePopoverPosition', () => {
  it('下方空间够用时朝下展开，与触发键左对齐', () => {
    const result = position({
      trigger: TOP_TRIGGER,
      panel: { width: 260, height: 200 },
    })

    expect(result).toEqual({ top: 144, left: 357 })
  })

  /* 这条就是被修掉的那个 bug：触发键贴底，面板整块留在视口内。 */
  it('贴底时翻到触发键上方，整块留在视口内', () => {
    const result = position()

    // 面板底边在触发键上方留出 gap：841 - 6 = 835，再往上 58px。
    expect(result.top).toBe(777)
    expect(result.top + 58).toBe(835)
    expect(result.top).toBeGreaterThanOrEqual(8)
  })

  it('下方只差一点也翻上去，既不裁切也不贴边', () => {
    // 触发键底边 879，下方余量 900 - 879 - 6 - 8 = 7px，装不下 58px 的面板。
    const result = position({ panel: { width: 260, height: 58 } })

    expect(result.top + 58).toBeLessThanOrEqual(DOCK_TRIGGER.top - 6)
    expect(result.top).toBeGreaterThanOrEqual(8)
  })

  it('两边都放不下时夹进视口，不从顶部跑出去', () => {
    // 面板比视口可用高度还高：只能压住触发键，但四周仍留出 margin。
    const result = position({ panel: { width: 260, height: 1200 } })

    expect(result.top).toBe(8)
  })

  it('上方也不够时宁可朝下，不选更挤的那一侧', () => {
    // 触发键在页面上段，上下余量都装不下 700px 的面板，但下方更多。
    const result = position({
      trigger: TOP_TRIGGER,
      panel: { width: 260, height: 700 },
    })

    expect(result.top).toBeGreaterThanOrEqual(TOP_TRIGGER.bottom)
  })

  it('右边放不下就改成与触发键右对齐', () => {
    const result = position({
      trigger: { top: 100, bottom: 138, left: 1300, right: 1400 },
      panel: { width: 260, height: 100 },
    })

    expect(result.left).toBe(1400 - 260)
    expect(result.left + 260).toBeLessThanOrEqual(VIEWPORT.width - 8)
  })

  it('窄视口下面板比视口还宽时夹到左边距，不出现负坐标', () => {
    const result = position({
      trigger: { top: 100, bottom: 138, left: 20, right: 120 },
      panel: { width: 400, height: 100 },
      viewport: { width: 360, height: 720 },
    })

    expect(result.left).toBe(8)
    expect(result.top).toBeGreaterThanOrEqual(8)
  })

  /* 下面四条是引用悬停卡带来的第二个消费方：它习惯朝上、并以小丸为中线。
     偏好是参数而不是写死，正是因为它与下拉面板的方向不同。 */
  it('prefer=above 时朝上展开，即使下方还有余量', () => {
    const result = position({
      trigger: TOP_TRIGGER,
      panel: { width: 120, height: 40 },
      prefer: 'above',
    })

    // 上方余量 100 - 6 - 8 = 86，装得下 40px，所以不翻。
    expect(result.top).toBe(TOP_TRIGGER.top - 6 - 40)
  })

  it('prefer=above 但上方放不下时照样翻到下方', () => {
    const result = position({
      trigger: { top: 20, bottom: 58, left: 357, right: 617 },
      panel: { width: 120, height: 200 },
      prefer: 'above',
    })

    expect(result.top).toBe(58 + 6)
  })

  it('align=center 时以触发元素的中线为准', () => {
    const result = position({
      trigger: { top: 100, bottom: 138, left: 400, right: 420 },
      panel: { width: 200, height: 40 },
      align: 'center',
    })

    expect(result.left).toBe(410 - 100)
  })

  it('align=center 也会夹进视口，不出现负坐标', () => {
    const result = position({
      trigger: { top: 100, bottom: 138, left: 0, right: 8 },
      panel: { width: 300, height: 40 },
      align: 'center',
    })

    expect(result.left).toBe(8)
  })
})
