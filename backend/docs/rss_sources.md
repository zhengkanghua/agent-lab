# RSS 来源与正文提取配置

本文记录已经实际验证过的 RSS 地址、正文获取方式和 CSS selector。添加来源时不能
只验证 Feed 能否返回 HTTP 200，还必须确认 Feed 是否持续更新、是否包含正文、
原文页面能否被 FreshRSS 服务器访问，以及正文 selector 是否稳定。

## 当前订阅

| 来源 | RSS 地址 | Feed 内容 | 原文 CSS selector | 建议配置 |
| --- | --- | --- | --- | --- |
| 中新网时政新闻 | `https://www.chinanews.com.cn/rss/china.xml` | 摘要 | `.left_zw` | 在 FreshRSS 中启用原文获取 |
| 彭博中国 | `https://www.bloombergchina.com/feed/` | `content:encoded` 已包含中文长正文 | 备用：`.article__content` | 已加入“财经”分类，不强制回源 |
| 国家统计局最新发布 | `https://rsshub.example.com/gov/stats/sj/zxfb` | RSSHub 已包含正文和附件 | 不需要 | 已加入“宏观数据”分类 |
| 联合早报 | `https://feedx.net/rss/zaobao.xml` | FeedX 已包含中文正文 | 不需要 | 已加入“新闻”分类 |
| 经济日报电子版 | `https://feedx.net/rss/jingjiribao.xml` | FeedX 已包含中文正文 | 不需要 | 已加入“财经”分类 |
| 界面新闻财经 | `https://rsshub.example.com/jiemian/lists/800` | RSSHub 已包含正文 | 不需要 | 已加入“财经”分类 |

### 同步分类白名单

FreshRSS 还可以保存本项目不需要处理的个人订阅。Python 服务不会读取总阅读列表，
只会读取环境变量 `FRESHRSS_SYNC_CATEGORIES` 明确允许的分类。例如：

```text
FRESHRSS_SYNC_CATEGORIES=["新闻","财经","宏观数据"]
```

Google Reader API 中的分类对应 `user/-/label/{分类名}` Stream。未配置的 `A`
等分类不会被同步；以后新增 FreshRSS 分类也默认不会进入业务库。白名单控制的是
分类，不是单个 Feed，因此项目分类中只应放置允许进入本服务的订阅。

### 新增来源验证记录

以下结果于 2026-08-12 从 Feed 原地址验证，并在加入后通过 FreshRSS 分类 Stream
再次读回。三类共读回 269 篇，全部能转换为 `SourceDocument`，没有空正文、缺失
原文 URL 或无时区发布时间。

| 来源 | 原始 Feed 检查 | FreshRSS 读回结果 | 已知边界 |
| --- | --- | --- | --- |
| 联合早报 | 20 条；链接、日期、正文均完整；正文长度 359 到 2886 字符 | “新闻”分类读回 20 条 | FeedX 未提供 RSS GUID，FreshRSS 根据条目标识生成内部 ID |
| 经济日报电子版 | 100 条；链接、日期、正文均完整；正文长度 28 到 8318 字符 | “财经”分类读回 100 条 | 6 个重复标题但链接不同，不能仅按标题去重；FeedX 未提供 RSS GUID |
| 界面新闻财经 | 16 条；链接、GUID、日期、正文均完整；正文长度 68 到 4970 字符 | “财经”分类读回 16 条 | 包含少量短快讯，后续可在文档质量阶段制定阈值 |

FeedX 属于第三方 Feed 服务，因此需要持续监控更新频率和内容来源。联合早报对应
的自建 RSSHub 路由当前因上游 JSON 解析错误返回 HTTP 503；人民日报 RSSHub
路由访问的旧版电子报地址返回 404；FT 中文 RSSHub 路由回源时返回 HTTP 429。
这三种情况都不能仅凭“RSSHub 有路由”判定为可用。

中新网财经、经济观察网、钛媒体和雪球热门曾用于临时调研，现已从 FreshRSS 删除。
调研结果仍保留在下文，避免以后重复验证。

### 为什么有的来源不配置 selector

FreshRSS 获取正文有两种路径：

1. Feed 自己提供完整的 `content:encoded`；
2. Feed 只提供摘要，FreshRSS 再访问文章 URL，并用 CSS selector 提取正文。

第二种路径增加了一次网络请求，还可能遇到反爬、登录、付费墙或网页结构变更。
因此，只要 Feed 已经提供足够完整的正文，就不应为了形式统一而强制回源。

