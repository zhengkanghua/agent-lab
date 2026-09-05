import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

/* 共享 CSS 的结构守护。
 *
 * 断言的是「提取为什么安全」的前提，不是运行时渲染：jsdom 不实现 @layer 级联，
 * 挂载组件也读不出层间胜负。真正的风险是有人破坏结构——把共享规则搬回 scoped、
 * 把 @keyframes 包进 @layer、或漏掉 main.ts 的引入——这些静态可查。
 *
 * 直接读源文件而不用 import.meta.glob：Vitest 默认关闭 CSS 处理，CSS 导入一律是
 * 空串，?raw 与 ?inline 都取不到内容；为一个测试打开全局 css 会牵动所有挂载测试。
 * 因此本文件用 .node.spec.ts 后缀归入 tsconfig.node.json，那里才有 node 类型，
 * 应用工程的全局环境保持干净。
 */

const SRC = join(__dirname, '..')
const SHARED_DIR = join(SRC, 'styles', 'components')

function read(...segments: string[]): string {
  return readFileSync(join(SRC, ...segments), 'utf8')
}

/** 剥掉块注释，避免注释里的示例代码被当成规则。 */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '')
}

const sharedFiles = readdirSync(SHARED_DIR)
  .filter((name) => name.endsWith('.css'))
  .sort()

const styleCss = read('style.css')
const mainTs = read('main.ts')

describe('层序声明', () => {
  it('是 style.css 的首条语句', () => {
    expect(stripComments(styleCss).trim().startsWith('@layer reset, base, components;')).toBe(true)
  })

  it('声明的每一层都实际有规则', () => {
    const declared = /@layer\s+([^;]+);/.exec(stripComments(styleCss))?.[1] ?? ''
    const layerNames = declared.split(',').map((name) => name.trim())
    expect(layerNames).toEqual(['reset', 'base', 'components'])

    const allCss = [styleCss, ...sharedFiles.map((name) => read('styles', 'components', name))]
      .map(stripComments)
      .join('\n')
    for (const name of layerNames) {
      expect(allCss).toMatch(new RegExp(`@layer\\s+${name}\\s*\\{`))
    }
  })
})

describe('styles/components/*.css', () => {
  it('存在且被 main.ts 全部引入，顺序在 style.css 之后', () => {
    expect(sharedFiles.length).toBeGreaterThan(0)
    const anchor = mainTs.indexOf("import './style.css'")
    expect(anchor).toBeGreaterThan(-1)
    for (const name of sharedFiles) {
      const specifier = `./styles/components/${name}`
      expect(mainTs).toContain(specifier)
      expect(mainTs.indexOf(specifier)).toBeGreaterThan(anchor)
    }
  })

  it.each(sharedFiles.map((name): [string] => [name]))('%s 的 @keyframes 定义在层外', (name) => {
    const css = stripComments(read('styles', 'components', name))
    const layered = /@layer\s+components\s*\{[\s\S]*\}/.exec(css)?.[0]
    // 名字全局、不随层分层：包进层里会引出「哪一份定义胜出」的歧义。
    expect(layered ?? '').not.toContain('@keyframes')
  })

  it.each(sharedFiles.map((name): [string] => [name]))(
    '%s 的每条规则都在 @layer components 内',
    (name) => {
      const rest = stripComments(read('styles', 'components', name))
        .replace(/@layer\s+components\s*\{[\s\S]*\}/, '')
        .replace(/@keyframes\s+[\w-]+\s*\{(?:\s*[^{}]*\{[^{}]*\})*\s*\}/g, '')
        .trim()
      expect(rest).toBe('')
    },
  )
})

/* 颜色 token 的分层守护，见 docs/adr/0007-two-layer-color-tokens.md。
 *
 * 靠人自觉守不住：迁移时全仓有 23 处裸色值，每一处单看都「只是这一个地方」。
 * 深色模式的代价全在这里——漏一处裸色值，深色模式下它就是一块打不掉的浅斑。
 */
