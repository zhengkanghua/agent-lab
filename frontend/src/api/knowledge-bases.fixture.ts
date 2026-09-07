import type { KnowledgeBaseDto } from './knowledge-bases'

export const newsKnowledgeBase: KnowledgeBaseDto = {
  id: '10000000-0000-4000-8000-000000000010',
  key: 'news',
  name: '新闻',
  description: '每日新闻与行业动态',
  is_active: true,
  created_at: '2026-09-06T00:00:00Z',
  updated_at: '2026-09-06T00:00:00Z',
}

export const techKnowledgeBase: KnowledgeBaseDto = {
  ...newsKnowledgeBase,
  id: '10000000-0000-4000-8000-000000000011',
  key: 'tech-notes',
  name: '技术资料',
  description: null,
  is_active: false,
}
