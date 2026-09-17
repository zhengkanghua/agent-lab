/* 纯前端 route mock 的共享数据与匹配函数：dev-screenshot.mjs 与 dev-audit.mjs 共用。
 * 字段与后端 openapi.json / src/api/* 契约一致。契约变化时此处要跟着前端 openapi.ts 更新。 */
import { createHash, randomUUID } from 'node:crypto'
import { matchTaskApi } from './task-mocks.mjs'

const NEWS_ID = '10000000-0000-4000-8000-000000000010'
const FILES_ID = '10000000-0000-4000-8000-000000000011'
const timestamp = '2026-09-08T00:00:00Z'
const KNOWLEDGE_BASES = [
  { id: NEWS_ID, key: 'news', name: '新闻', description: '新闻与行业动态', is_active: true },
  {
    id: FILES_ID,
    key: 'manuals',
    name: '技术资料',
    description: '运行手册与项目资料',
    is_active: true,
  },
  {
    id: '10000000-0000-4000-8000-000000000012',
    key: 'archive',
    name: '归档资料',
    description: null,
    is_active: false,
  },
].map((item) => ({ ...item, created_at: timestamp, updated_at: timestamp }))

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

/* 账号目录的可变副本。删除要真的从列表里消失，否则删完刷新一下又回来了，
   看的人会以为删除没生效。进程内保存，重启 mock 即还原。 */
const ACCOUNTS = [ENV_ADMIN, REGULAR_USER].map((user) => ({ ...user }))

/* 当前账号的个人偏好。字段名与后端一致（下划线），与账号目录同一个理由：
   保存后要能读回来，否则本地看起来像「保存没生效」。 */
const PREFERENCES = {
  system_prompt: null,
  document_limit: 10,
  matches_per_document: 3,
}

const BEST_MATCH = {
  chunk_id: '10000000-0000-4000-8000-000000000001',
  score: 0.91,
  page_content: '央行在季度例会上重申将根据经济运行情况择机调整利率，保持流动性合理充裕。',
  chunk_index: 0,
  chunk_count: 2,
}

/* 外部来源。后台的「来源管理」分区要读它，缺了会让整页停在失败态。
   一条已绑定知识库、一条未绑定，覆盖两种单元格形态。 */
const SOURCES = [
  {
    id: '30000000-0000-4000-8000-000000000001',
    provider: 'freshrss',
    external_id: 'feed/news',
    name: '新闻与行业动态',
    feed_url: 'https://example.com/news.xml',
    home_url: 'https://example.com/news',
    knowledge_base_id: NEWS_ID,
    knowledge_base_key: 'news',
    sync_checkpoint: '1740000000',
    sync_checkpoint_updated_at: timestamp,
  },
  {
    id: '30000000-0000-4000-8000-000000000002',
    provider: 'freshrss',
    external_id: 'feed/weekly',
    name: '行业周报',
    feed_url: 'https://example.com/weekly.xml',
    home_url: null,
    knowledge_base_id: null,
    knowledge_base_key: null,
    sync_checkpoint: null,
    sync_checkpoint_updated_at: null,
  },
]