雪球原文页依赖动态渲染，静态 HTML 中没有可供 FreshRSS 稳定提取的正文容器；但
其 RSS 已经提供正文，所以这是合理配置，不是缺少适配。

### selector 验证记录

- 中新网 `.left_zw`：抽查 3 篇，均唯一命中，正文长度分别为 696、1602、776。
- 经济观察网 `.xx_boxsing`：抽查 3 篇，均唯一命中。短公告本身可能只有百余字。
- 钛媒体 `article`：抽查 3 篇，均唯一命中，正文长度为 2954 到 4865。
- 雪球：抽查 5 条 `content:encoded`，正文长度为 852 到 2028，无需网页 selector。

经济观察网的 HTTPS Feed 在 FreshRSS 服务器侧添加时返回 HTTP 400，而 HTTP Feed
可以正常订阅，因此当前保存的是 `http://www.eeo.com.cn/rss.xml`。这不代表所有
来源都应降级到 HTTP，只是该来源的实测兼容结果。

## 暂不采用的来源

| 来源 | 暂不采用原因 |
| --- | --- |
| FT中文网 | Feed 可用，但自动访问原文页返回 HTTP 429，无法稳定获取全文 |
| 法国国际广播中文 | Feed 可用，但自动访问原文页返回 HTTP 403，且不是财经主源 |
| 德国之声中文 | 原文 `.rich-text` 可提取，但不是财经主源 |
| 人民网财经旧 RSS | Feed 最新文章停在 2025 年 6 月，已经不够及时 |
| 新华网旧财经 RSS | Feed 内容停在 2022 年，且部分文章没有发布时间 |
| `rsshub.app` 公共实例 | 金十、财联社等候选路由当前返回 HTTP 403，不能作为稳定依赖 |

## 待筛选来源

候选 RSSHub 地址统一使用 `{RSSHUB_BASE}` 表示未来自建实例地址。以下内容根据
2026-08-12 的 RSSHub `master` 路由定义整理；路由存在不等于生产可用，正式加入
FreshRSS 前还必须从自建 RSSHub 实例验证一次响应、时间、正文和链接。

### 中文财经与市场

| 来源 | 地址或路由 | 方向 | 内容形态 | 原文 selector |
| --- | --- | --- | --- | --- |
| 金十数据重要快讯 | `{RSSHUB_BASE}/jin10/important` | 全球宏观、央行、商品、外汇、地缘和市场快讯 | 底层 API 已验证更新；等待自建 RSSHub 最终验证 | 不需要 |
| 金十美国经济数据 | `{RSSHUB_BASE}/jin10/category/83` | 美国 CPI、非农、GDP 等经济数据相关新闻 | 数据有更新，但存在 VIP 空正文、推广和接口波动 | 不需要 |
| 金十央行 | `{RSSHUB_BASE}/jin10/category/26` | 全球央行综合 | RSSHub API 直接生成内容 | 不需要 |
| 金十美联储 | `{RSSHUB_BASE}/jin10/category/53` | 美联储政策、官员讲话 | RSSHub API 直接生成内容 | 不需要 |
| 金十中国央行 | `{RSSHUB_BASE}/jin10/category/54` | 中国货币政策 | RSSHub API 直接生成内容 | 不需要 |
| 金十财报 | `{RSSHUB_BASE}/jin10/category/59` | 美股财报 | RSSHub API 直接生成内容 | 不需要 |
| 金十黄金 | `{RSSHUB_BASE}/jin10/category/2` | 黄金 | RSSHub API 直接生成内容 | 不需要 |
| 金十原油 | `{RSSHUB_BASE}/jin10/category/6` | 原油、OPEC 和能源市场 | RSSHub API 直接生成内容 | 不需要 |
| 华尔街见闻金融资讯 | `{RSSHUB_BASE}/wallstreetcn/news/finance` | 金融新闻和深度文章 | RSSHub 获取文章详情并生成正文 | 不需要 |
| 华尔街见闻重要快讯 | `{RSSHUB_BASE}/wallstreetcn/live/global/2` | 全球重要财经快讯 | RSSHub API 直接生成快讯正文 | 不需要 |
| 华尔街见闻宏观日历 | `{RSSHUB_BASE}/wallstreetcn/calendar/macrodatas` | 全球经济数据实际值、预期值和前值 | 底层 API 已验证更新；等待自建 RSSHub 最终验证 | 不适用 |
| 华尔街见闻财报日历 | `{RSSHUB_BASE}/wallstreetcn/calendar/report` | 公司 EPS 预期、实际值和差异 | 结构化数据生成短文 | 不适用 |
| 财联社电报 | `{RSSHUB_BASE}/cls/telegraph` | A 股、公司、基金、港美股快讯 | RSSHub API 直接生成快讯正文 | 不需要 |
| 财联社金融深度 | `{RSSHUB_BASE}/cls/depth/1032` | 金融深度文章 | RSSHub 已抓取文章详情 | 不需要 |
| 第一财经金融 | `{RSSHUB_BASE}/yicai/news/jinrong` | 银行、保险、证券和金融政策 | RSSHub 已抓取文章正文 | 不需要 |
| 第一财经全球 | `{RSSHUB_BASE}/yicai/news/shijie` | 国际政经与市场 | RSSHub 已抓取文章正文 | 不需要 |
| 证券时报要闻 | `{RSSHUB_BASE}/stcn/article/list/yw` | 国内资本市场要闻 | RSSHub 已抓取详情正文 | 不需要 |
| 证券时报金融 | `{RSSHUB_BASE}/stcn/article/list/finance` | 银行、保险、证券等 | RSSHub 已抓取详情正文 | 不需要 |
| 东方财富宏观研究 | `{RSSHUB_BASE}/eastmoney/report/macresearch` | 券商宏观研报 | RSSHub 尝试抓取研报正文 | 不需要 |
| 东方财富策略报告 | `{RSSHUB_BASE}/eastmoney/report/strategyreport` | 市场策略研报 | RSSHub 尝试抓取研报正文 | 不需要 |

