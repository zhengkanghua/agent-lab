/* 文档阅读器的输入契约：检索结果、Agent 引用、文件资料三个入口都靠它把
 * 「要看哪一篇」交给阅读器，字段是打开全文所需的稳定文档身份与回退元数据。
 *
 * 放在 shared 而不是某个 feature 里：这三个入口分属三个 feature，任何一个
 * feature 拥有它都会让另外两个反向依赖它。
 */

/** 打开全文时所需的稳定文档身份和回退元数据（详情接口缺省时由它兜底）。 */
export interface ReadableResult {
  documentId: string
  knowledgeBaseId: string
  knowledgeBaseName?: string
  uploadFilename?: string | null
  contentHash: string
  title: string
  url: string | null
  sourceName: string | null
  publishedAt: string | null
  labels: string[]
  authors: string[]
}

/* Agent 引用快照：打开阅读器时显示「当时引用的片段」，与当前原文对照。
 *
 * 阅读层只用到这四个字段，所以只声明这四个，不 import Agent 的 `DocumentEvidence`：
 * 那会让「读文档」这个被检索、Agent、文件资料三处共用的能力反向认识 Agent 的领域类型，
 * 而 Agent 只是调用入口之一。用结构类型正好——调用方传 DocumentEvidence 同样满足本接口。 */
export interface EvidenceSnapshot {
  knowledge_base_name: string
  title: string
  excerpt: string
  /* 可选：后端契约里它就是可选的，缺省表示「当时读的是完整正文」。
     写成必填会让 DocumentEvidence 不再满足本接口。 */
  truncated?: boolean
}

/* 阅读层与结果卡共用的发布时间格式：只到日，固定东八区——
 * 检索结果的时间戳来自后端，按浏览器本地时区渲染会让同一条记录在不同机器上显示不同日期。 */
const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
  timeZone: 'Asia/Shanghai',
})

export function formatPublishedAt(value: string | null): string {
  if (!value) {
    return '时间未提供'
  }

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return '时间未提供'
  }
  return dateFormatter.format(date)
}