const DOCUMENT_RESULT = {
  document_id: '20000000-0000-4000-8000-000000000001',
  knowledge_base_id: NEWS_ID,
  knowledge_base_name: '新闻',
  mime_type: 'text/plain',
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
  knowledge_base_id: NEWS_ID,
  mime_type: 'text/plain',
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

const DOC_DETAIL = {
  document_id: DOCUMENT_RESULT.document_id,
  knowledge_base_id: NEWS_ID,
  knowledge_base_name: '新闻',
  mime_type: 'text/plain',
  revision: 1,
  content_hash: DOCUMENT_RESULT.content_hash,
  title: DOCUMENT_RESULT.title,
  url: DOCUMENT_RESULT.url,
  source_name: DOCUMENT_RESULT.source_name,
  published_at: DOCUMENT_RESULT.published_at,
  authors: DOCUMENT_RESULT.authors,
  labels: DOCUMENT_RESULT.labels,
  content_text: '央行在季度例会上重申将根据经济运行情况择机调整利率，保持流动性合理充裕。',
}

const FILE_DOCUMENT = {
  document_id: '20000000-0000-4000-8000-000000000010',
  knowledge_base_id: FILES_ID,
  knowledge_base_name: '技术资料',
  knowledge_base_active: true,
  upload_filename: '运行手册.md',
  title: '运行手册',
  mime_type: 'text/markdown',
  content_hash: 'b'.repeat(64),
  revision: 2,
  processing_status: 'indexed',
  processing_error: null,
  updated_at: timestamp,
  deletion_pending: false,
  deletion_error: null,
}
const files = new Map([[FILE_DOCUMENT.document_id, FILE_DOCUMENT]])
const details = new Map([
  [DOC_DETAIL.document_id, DOC_DETAIL],
  [
    FILE_DOCUMENT.document_id,
    {
      ...FILE_DOCUMENT,
      url: null,
      source_name: null,
      published_at: null,
      authors: [],
      labels: [],
      content_text:
        '# 当前运行手册\n\n日常备份保留 **14 天**。\n\n| 项目 | 规定 |\n| --- | --- |\n| 恢复演练 | 每月一次 |\n\n```sh\nbackup verify\n```\n\n![外链图片不会加载](https://example.com/mock-image.png)',
    },
  ],
])
const FILE_RESULT = {
  ...DOCUMENT_RESULT,
  document_id: FILE_DOCUMENT.document_id,
  knowledge_base_id: FILES_ID,
  knowledge_base_name: '技术资料',
  mime_type: 'text/markdown',
  upload_filename: FILE_DOCUMENT.upload_filename,
  title: FILE_DOCUMENT.title,
  url: null,
  source_name: null,
  published_at: null,
  authors: [],
  labels: [],
  chunk_count: 1,
  best_match: { ...BEST_MATCH, page_content: '日常备份保留 7 天。', chunk_count: 1 },
}
const scopes = new Map()
const replays = new Map()

function resolveScope(selection = { mode: 'all' }) {
  return {
    mode: selection.mode,
    knowledge_bases: KNOWLEDGE_BASES.filter(
      (item) =>
        item.is_active &&
        (selection.mode === 'all' || selection.knowledge_base_ids?.includes(item.id)),
    ),
  }
}

function searchResults(scope) {
  return [DOCUMENT_RESULT, ...(files.has(FILE_DOCUMENT.document_id) ? [FILE_RESULT] : [])].filter(
    (item) => scope.knowledge_bases.some((kb) => kb.id === item.knowledge_base_id),
  )
}

async function requestBody({ body, contentType = '' }) {
  if (!body) return {}
  if (contentType.includes('multipart/form-data')) {
    const form = await new Response(body, { headers: { 'content-type': contentType } }).formData()
    const file = form.get('file')
    return { ...Object.fromEntries(form), filename: file?.name, text: await file?.text() }
  }
  if (contentType.includes('application/json')) return JSON.parse(body.toString())
  return Object.fromEntries(new URLSearchParams(body.toString()))
}

function json(body, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(body) }
}

