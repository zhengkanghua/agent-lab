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
