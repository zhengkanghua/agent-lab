import type { SourceDto } from './sources'
import { newsKnowledgeBase } from './knowledge-bases.fixture'

export const boundNewsSource: SourceDto = {
  id: '20000000-0000-4000-8000-000000000001',
  provider: 'freshrss',
  external_id: 'feed/1',
  name: '财经早报',
  feed_url: 'https://freshrss.example.com/feed/1.xml',
  home_url: 'https://freshrss.example.com/feed/1',
  knowledge_base_id: newsKnowledgeBase.id,
  knowledge_base_key: newsKnowledgeBase.key,
  sync_checkpoint: '42',
  sync_checkpoint_updated_at: '2026-09-06T08:00:00Z',
}

export const unboundSource: SourceDto = {
  ...boundNewsSource,
  id: '20000000-0000-4000-8000-000000000002',
  external_id: 'feed/2',
  name: '待配置订阅',
  knowledge_base_id: null,
  knowledge_base_key: null,
  sync_checkpoint: null,
  sync_checkpoint_updated_at: null,
}