### 中文权威机构

| 来源 | RSSHub 路由 | 方向 | 技术说明 |
| --- | --- | --- | --- |
| 国家统计局最新发布 | `{RSSHUB_BASE}/gov/stats/sj/zxfb` | CPI、PPI、GDP、就业等官方统计发布 | 官方页面已验证更新；等待自建 RSSHub 最终验证 |
| 国家统计局数据解读 | `{RSSHUB_BASE}/gov/stats/sj/sjjd` | 官方数据解读 | RSSHub 获取正文和附件 |
| 中国人民银行政策研究 | `{RSSHUB_BASE}/gov/pbc/zcyj` | 货币政策与宏观研究 | 不采用：最新文章停在 2022-09-09 |
| 中国人民银行公开市场公告 | `{RSSHUB_BASE}/gov/pbc/tradeAnnouncement` | 逆回购、MLF 等公开市场操作 | RSSHub 必须启用 Puppeteer |
| 国家发改委新闻发布 | `{RSSHUB_BASE}/gov/ndrc/xwdt/xwfb` | 国内宏观政策和发布会 | RSSHub 获取正文和附件 |
| 国家发改委国内经济监测 | `{RSSHUB_BASE}/gov/ndrc/fggz/fgzh/gnjjjc` | 国内经济运行 | RSSHub 获取正文 |
| 财政部国债发行 | `{RSSHUB_BASE}/gov/mof/bond/gzfxzjs` | 记账式国债和特别国债发行 | RSSHub 获取 `div.my_doccontent` 正文 |
| 财政部关税政策 | `{RSSHUB_BASE}/gov/mof/gss/zhengcefabu` | 关税政策文件 | RSSHub 获取 `div.my_doccontent` 正文 |
| 中国证监会要闻 | `{RSSHUB_BASE}/gov/csrc/news/c100028/common_xq_list.shtml` | 资本市场监管 | RSSHub API 或详情页生成正文 |

### 国际品牌的中文内容

| 来源 | 地址或路由 | 方向 | 主要限制 |
| --- | --- | --- | --- |
| 彭博中国官方 RSS | `https://www.bloombergchina.com/feed/` | 宏观经济、市场观点、金融监管、金融科技和彭博产品洞察 | Feed 的 `content:encoded` 已带中文长正文；更新频率较低，不是 Bloomberg News 实时新闻流；备用 selector 为 `.article__content` |
| 日经中文网 | `{RSSHUB_BASE}/nikkei/cn/cn/rss` | 中日经济、亚洲产业和全球市场 | RSSHub 使用官方列表并抓取 `#contentDiv`；需自建后验证反爬 |
| FT中文网官方 RSS | `https://www.ftchinese.com/rss/feed` | 全球财经、商业和评论 | Feed 只有摘要；实测原文自动访问曾返回 HTTP 429 |
| FT中文网全文路由 | `{RSSHUB_BASE}/ftchinese/simplified/news` | 全球财经新闻 | RSSHub 尝试提取全文，但明确不支持付费文章 |
| BBC中文官方 RSS | `https://feeds.bbci.co.uk/zhongwen/simp/rss.xml` | 国际时政、社会、科技和部分经济 | 中文但不是纯财经，官方 Feed 主要是摘要 |
| 德国之声中文官方 RSS | `https://rss.dw.com/rdf/rss-chi-all` | 国际时政、中欧关系和部分经济 | 中文但不是纯财经；原文备用 selector 为 `.rich-text` |
| 法国国际广播中文 RSS | `https://www.rfi.fr/cn/rss` | 国际时政、欧洲和部分经济 | 中文但不是纯财经；实测原文自动访问曾返回 HTTP 403 |

