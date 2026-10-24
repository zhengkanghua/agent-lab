export const agentChatKeys = {
  all: ['agent-chat'] as const,
  threads: () => [...agentChatKeys.all, 'threads'] as const,
}
