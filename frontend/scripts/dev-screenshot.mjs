/* eslint-disable no-console */
/*
 * 纯前端可视化工具：不启动后端也能浏览 Signal Desk 的页面与样式。
 *
 * 用法：
 *   1) 先起前端 dev server：cd frontend && npm run dev（默认 5173）
 *   2) node scripts/dev-screenshot.mjs [--port 5173] [--out .devshots]
 *
 * 原理：用 Playwright 启动真实浏览器，把 /api/** 请求全部在本机拦截（route mock），
 * 返回与后端 openapi 契约一致的模拟数据，因此无需连接 FastAPI/PostgreSQL/Ollama/Qdrant。
 * 截图保存到 out 目录，供人工/视觉核验样式与流程。
 *
 * 边界：route mock 只用于隔离验证前端状态与样式，不能作为后端已更新或部署成功的依据。
 * mock 数据若与真实后端契约脱节（后端改字段/改路由），本脚本要跟着前端 openapi.ts 更新。
 */
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { chromium } from 'playwright'
import { matchApi } from './dev-mocks.mjs'

const __dirname = dirname(fileURLToPath(import.meta.url))
const arg = (flag) => {
  const i = process.argv.indexOf(flag)
  return i >= 0 ? process.argv[i + 1] : undefined
}
const PORT = Number(arg('--port') || process.env.DEVSHOT_PORT || 5173)
const OUT = resolve(__dirname, '..', arg('--out') || '.devshots')

const BASE = `http://localhost:${PORT}`
/* ---------- 截图流程 ---------- */

mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch()
let authed = true

async function shot(page, name, selector) {
  if (selector) await page.waitForSelector(selector, { timeout: 8000 })
  else await page.waitForTimeout(400)
  const file = resolve(OUT, `${name}.png`)
  await page.screenshot({ path: file, fullPage: true })
  console.log(`saved ${file}`)
}

const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
// 只拦截浏览器对 API 前缀的调用（/api/**），不能误伤 dev server 自己 /src/api 的源码请求。
await context.route(
  (url) => url.pathname.startsWith('/api/'),
  (route) => {
    const request = route.request()
    const hit = matchApi(request.url(), authed)
    if (hit) {
      void route.fulfill({ status: hit.status, contentType: hit.contentType, body: hit.body })
    } else {
      console.warn('unhandled api route:', request.method(), request.url())
      void route.fulfill({ status: 404, contentType: 'application/json', body: '{}' })
    }
  },
)
const page = await context.newPage()

try {
  // 1) 登录页（未登录态）
  authed = false
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
  await shot(page, '01-login', '.login-tool')

  // 2) 检索页 · 待输入
  authed = true
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' })
  await shot(page, '02-search-idle', '.composer')

  // 3) 检索页 · 结果
  await page.fill('textarea', '央行近期是否调整利率？')
  await page.click('.composer button[type="submit"]')
  await page.waitForSelector('.result-card', { timeout: 8000 })
  await page.waitForTimeout(400)
  const file = resolve(OUT, '03-search-results.png')
  await page.screenshot({ path: file, fullPage: true })
  console.log(`saved ${file}`)

  // 4) Agent 对话页
  await page.goto(`${BASE}/agent`, { waitUntil: 'domcontentloaded' })
  await shot(page, '04-agent', '.composer-dock')

  // 5) 账号设置页
  await page.goto(`${BASE}/account`, { waitUntil: 'domcontentloaded' })
  await shot(page, '05-account', '.page-title')

  // 6) 后台 · 账号管理（桌面）
  await page.goto(`${BASE}/admin/users`, { waitUntil: 'domcontentloaded' })
  await shot(page, '06-admin-users', '.admin-sidebar')

  // 7) 后台 · 移动端抽屉
  const mobile = await context.newPage()
  await mobile.setViewportSize({ width: 390, height: 844 })
  await mobile.goto(`${BASE}/admin/users`, { waitUntil: 'domcontentloaded' })
  await mobile.waitForSelector('.admin-sidebar')
  await mobile.waitForTimeout(300)
  await mobile.screenshot({ path: resolve(OUT, '07-admin-mobile-closed.png'), fullPage: true })
  console.log(`saved ${resolve(OUT, '07-admin-mobile-closed.png')}`)
  await mobile.click('.menu-toggle button, button[aria-label="打开导航"]')
  await mobile.waitForTimeout(400)
  await mobile.screenshot({ path: resolve(OUT, '08-admin-mobile-open.png'), fullPage: true })
  console.log(`saved ${resolve(OUT, '08-admin-mobile-open.png')}`)

  // 8) 检索页 · 移动端
  const mobileSearch = await context.newPage()
  await mobileSearch.setViewportSize({ width: 390, height: 844 })
  await mobileSearch.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' })
  await shot(mobileSearch, '09-search-mobile', '.composer')
} finally {
  await browser.close()
}
console.log('done')
