/* eslint-disable no-console */
/*
 * 本地 mock API 服务器：把 dev-mocks.mjs 的模拟数据包成一个真的 HTTP 服务，
 * 让 `BACKEND_PROXY_TARGET=http://127.0.0.1:<port> npm run dev` 指向它，
 * 于是可以在自己的浏览器里完整操作前端（登录、检索、Agent 对话、设置中心、后台），
 * 而不启动 FastAPI/PostgreSQL/Ollama/Qdrant。
 *
 * 用法：
 *   node scripts/dev-mock-server.mjs [--port 8788]
 *   # 另开一个终端：
 *   BACKEND_PROXY_TARGET=http://127.0.0.1:8788 npm run dev
 *
 * 与 dev-screenshot.mjs 的 route mock 同一套数据（dev-mocks.mjs），并模拟了登录态：
 * 初始未登录（/auth/me 返回 401），任意邮箱密码提交 /auth/login 后变成超管 admin@example.com，
 * /auth/logout 后退回未登录。
 *
 * 边界：mock 数据只用于核验前端交互与样式，不能作为后端已更新或部署成功的依据；
 * SSE 是一次性整段到达，不代表真实流式节奏。
 */
import { createServer } from 'node:http'
import { matchApi } from './dev-mocks.mjs'

const arg = (flag) => {
  const i = process.argv.indexOf(flag)
  return i >= 0 ? Number(process.argv[i + 1]) : undefined
}
const PORT = arg('--port') || Number(process.env.MOCK_API_PORT) || 8788

let authed = false

createServer((req, res) => {
  // 浏览器/代理中途断开（比如取消 Agent 流、刷新页面）会触发 error 事件；
  // 不接住的话未处理的 'error' 会把整个 mock 进程打崩，表现为「突然全都 502」。
  req.on('error', () => {})
  res.on('error', () => {})

  const method = req.method ?? 'GET'

  // 登录/登出改写内存里的会话标志，让「登录 → 使用 → 退出」走真实的前端流程。
  if (method === 'POST' && req.url === '/auth/login') {
    authed = true
    res.writeHead(204).end()
    return
  }
  if (method === 'POST' && req.url === '/auth/logout') {
    authed = false
    res.writeHead(204).end()
    return
  }

  // matchApi 期望完整 URL 且路径带 /api 前缀（vite 代理已把 /api 剥掉，这里补回去）。
  const hit = matchApi(`http://localhost/api${req.url}`, authed)
  const respond = (status, contentType, body) => {
    if (res.destroyed || res.writableEnded) return
    res.writeHead(status, { 'content-type': contentType })
    res.end(body)
  }

  if (hit) {
    respond(hit.status, hit.contentType, hit.body)
    return
  }

  console.warn(`unhandled: ${method} ${req.url}`)
  respond(
    404,
    'application/json',
    JSON.stringify({ detail: 'mock 未覆盖该路由', code: 'not_found' }),
  )
}).listen(PORT, '127.0.0.1', () => {
  console.log(`mock api 已启动：http://127.0.0.1:${PORT}`)
  console.log('启动前端：BACKEND_PROXY_TARGET=http://127.0.0.1:' + PORT + ' npm run dev')
  console.log('浏览器里任意邮箱 + 任意密码即可登录为超管 admin@example.com。')
})
