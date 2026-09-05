/* eslint-disable no-console */
/* 布局/结构审计：纯前端（route mock），核对渲染结果是否出现可见问题。
 * 用法与 scripts/dev-screenshot.mjs 相同（先起 dev server）。输出文本报告。 */
import { chromium } from 'playwright'
import { matchApi, SUPERUSER } from './dev-mocks.mjs'

const PORT = Number(process.env.DEVSHOT_PORT || 5173)
const BASE = `http://localhost:${PORT}`

let authed = true

const browser = await chromium.launch()
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
await context.route(
  (url) => url.pathname.startsWith('/api/'),
  (route) => {
    const hit = matchApi(route.request().url(), authed)
    if (hit)
      void route.fulfill({ status: hit.status, contentType: hit.contentType, body: hit.body })
    else void route.fulfill({ status: 404, contentType: 'application/json', body: '{}' })
  },
)

const errors = []
const page = await context.newPage()
page.on('console', (m) => {
  if (m.type() === 'error') errors.push(`console.error: ${m.text()}`)
})
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))

async function geo(sel) {
  const el = page.locator(sel).first()
  if (!(await el.count())) return null
  return el.boundingBox()
}

async function audit(name, url) {
  await page.goto(url, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(700)
  const metrics = await page.evaluate(() => {
    const de = document.documentElement
    return {
      scrollW: de.scrollWidth,
      innerW: window.innerWidth,
      bodyTextLen: (document.body.innerText || '').length,
    }
  })
  const overflowX = metrics.scrollW - metrics.innerW
  console.log(`\n=== ${name}  ${url}`)
  console.log(
    `  innerWidth=${metrics.innerW}  scrollWidth=${metrics.scrollW}  overflowX=${overflowX}${overflowX > 0 ? '  <-- 横向溢出!' : '  (ok)'}  bodyText=${metrics.bodyTextLen} chars`,
  )
}

// 1) 登录页（未登录态）
authed = false
await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(500)
console.log('=== 登录页')
console.log('  .login-tool visible:', await page.locator('.login-tool').first().isVisible())
console.log(
  '  登录按钮文本:',
  await page
    .locator('.login-tool button[type="submit"]')
    .first()
    .textContent()
    .catch(() => null),
)

// 已登录态后的各页审计
authed = true

// 2) 检索页
await audit('检索页·待输入', `${BASE}/`)
const navLabels = await page
  .locator('.topbar-nav-link')
  .evaluateAll((els) => els.map((e) => e.getAttribute('aria-label')))
console.log(
  '  顶栏功能链接:',
  JSON.stringify(navLabels),
  navLabels.includes('账号管理') ? '  <-- 不应有账号管理直达!' : '  (符合:无账号管理直达)',
)

// 3) 检索结果
await page.fill('textarea', '央行利率')
await page.click('.composer button[type="submit"]')
await page.waitForSelector('.result-card', { timeout: 8000 })
console.log('=== 检索结果')
console.log('  result-card 数量:', await page.locator('.result-card').count())
console.log(
  '  结果标题含数据:',
  (await page.locator('.result-card').first().innerText()).slice(0, 80).replace(/\n/g, ' / '),
)

// 4) Agent 页
await audit('Agent 对话页', `${BASE}/agent`)
console.log(
  '  顶栏功能链接:',
  JSON.stringify(
    await page
      .locator('.topbar-nav-link')
      .evaluateAll((e) => e.map((x) => x.getAttribute('aria-label'))),
  ),
)
console.log(
  '  会话侧栏项数:',
  await page.locator('.thread-rail [role="listitem"], .thread-rail li').count(),
)

// 5) 设置中心（旧 /account 已重定向并入）：三个分区各查一眼关键元素
await audit('设置中心·账号分区', `${BASE}/settings/account`)
console.log(
  '  账号分区标题可见:',
  await page
    .locator('#account-heading')
    .isVisible()
    .catch(() => false),
)
console.log('  改密表单字段数:', await page.locator('.password-form input').count())

await audit('设置中心·检索偏好', `${BASE}/settings/search`)
console.log(
  '  偏好下拉数:',
  await page.locator('#search-prefs-heading ~ * select, .field-group select').count(),
)

await audit('设置中心·Agent 偏好', `${BASE}/settings/agent`)
console.log(
  '  提示词编辑器可见:',
  await page
    .locator('.prompt-editor')
    .isVisible()
    .catch(() => false),
)

// 6) 后台·桌面
await audit('后台·账号管理(桌面)', `${BASE}/admin/users`)
const side = await geo('.admin-sidebar')
const main = await geo('.admin-main-wrap')
const top = await geo('.admin-topbar')
console.log(
  '  侧边栏:',
  side
    ? `${Math.round(side.x)},${Math.round(side.y)} ${Math.round(side.width)}x${Math.round(side.height)}`
    : 'MISSING',
)
console.log('  内容区:', main ? `x=${Math.round(main.x)} w=${Math.round(main.width)}` : 'MISSING')
if (side && main) {
  const overlap = side.x + side.width > main.x + 2
  console.log(
    '  侧边栏与内容区重叠?',
    overlap
      ? '是 <-- 问题!'
      : `否(ok) 侧栏右缘=${Math.round(side.x + side.width)},内容左缘=${Math.round(main.x)}`,
  )
}
console.log('  顶栏:', top ? `y=${Math.round(top.y)} h=${Math.round(top.height)}` : 'MISSING')
console.log('  [返回工作台] href:', await page.locator('.menu-back').first().getAttribute('href'))
console.log(
  '  账号管理菜单项高亮:',
  await page
    .locator('.menu-item.router-link-active')
    .first()
    .textContent()
    .catch(() => null),
)
console.log(
  '  用户目录行数:',
  await page.locator('[data-testid^="active-"], table tbody tr').count(),
)
console.log('  侧边栏账号管理链接数:', await page.locator('a[href="/admin/users"]').count())

// 7) 移动端·后台抽屉
const mob = await context.newPage()
mob.on('console', (m) => m.type() === 'error' && errors.push(`console.error(mob): ${m.text()}`))
mob.on('pageerror', (e) => errors.push(`pageerror(mob): ${e.message}`))
await mob.setViewportSize({ width: 390, height: 844 })
await mob.goto(`${BASE}/admin/users`, { waitUntil: 'domcontentloaded' })
await mob.waitForTimeout(600)
console.log('\n=== 后台·移动端(390px)')
console.log(
  '  抽屉默认关闭(is-open=false):',
  !(await mob.locator('.admin-sidebar').evaluate((e) => e.classList.contains('is-open'))),
)
await mob.locator('.menu-toggle button, button[aria-label="打开导航"]').first().click()
await mob.waitForTimeout(350)
console.log(
  '  点汉堡后 is-open=true:',
  await mob.locator('.admin-sidebar').evaluate((e) => e.classList.contains('is-open')),
)
console.log('  遮罩存在:', (await mob.locator('.sidebar-overlay').count()) > 0)

console.log('\n=== 控制台/页面错误 ===')
if (errors.length) errors.forEach((e) => console.log('  ✗', e))
else console.log('  无 (ok)')

await browser.close()
console.log('\ndone')
