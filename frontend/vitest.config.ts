import { defineConfig, mergeConfig } from 'vitest/config'
// 带 .ts 后缀：本文件归 tsconfig.node.json，那里是 nodenext，要求写全扩展名。
import viteConfig from './vite.config.ts'

/* 用 mergeConfig 继承 vite.config.ts，而不是自己再写一份 plugins 与 resolve。
 *
 * 这个文件独立存在时不会自动继承 vite 配置，是个反复踩的坑：别名、插件在这里
 * 漏一项，表现是「浏览器里好的，测试里解析不到」。继承之后只有一处定义。
 * server.proxy 一并被带进来，但测试里没有 dev server，它是惰性的。
 */
export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      clearMocks: true,
      restoreMocks: true,
      include: ['src/**/*.spec.ts'],
    },
  }),
)