彭博中国官方 RSS 抽查 5 篇，`content:encoded` 均包含中文长正文，原网页的
`.article__content` 也均唯一命中。它属于彭博专业服务中国站内容，不应误称为
“Bloomberg News 中文实时新闻”。

### 2026-08-12 RSSHub 候选验证

公共 `rsshub.app` 对华尔街见闻宏观日历、国家统计局、人民银行政策研究、金十
重要快讯和金十美国经济数据均返回 HTTP 403，并明确说明公共实例只用于测试。
项目现已使用自建实例 `https://rsshub.example.com` 进行实际路由验证。

- 华尔街见闻宏观日历底层 API 当天返回 35 条，包含发布时间、实际值、预期值、
  前值、单位和重要度，确认仍在更新。
- 国家统计局“最新发布”页面返回 15 条，最新包含 2026 年 7 月 CPI、PPI 等，
  发布时间为 2026-08-09，符合月度统计发布节奏。
- 中国人民银行“政策研究”页面虽然可访问，但最新文章停在 2022-09-09，不应
  再作为当前数据源。后续应另选人民银行正在更新的栏目。
- 金十重要快讯底层 API 返回当天数据。抽样时 21 条中有 6 条标为 important，
  这 6 条均有正文且没有 VIP 锁定，但仅 3 条带原文链接。
- 金十 `category/83` 抽样返回 100 条，其中 57 条有正文、22 条 VIP 锁定、约 4 条
  明显带竞猜或推广内容，接口还偶发 HTTP 502。它不能与 `/jin10/important`
  一样直接判定为稳定、干净的数据源。

自建 RSSHub 验证结果：

- 国家统计局路由返回 15 条，全部具有唯一链接、GUID、发布时间和正文，已加入
  FreshRSS。FreshRSS Google Reader API 可以读回全部 15 条，正文未被截断。
- 华尔街见闻宏观日历返回 35 条，正文、时间和 GUID 正常，但其中 10 条没有链接。
  当前 `FreshRSSItemMapper` 要求 URL，因此暂不加入。
- 金十重要快讯本次只返回 2 条，其中 1 条是“期货盯盘神器专属文章”推广且没有
  链接；暂不加入。需要先确定过滤推广和为无链接快讯建立稳定 URL 的规则。

金十无链接快讯还需要真实验证 RSSHub 到 FreshRSS 的最终协议输出。当前 Python
Mapper 要求文档具有 canonical 或 alternate URL；如果 FreshRSS 不为无链接条目
生成稳定 URL，这些条目会被拒绝。该规则不能在看到真实输出之前盲目放宽。

Bloomberg News 另有可用官方 RSS，例如
`https://feeds.bloomberg.com/markets/news.rss` 和
`https://feeds.bloomberg.com/economics/news.rss`，但内容是英文。RSSHub 的
`/bloomberg/markets`、`/bloomberg/economics` 等路由同样读取英文官方 Feed，
并不会把它翻译成中文。路透当前可见 RSSHub 路由也不是中文综合财经 Feed。

金十数据、财联社、华尔街见闻、央行、财政部和统计局等来源，后续优先通过自建
RSSHub 生成 Feed，再统一交给 FreshRSS。公共 RSSHub 演示实例不应作为生产数据
源。

## FreshRSS 配置边界

Google Reader 兼容 API 可以添加、删除和分类订阅，但不提供 FreshRSS 特有的
“原文 CSS selector”配置字段。因此订阅可以通过 API 添加，selector 仍需在
FreshRSS 的 Feed 配置页面中填写。本文是项目侧的配置依据，不能代替 FreshRSS
中的实际设置。

网站改版后，如果 selector 失效，应先重新检查原文 DOM，再更新 FreshRSS 和本
文档；不要立即在 Python Mapper 中增加某个网站专用解析分支。
