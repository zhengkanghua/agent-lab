// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { createStaleGuard } from '../stale-guard'

/* 陈旧响应守卫的四条底线。
 *
 * 这里只断言可观察结果（`isCurrent()` 的答案、`signal.aborted`），不碰内部字段：序号与
 * 控制器是实现，换一种写法也该照样通过。
 *
 * 每条都指得出「什么观察能证明它是假的」：
 *  - 取令牌 → `isCurrent()` 为假或信号已 abort，说明 `begin()` 一上来就把自己作废了；
 *  - 再取一次 → 第一个仍 current，说明作废没有真的落到旧令牌上；
 *  - `cancel()` 之后仍 current 或信号没 abort，说明放弃没生效；
 *  - 陈旧令牌 release 之后 `cancel()` 掐不掉新一轮，说明 `release()` 里少了「仅当自己
 *    仍是当前那一轮」那句比对。
 */

describe('createStaleGuard', () => {
  it('取到令牌后本轮仍是当前那一轮，信号还没 abort', () => {
    const guard = createStaleGuard()

    const token = guard.begin()

    expect(token.isCurrent()).toBe(true)
    expect(token.signal.aborted).toBe(false)
  })

  it('再次取令牌后，第一个令牌不再是当前、信号已 abort，第二个仍是当前', () => {
    const guard = createStaleGuard()

    const first = guard.begin()
    const second = guard.begin()

    expect(first.isCurrent()).toBe(false)
    expect(first.signal.aborted).toBe(true)
    expect(second.isCurrent()).toBe(true)
    expect(second.signal.aborted).toBe(false)
  })

  it('放弃当前这一轮后，令牌不再是当前、信号已 abort', () => {
    const guard = createStaleGuard()
    const token = guard.begin()

    guard.cancel()

    expect(token.isCurrent()).toBe(false)
    expect(token.signal.aborted).toBe(true)
  })

  /* 陈旧令牌的 release() 不许误伤新一轮。
   *
   * 两个消费者里的表现是「老的一轮收尾时顺手把新一轮的控制器清掉」，之后那次 cancel()
   * 就 abort 不到任何人——界面上看不出异常，连接却留在那里。所以这里钉住 release() 里
   * 那句「仅当自己仍是当前这一轮」：删掉它，下面这条 `cancel()` 后的断言就会变红。 */
  it('陈旧令牌释放自己之后，新一轮的信号仍能被 cancel() abort 掉', () => {
    const guard = createStaleGuard()

    const first = guard.begin()
    const second = guard.begin()
    first.release()

    guard.cancel()

    expect(second.signal.aborted).toBe(true)
  })
})