const TOKENS_CSS = 'styles/tokens.css'
/** 原始色阶的前缀。只允许 tokens.css 自己引用。 */
const RAW_SCALE = /var\(\s*--(?:neutral|teal|brick|amber)-\d+\s*\)/g
/** 裸色值：十六进制、rgb()/rgba()、hsl()/hsla()、CSS 具名颜色。 */
const BARE_COLOR =
  /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|:\s*(?:white|black|red|green|blue|gray|grey|silver|orange|yellow|purple|pink|brown|navy|teal|olive|maroon|lime|aqua|fuchsia)\s*[;!]/g

/** 只取 .vue 的 <style> 块内容：script 与 template 里的颜色字面量不在本轮范围。 */
function styleBlocks(source: string): string {
  return [...source.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)]
    .map((match) => match[1])
    .join('\n')
}

describe('颜色 token 分层', () => {
  const styledFiles: [string, string][] = [
    ...listVueFiles(SRC)
      .map((path) => path.slice(SRC.length + 1).replace(/\\/g, '/'))
      .map((relative): [string, string] => [relative, styleBlocks(read(relative))]),
    ...['style.css', ...sharedFiles.map((name) => `styles/components/${name}`)].map(
      (relative): [string, string] => [relative, read(relative)],
    ),
  ]

  it('取到了待查文件', () => {
    expect(styledFiles.length).toBeGreaterThanOrEqual(15)
    expect(styledFiles.map(([relative]) => relative)).toContain('pages/UserAdminPage.vue')
  })

  it.each(styledFiles)('%s 不写裸色值', (_relative, css) => {
    expect(stripComments(css).match(BARE_COLOR) ?? []).toEqual([])
  })

  it.each(styledFiles)('%s 不直接引原始色阶', (_relative, css) => {
    expect(stripComments(css).match(RAW_SCALE) ?? []).toEqual([])
  })

  /* 不是设计 token、由组件自己声明并沿 DOM 往下传的自定义属性。
     它们的值是布局量测结果（顶栏多高、表格分几列），放进 tokens.css 就得把断点也搬过去，
     那会让同一件事有两个来源。两个都用来让「必须对齐的两处」共用一份数字：顶栏高度给
     正文算视口余量，列宽给表头与每一行对齐。下面那条用例盯住它们真的有声明，
     所以这里放行不等于放松检查。 */
  const PUBLISHED_BY_COMPONENTS: Readonly<Record<string, string>> = {
    '--app-topbar-height': 'layouts/AppShell.vue',
    '--user-row-columns': 'features/user-admin/components/UserDirectoryTable.vue',
    '--job-row-columns': 'features/scheduled-jobs/components/JobDirectoryTable.vue',
  }

  it('引用到的 token 都在 tokens.css 里有定义', () => {
    const tokensCss = stripComments(read(TOKENS_CSS))
    const defined = new Set(
      [...tokensCss.matchAll(/^\s+(--[\w-]+)\s*:/gm)].map((match) => match[1]),
    )
    expect(defined.size).toBeGreaterThanOrEqual(30)

    const referenced = new Set(
      styledFiles.flatMap(([, css]) =>
        [...stripComments(css).matchAll(/var\(\s*(--[\w-]+)/g)].map((match) => match[1]),
      ),
    )
    // 拼错的 token 名不报错、只是静默失效，浏览器里看不出来，只能在这里查。
    expect(
      [...referenced]
        .filter((name) => !defined.has(name) && !(name in PUBLISHED_BY_COMPONENTS))
        .sort(),
    ).toEqual([])
  })

  it.each(Object.entries(PUBLISHED_BY_COMPONENTS))('%s 由 %s 真的声明了', (name, owner) => {
    /* 上一条用例给这些名字开了口子，这条把口子收住：声明所在的文件写死在表里，
         哪天 AppShell 不再发布它，引用方会拿到 var() 的兜底值静默偏移，只有这里能拦。 */
    expect(stripComments(styleBlocks(read(owner)))).toMatch(new RegExp(`${name}\\s*:\\s*[^;]+;`))
  })
})

/* 共享类在组件 scoped 块里的顶层重声明。
 *
 * 当前没有任何允许的重声明：result-card.css 的最后两个条目（locator-line、score-block）
 * 随该文件折叠回 SearchResultCard 而删除——类不再属于共享层，本地怎么写都不算回退。
 * 以后往 styles/components/ 加共享类时，如果某个组件需要刻意覆盖一条，
 * 在这里登记文件与类名，并补一条「为什么是这一条」的说明。
 */
const LOCAL_OVERRIDES: Record<string, string[]> = {}

/** 按大括号配对删掉 @media 块：断点覆盖不算回退，两页断点本就不同。 */
function stripMediaBlocks(css: string): string {
  let out = ''
  let index = 0
  while (index < css.length) {
    const start = css.indexOf('@media', index)
    if (start === -1) {
      out += css.slice(index)
      break
    }
    out += css.slice(index, start)
    let cursor = css.indexOf('{', start)
    if (cursor === -1) {
      break
    }
    let depth = 1
    while (depth > 0 && ++cursor < css.length) {
      if (css[cursor] === '{') depth += 1
      else if (css[cursor] === '}') depth -= 1
    }
    index = cursor + 1
  }
  return out
}

function listVueFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return listVueFiles(path)
    return entry.name.endsWith('.vue') ? [path] : []
  })
}

