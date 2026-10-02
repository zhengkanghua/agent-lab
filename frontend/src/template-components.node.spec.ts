// @vitest-environment node
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

/* 组件模板里用到的 PascalCase 标签，必须在同一个文件里有过 import。
 *
 * 为什么需要这条：漏 import 的组件**两种自动检查都拦不住**——eslint 不解析模板标签，
 * vue-tsc 把未知标签当原生元素，两边都绿。只有运行时 Vue 打一条 [Vue warn]，
 * 界面照常渲染，只是那个组件整个不生效（2026-10 实测：JobForm 改成 BaseField 后
 * 少了一行 import，12 个字段的标签与 aria 接线静默消失，页面看起来「只是样式变了」，
 * 700 个测试全过）。
 *
 * 与 shared-styles.node.spec.ts 同一路数：直接读源文件做静态检查。这类「静默失效」
 * 只能靠结构守护钉住——前端 AGENTS.md 第 5 条记的那次「更多设置打不开」也是同一类。
 * 这里用「名字在 <script setup> 里出现过」做判据，不做真正的模块解析：够用，
 * 而且不会因为解析器差异误报。全局注册的组件（Vue 内置、vue-router 的
 * RouterLink/RouterView）在 GLOBAL 里放行，其余一律要求文件内有 import。
 */

const SRC = join(__dirname)
/** 不需要 import 的标签：Vue 内置组件，以及 vue-router 在 app.use(router) 时全局注册的两个。 */
const GLOBAL = new Set([
  'Transition',
  'TransitionGroup',
  'Teleport',
  'KeepAlive',
  'Suspense',
  'RouterLink',
  'RouterView',
])

function listVueFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return listVueFiles(path)
    return entry.name.endsWith('.vue') ? [path] : []
  })
}

/** 取最外层 <template> 的内容：文件里还有 <template #slot> 这种内层标签。 */
function templateBlock(source: string): string {
  const open = source.indexOf('\n<template>')
  if (open === -1) return ''
  const close = source.lastIndexOf('</template>')
  return close > open ? source.slice(open, close) : ''
}

function scriptBlock(source: string): string {
  const open = source.indexOf('<script')
  if (open === -1) return ''
  const end = source.indexOf('</script>', open)
  return end > open ? source.slice(open, end) : ''
}

const files = listVueFiles(SRC)
  .map((path) => path.slice(SRC.length + 1).replace(/\\/g, '/'))
  .filter((relative) => !relative.endsWith('.spec.ts'))

describe('模板组件标签都有 import', () => {
  it('找到的 .vue 文件数像是完整的', () => {
    expect(files.length).toBeGreaterThan(40)
  })

  it('每个 PascalCase 标签都能在同一个文件的 script 块里找到名字', () => {
    const missing: string[] = []
    for (const relative of files) {
      const source = readFileSync(join(SRC, relative), 'utf8')
      const script = scriptBlock(source)
      const used = new Set(
        [...templateBlock(source).matchAll(/<([A-Z][A-Za-z0-9]*)/g)].map((match) => match[1]!),
      )
      for (const name of used) {
        if (GLOBAL.has(name)) continue
        if (!new RegExp(`\\b${name}\\b`).test(script)) missing.push(`${relative}: <${name}>`)
      }
    }
    expect(missing.sort()).toEqual([])
  })
})
