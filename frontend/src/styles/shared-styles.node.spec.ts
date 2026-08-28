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

/* 共享类在组件 scoped 块里的顶层重声明。
 *
 * 允许的重声明只有三处，每处都是「刻意留在本地的那一条声明」：
 * 两个卡片的强调色，和 UserAdminPage 刻意不同的 800ms 计时。
 * 其余共享类一旦在 scoped 顶层重新出现，就说明提取被回退了。
 */
const LOCAL_OVERRIDES: Record<string, string[]> = {
  spin: ['pages/UserAdminPage.vue'],
  'locator-line': [
    'features/semantic-search/components/ChunkResultCard.vue',
    'features/semantic-search/components/SearchResultCard.vue',
  ],
  'score-block': [
    'features/semantic-search/components/ChunkResultCard.vue',
    'features/semantic-search/components/SearchResultCard.vue',
  ],
}

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
    expect(sharedClasses.length).toBeGreaterThanOrEqual(12)
    // 不锁总数，只确认遍历真的走到了参与提取的五个文件——它们是断言的实际对象。
    expect(vueFiles).toEqual(
      expect.arrayContaining([
        'features/semantic-search/components/ChunkResultCard.vue',
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
