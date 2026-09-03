/* 纯前端 route mock 的共享数据与匹配函数：dev-screenshot.mjs 与 dev-audit.mjs 共用。
 * 字段与后端 openapi.json / src/api/* 契约一致。契约变化时此处要跟着前端 openapi.ts 更新。 */

export const SUPERUSER = {
  id: '10000000-0000-4000-8000-000000000001',
  email: 'admin@example.com',
  is_active: true,
  is_superuser: true,
  is_verified: true,
  is_environment_admin: true,
  created_at: '2026-08-17T00:00:00Z',
  updated_at: '2026-08-17T00:00:00Z',
}

const ENV_ADMIN = { ...SUPERUSER }
const REGULAR_USER = {
  id: '20000000-0000-4000-8000-000000000001',
  email: 'reader@example.com',
  is_active: true,
  is_superuser: false,
  is_verified: true,
  is_environment_admin: false,
  created_at: '2026-08-18T00:00:00Z',
  updated_at: '2026-08-18T00:00:00Z',
}

const BEST_MATCH = {
  chunk_id: '10000000-0000-4000-8000-000000000001',
  score: 0.91,
  page_content: '央行在季度例会上重申将根据经济运行情况择机调整利率，保持流动性合理充裕。',
  chunk_index: 0,
  chunk_count: 2,
}

const DOCUMENT_RESULT = {
  document_id: '20000000-0000-4000-8000-000000000001',
  content_hash: 'a'.repeat(64),
  title: '央行：将根据经济运行情况择机调整利率',
  url: 'https://example.com/news/1',
  source_name: '财经观察',
  published_at: '2026-08-20T09:30:00Z',
  authors: ['张明'],
  labels: ['宏观', '货币'],
  chunk_count: 2,
  best_score: BEST_MATCH.score,
  best_match: BEST_MATCH,
  additional_matches: [],
}

const CHUNK_RESULT = {
  chunk_id: BEST_MATCH.chunk_id,
  score: 0.82,
  page_content: BEST_MATCH.page_content,
  document_id: DOCUMENT_RESULT.document_id,
  content_hash: DOCUMENT_RESULT.content_hash,
  chunk_index: 0,
  chunk_count: 2,
  title: DOCUMENT_RESULT.title,
  url: DOCUMENT_RESULT.url,
  published_at: DOCUMENT_RESULT.published_at,
  source_updated_at: null,
  document_type: 'article',
  source_id: '30000000-0000-4000-8000-000000000001',
  source_provider: 'test',
  source_name: DOCUMENT_RESULT.source_name,
  source_external_id: 'feed/1',
  document_external_id: 'article/1',
  authors: DOCUMENT_RESULT.authors,
  labels: DOCUMENT_RESULT.labels,
  previous_chunk_id: null,
  next_chunk_id: null,
  embedding_model: 'bge-m3:567m',
}

const THREADS = {
  items: [
    {
      thread_id: '30000000-0000-4000-8000-000000000001',
      title: '最近央行对利率的表态？',
      created_at: '2026-08-20T08:00:00Z',
      last_active_at: '2026-08-20T08:10:00Z',
    },
    {
      thread_id: '30000000-0000-4000-8000-000000000002',
      title: '房地产政策有哪些新动向',
      created_at: '2026-08-19T10:00:00Z',
      last_active_at: '2026-08-19T10:20:00Z',
    },
  ],
  total: 2,
}

const AGENT_SSE_FRAMES = [
  { event: 'token', text: '这是模拟回答。由于本次开发不连接后端，下面的文字来自本地 mock。' },
  { event: 'token', text: '\n\n基于检索到的新闻原文，我整理了三条要点供你参考。' },
  {
    event: 'tool_call',
    tool_call_id: 'call_1',
    tool: 'research_documents',
    arguments: { query: '央行 利率' },
  },
  {
    event: 'tool_result',
    tool_call_id: 'call_1',
    tool: 'research_documents',
    content: '央行：将根据经济运行情况择机调整利率……',
    failed: false,
  },
  { event: 'done', thread_id: '30000000-0000-4000-8000-000000000099' },
]
  .map((obj) => `data: ${JSON.stringify(obj)}`)
  .join('\n\n')

const DOC_DETAIL = {
  document_id: DOCUMENT_RESULT.document_id,
  content_hash: DOCUMENT_RESULT.content_hash,
  title: DOCUMENT_RESULT.title,
  url: DOCUMENT_RESULT.url,
  source_name: DOCUMENT_RESULT.source_name,
  published_at: DOCUMENT_RESULT.published_at,
  authors: DOCUMENT_RESULT.authors,
  labels: DOCUMENT_RESULT.labels,
  document_text: '这里是文档全文正文……',
}

function json(body, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(body) }
}

/** 根据请求 URL 返回 mock 响应；未命中返回 null。authed 决定 /auth/me 是否返回已登录用户。 */
export function matchApi(url, authed) {
  const path = new URL(url).pathname // 形如 /api/auth/me
  const suffix = path.replace(/^\/api/, '')
  const unauth = {
    status: 401,
    contentType: 'application/json',
    body: JSON.stringify({ detail: 'not authenticated', code: 'authentication_required' }),
  }
  if (suffix === '/auth/me') return authed ? json(SUPERUSER) : unauth
  if (suffix === '/auth/login') return { status: 204, contentType: 'text/plain', body: '' }
  if (suffix === '/auth/logout') return { status: 204, contentType: 'text/plain', body: '' }
  if (suffix === '/admin/users') return json([ENV_ADMIN, REGULAR_USER])
  if (suffix === '/document-search') return json([DOCUMENT_RESULT])
  if (suffix === '/scheduled-jobs')
    return json([
      {
        id: '40000000-0000-4000-8000-000000000001',
        key: 'sync_news',
        task_type: 'freshrss_sync',
        cron_expr: '0 * * * *',
        params: {},
        enabled: true,
        next_run_at: '2026-08-20T09:00:00Z',
        last_run: null,
        created_at: '2026-08-17T00:00:00Z',
        updated_at: '2026-08-17T00:00:00Z',
      },
    ])
  if (suffix === '/vector-search') return json([CHUNK_RESULT])
  if (suffix === '/agent/default-prompt')
    return json({ system_prompt: '你是新闻语义检索助手，请基于检索到的原文作答。' })
  if (suffix.startsWith('/agent/threads') && suffix !== '/agent/threads') {
    if (path.endsWith('/messages')) return json({ items: [] })
    return json({ thread_id: '30000000-0000-4000-8000-000000000001' })
  }
  if (suffix === '/agent/threads') return json(THREADS)
  if (suffix === '/agent/chat')
    return { status: 200, contentType: 'text/event-stream', body: AGENT_SSE_FRAMES }
  if (suffix.startsWith('/documents/')) return json(DOC_DETAIL)
  return null
}
