import type { AgentChatEvent } from './agent-chat'
import type { DocumentEvidence } from './agent-evidence'
import type { ResolvedKnowledgeBaseScope } from './knowledge-scope'
import { newsKnowledgeBase } from './knowledge-bases.fixture'

export const AGENT_THREAD_ID = '30000000-0000-4000-8000-000000000001'
export const AGENT_RUN_ID = '30000000-0000-4000-8000-000000000010'
export const agentScope: ResolvedKnowledgeBaseScope = {
  mode: 'selected',
  knowledge_bases: [
    {
      id: newsKnowledgeBase.id,
      key: newsKnowledgeBase.key,
      name: newsKnowledgeBase.name,
      description: null,
    },
  ],
}
export const agentEvidence: DocumentEvidence = {
  citation_id: 'E0123456789ab',
  document_id: '20000000-0000-4000-8000-000000000001',
  knowledge_base_id: newsKnowledgeBase.id,
  knowledge_base_name: newsKnowledgeBase.name,
  title: '运行手册',
  content_hash: 'a'.repeat(64),
  excerpt: '日常备份保留 7 天。',
  upload_filename: '运行手册.md',
  kind: 'match',
  truncated: false,
  source_name: null,
  url: null,
  published_at: null,
}

export function agentDone(
  answer = '答',
  threadId = AGENT_THREAD_ID,
): Extract<AgentChatEvent, { event: 'done' }> {
  return {
    event: 'done',
    thread_id: threadId,
    answer,
    status: 'completed',
    citations: [],
    invalid_citations: [],
  }
}

export function agentStarted(
  scope = agentScope,
): Extract<AgentChatEvent, { event: 'run_started' }> {
  return { event: 'run_started', thread_id: AGENT_THREAD_ID, run_id: AGENT_RUN_ID, scope }
}