/** 共享文件里的顶层单类选择器，形如 `  .foo {`；后代与伪类选择器不参与本轮断言。 */
const sharedClasses: string[] = sharedFiles.flatMap((name) => {
  const css = stripComments(read('styles', 'components', name))
  const found: string[] = []
  const pattern = /^ {2}\.([\w-]+)\s*\{/gm
  let match = pattern.exec(css)
  while (match !== null) {
    found.push(match[1])
    match = pattern.exec(css)
  }
  return found
})

describe('共享类未被组件重新声明', () => {
  const vueFiles = listVueFiles(SRC).map((path) => path.slice(SRC.length + 1).replace(/\\/g, '/'))

  it('取到了共享类与组件清单', () => {
    /* result-card.css 折叠回 SearchResultCard 后共享文件只剩 topbar 与 motion 两个，
       加上检索流宽度等纯声明，这里的下限相应下调；再往共享层加文件时应同步上调。 */
    expect(sharedClasses.length).toBeGreaterThanOrEqual(8)
    // 不锁总数，只确认遍历真的走到了参与提取的五个文件——它们是断言的实际对象。
    expect(vueFiles).toEqual(
      expect.arrayContaining([
        'features/semantic-search/components/SearchRecordTurn.vue',
        'features/semantic-search/components/SearchResultCard.vue',
        'pages/LoginPage.vue',
        'pages/SearchPage.vue',
        'pages/UserAdminPage.vue',
      ]),
    )
  })

  // 包成一元元组：it.each 对裸数组推不出单参数签名。
  it.each(sharedClasses.map((name): [string] => [name]))(
    '.%s 只在允许的组件里保留本地覆盖',
    (className) => {
      const allowed = [...(LOCAL_OVERRIDES[className] ?? [])].sort()
      const found = vueFiles
        .filter((relative) =>
          new RegExp(`^\\.${className}\\s*\\{`, 'm').test(
            stripMediaBlocks(stripComments(read(relative))),
          ),
        )
        .sort()
      expect(found).toEqual(allowed)
    },
  )

  it.each(Object.entries(LOCAL_OVERRIDES))('.%s 的本地覆盖只有一条声明', (className, paths) => {
    for (const relative of paths) {
      const body = new RegExp(`^\\.${className}\\s*\\{([^}]*)\\}`, 'm').exec(read(relative))?.[1]
      const declarations = (body ?? '')
        .split(';')
        .map((part) => part.trim())
        .filter(Boolean)
      expect(declarations).toHaveLength(1)
    }
  })
})