/** 仅本地内存模拟；支持范围、引用回看和文件操作，不调用业务服务。 */
export async function matchApi(url, authed, options = {}) {
  const requestUrl = new URL(url)
  const path = requestUrl.pathname // 形如 /api/auth/me
  const suffix = path.replace(/^\/api/, '')
  const method = options.method ?? 'GET'
  const body = await requestBody(options)
  const unauth = {
    status: 401,
    contentType: 'application/json',
    body: JSON.stringify({ detail: 'not authenticated', code: 'authentication_required' }),
  }
  if (suffix === '/auth/me') return authed ? json(SUPERUSER) : unauth
  if (suffix === '/auth/login') return { status: 204, contentType: 'text/plain', body: '' }
  if (suffix === '/auth/logout') return { status: 204, contentType: 'text/plain', body: '' }
  // 个人偏好：进程内可变，保存后刷新页面能读到刚写的那份（否则本地看什么都像没保存成功）。
  if (suffix === '/auth/me/preferences') {
    if (!authed) return unauth
    if (method === 'PUT') Object.assign(PREFERENCES, body ?? {})
    return json({ ...PREFERENCES })
  }
  if (!authed) return unauth
  const task = matchTaskApi(suffix, requestUrl.searchParams, method, body, options.requestKey ?? '')
  if (task) return task
  if (suffix === '/knowledge-bases')
    return json(
      KNOWLEDGE_BASES.filter(
        (item) => requestUrl.searchParams.get('include_inactive') === 'true' || item.is_active,
      ),
    )
  if (suffix === '/admin/users') return json(ACCOUNTS)
  const accountId = /^\/admin\/users\/([^/]+)$/.exec(suffix)?.[1]
  if (accountId && method === 'DELETE') {
    const index = ACCOUNTS.findIndex((user) => user.id === accountId)
    if (index < 0)
      return json({ code: 'user_not_found', detail: '账号不存在。', retryable: false }, 404)
    // 与后端的保护规则一致：保底管理员和最后一个活跃超管都删不了。
    // 本地调试若「删得掉」而线上被拒，会让人以为后端坏了。
    if (ACCOUNTS[index].is_environment_admin)
      return json(
        {
          code: 'environment_admin_protected',
          detail: '环境托管的管理员账号必须通过服务端密钥修改。',
          retryable: false,
        },
        409,
      )
    const activeSuperusers = ACCOUNTS.filter((user) => user.is_active && user.is_superuser)
    if (ACCOUNTS[index].is_active && ACCOUNTS[index].is_superuser && activeSuperusers.length <= 1)
      return json(
        {
          code: 'last_superuser_protected',
          detail: '最后一个活跃超级管理员不能被删除。',
          retryable: false,
        },
        409,
      )
    ACCOUNTS.splice(index, 1)
    return { status: 204, contentType: 'text/plain', body: '' }
  }
  if (suffix === '/sources') return json(SOURCES)
  if (suffix === '/document-search') {
    const scope = resolveScope(body.scope ?? { mode: 'selected', knowledge_base_ids: [NEWS_ID] })
    const results = searchResults(scope)
    return json(body.scope ? { scope, results } : results)
  }
  if (suffix === '/file-documents' && method === 'GET') {
    const offset = Number(requestUrl.searchParams.get('offset') ?? 0)
    return json({
      items: [...files.values()].slice(offset, offset + 25),
      has_more: false,
      max_file_bytes: 2 * 1024 * 1024,
    })
  }
  if (
    (suffix === '/file-documents' && method === 'POST') ||
    (/^\/file-documents\/[^/]+\/file$/.test(suffix) && method === 'PUT')
  ) {
    const target = method === 'PUT' ? files.get(suffix.split('/')[2]) : null
    const kb = KNOWLEDGE_BASES.find(
      (item) => item.id === (target?.knowledge_base_id ?? body.knowledge_base_id),
    )
    const hash = createHash('sha256')
      .update(body.text ?? '')
      .digest('hex')
    const unchanged = target?.content_hash === hash && target?.upload_filename === body.filename
    const item = {
      ...FILE_DOCUMENT,
      document_id: target?.document_id ?? randomUUID(),
      knowledge_base_id: kb.id,
      knowledge_base_name: kb.name,
      upload_filename: body.filename,
      title: body.filename.replace(/\.(md|txt)$/i, ''),
      mime_type: /\.md$/i.test(body.filename) ? 'text/markdown' : 'text/plain',
      content_hash: hash,
      revision: unchanged ? target.revision : (target?.revision ?? 0) + 1,
      processing_status: unchanged ? target.processing_status : 'pending',
      updated_at: new Date().toISOString(),
    }
    files.set(item.document_id, item)
    details.set(item.document_id, {
      ...item,
      content_text: body.text,
      url: null,
      source_name: null,
      published_at: null,
      authors: [],
      labels: [],
    })
    return json(item, target ? 200 : 201)
  }
  if (suffix.startsWith('/file-documents/') && method === 'DELETE') {
    const id = suffix.split('/')[2]
    files.delete(id)
    details.delete(id)
    return { status: 204, contentType: 'text/plain', body: '' }
  }
  if (suffix.endsWith('/retry') && method === 'POST') {
    const item = files.get(suffix.split('/')[2])
    item.processing_status = 'pending'
    return json(item)
  }
  if (suffix === '/vector-search') return json([CHUNK_RESULT])
  if (suffix === '/agent/default-prompt')
    return json({ system_prompt: '你是知识库检索助手，请基于本次取得的原文作答并引用。' })
  if (suffix.startsWith('/agent/threads') && suffix !== '/agent/threads') {
    const threadId = suffix.split('/')[3]
    if (suffix.endsWith('/scope')) {
      scopes.set(threadId, body)
      return json(body)
    }
    if (suffix.endsWith('/messages'))
      return json({
        thread_id: threadId,
        scope: scopes.get(threadId) ?? { mode: 'all' },
        turns: replays.get(threadId) ?? [],
        summarized: false,
        summary: null,
      })
    return json({ thread_id: threadId })
  }
  if (suffix === '/agent/threads') return json(THREADS)
  if (suffix === '/agent/chat') {
    const threadId = body.thread_id ?? randomUUID()
    const selection = body.scope ?? scopes.get(threadId) ?? { mode: 'all' }
    scopes.set(threadId, selection)
    const scope = resolveScope(selection)
    const result = searchResults(scope).at(-1)
    const runId = randomUUID()
    const evidence = result
      ? {
          citation_id: 'E0123456789ab',
          document_id: result.document_id,
          knowledge_base_id: result.knowledge_base_id,
          knowledge_base_name: result.knowledge_base_name,
          title: result.title,
          content_hash: result.content_hash,
          excerpt: result.best_match.page_content,
          upload_filename: result.upload_filename ?? null,
          source_name: result.source_name,
          url: result.url,
          published_at: result.published_at,
          kind: 'match',
          truncated: false,
        }
      : null
    const answer = evidence
      ? `${evidence.excerpt}[[${evidence.citation_id}]]`
      : '当前范围没有足够资料。'
    const trace = {
      tool_call_id: 'call_1',
      tool: 'search_documents',
      arguments: { query: body.message },
      content: evidence?.excerpt ?? '没有命中',
      failed: false,
      scope,
      evidence: evidence ? [evidence] : [],
    }
    const turn = {
      question: body.message,
      answer,
      status: 'completed',
      run_id: runId,
      scope,
      citations: evidence ? [evidence] : [],
      invalid_citations: [],
      traces: [trace],
    }
    replays.set(threadId, [...(replays.get(threadId) ?? []), turn])
    const events = [
      { event: 'run_started', thread_id: threadId, run_id: runId, scope },
      { event: 'tool_call', ...trace },
      { event: 'tool_result', ...trace },
      { event: 'token', text: answer },
      {
        event: 'done',
        thread_id: threadId,
        answer,
        status: turn.status,
        citations: turn.citations,
        invalid_citations: [],
      },
    ]
    return {
      status: 200,
      contentType: 'text/event-stream',
      body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''),
    }
  }
  if (suffix.startsWith('/documents/')) {
    const detail = details.get(suffix.split('/')[2])
    return detail
      ? json(detail)
      : json({ code: 'document_not_found', detail: '文档不存在。', retryable: false }, 404)
  }
  return null
}
