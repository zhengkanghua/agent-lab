// @vitest-environment node
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
const sharedStyles: [string, string][] = sharedFiles.map((name) => [
  name,
  stripComments(read('styles', 'components', name)),
])

describe('层序声明', () => {
  it('是 style.css 的首条语句', () => {
    expect(stripComments(styleCss).trim().startsWith('@layer reset, base, components;')).toBe(true)
  })

  it('声明的每一层都实际有规则', () => {
    const declared = /@layer\s+([^;]+);/.exec(stripComments(styleCss))?.[1] ?? ''
    const layerNames = declared.split(',').map((name) => name.trim())
    expect(layerNames).toEqual(['reset', 'base', 'components'])

    const allCss = [stripComments(styleCss), ...sharedStyles.map(([, css]) => css)].join('\n')
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

  it('全部共享文件的 @keyframes 定义在层外', () => {
    for (const [name, css] of sharedStyles) {
      const layered = /@layer\s+components\s*\{[\s\S]*\}/.exec(css)?.[0]
      // 名字全局、不随层分层：包进层里会引出「哪一份定义胜出」的歧义。
      expect(layered ?? '', name).not.toContain('@keyframes')
    }
  })

  it('全部共享规则都在 @layer components 内', () => {
    for (const [name, css] of sharedStyles) {
      const rest = css
        .replace(/@layer\s+components\s*\{[\s\S]*\}/, '')
        .replace(/@keyframes\s+[\w-]+\s*\{(?:\s*[^{}]*\{[^{}]*\})*\s*\}/g, '')
        .trim()
      expect(rest, name).toBe('')
    }
  })
})

/* 颜色 token 的分层守护，见 docs/adr/0007-two-layer-color-tokens.md。
 *
 * 靠人自觉守不住：迁移时全仓有 23 处裸色值，每一处单看都「只是这一个地方」。
 * 深色模式的代价全在这里——漏一处裸色值，深色模式下它就是一块打不掉的浅斑。
 */
const TOKENS_CSS = 'styles/tokens.css'
/** 原始色阶的前缀。只允许 tokens.css 自己引用。 */
const RAW_SCALE = /var\(\s*--(?:neutral|pine|brick|amber)-\d+\s*\)/g
/** 裸色值：十六进制、rgb()/rgba()、hsl()/hsla()、CSS 具名颜色。 */
const BARE_COLOR =
  /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|:\s*(?:white|black|red|green|blue|gray|grey|silver|orange|yellow|purple|pink|brown|navy|teal|olive|maroon|lime|aqua|fuchsia)\s*[;!]/g

/** 只取 .vue 的 <style> 块内容：script 与 template 里的颜色字面量不在本轮范围。 */
function styleBlocks(source: string): string {
  return [...source.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)]
    .map((match) => match[1])
    .join('\n')
}

// 每个源文件只读取、剥注释一次；按规则报告失败，消息仍指出具体文件。
const vueStyles: [string, string][] = listVueFiles(SRC)
  .map((path) => path.slice(SRC.length + 1).replace(/\\/g, '/'))
  .map((relative) => [relative, stripComments(styleBlocks(read(relative)))])
const styledFiles: [string, string][] = [
  ...vueStyles,
  ['style.css', stripComments(styleCss)],
  ...sharedStyles.map(([name, css]): [string, string] => [`styles/components/${name}`, css]),
]

describe('颜色 token 分层', () => {
  it('全部组件与共享样式不写裸色值', () => {
    expect(styledFiles.length).toBeGreaterThan(0)
    for (const [relative, css] of styledFiles) {
      expect(css.match(BARE_COLOR) ?? [], relative).toEqual([])
    }
  })

  it('全部组件与共享样式不直接引原始色阶', () => {
    for (const [relative, css] of styledFiles) {
      expect(css.match(RAW_SCALE) ?? [], relative).toEqual([])
    }
  })

  /* 不是设计 token、由组件自己声明并沿 DOM 往下传的自定义属性。
     它们的值是布局量测结果（头部偏移多少、表格分几列），放进 tokens.css 就得把断点也搬过去，
     那会让同一件事有两个来源。两个都用来让「必须对齐的两处」共用一份数字：头部偏移给
     正文算视口余量，列宽给表头与每一行对齐。下面那条用例盯住它们真的有声明，
     所以这里放行不等于放松检查。 */
  const PUBLISHED_BY_COMPONENTS: Readonly<Record<string, string>> = {
    '--app-header-offset': 'layouts/AppShell.vue',
    '--user-row-columns': 'features/user-admin/components/UserDirectoryTable.vue',
    '--job-row-columns': 'features/scheduled-jobs/components/JobDirectoryTable.vue',
  }

  it('引用到的 token 都在 tokens.css 里有定义', () => {
    const tokensCss = stripComments(read(TOKENS_CSS))
    const defined = new Set(
      [...tokensCss.matchAll(/^\s+(--[\w-]+)\s*:/gm)].map((match) => match[1]),
    )
    expect(defined.size).toBeGreaterThan(0)

    const referenced = new Set(
      styledFiles.flatMap(([, css]) =>
        [...css.matchAll(/var\(\s*(--[\w-]+)/g)].map((match) => match[1]),
      ),
    )
    // 拼错的 token 名不报错、只是静默失效，浏览器里看不出来，只能在这里查。
    expect(
      [...referenced]
        .filter((name) => !defined.has(name) && !(name in PUBLISHED_BY_COMPONENTS))
        .sort(),
    ).toEqual([])
  })

  it('组件发布的自定义属性确实在所属文件声明', () => {
    /* 上一条用例给这些名字开了口子，这条把口子收住：声明所在的文件写死在表里，
         哪天 AppShell 不再发布它，引用方会拿到 var() 的兜底值静默偏移，只有这里能拦。 */
    for (const [name, owner] of Object.entries(PUBLISHED_BY_COMPONENTS)) {
      const css = vueStyles.find(([relative]) => relative === owner)?.[1] ?? ''
      expect(css, `${owner}: ${name}`).toMatch(new RegExp(`${name}\\s*:\\s*[^;]+;`))
    }
  })
})

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
const sharedClasses: string[] = sharedStyles.flatMap(([, css]) => {
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
  const componentStyles = vueStyles.map(([relative, css]) => ({
    relative,
    css: stripMediaBlocks(css),
  }))

  it('全部共享类都没有组件内的重复声明', () => {
    expect(sharedClasses.length).toBeGreaterThan(0)
    expect(componentStyles.length).toBeGreaterThan(0)
    for (const className of sharedClasses) {
      const found = componentStyles
        .filter(({ css }) => new RegExp(`^\\.${className}\\s*\\{`, 'm').test(css))
        .map(({ relative }) => relative)
        .sort()
      expect(found, `.${className} 不应在组件内重复声明`).toEqual([])
    }
  })
})
