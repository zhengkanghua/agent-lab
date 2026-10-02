import pluginVue from 'eslint-plugin-vue'
import { withVueTs, vueTsConfigs } from '@vue/eslint-config-typescript'
import prettierConfig from 'eslint-config-prettier/flat'

/* 禁止一切「把字符串当 HTML 交给浏览器解析」的写法。
 *
 * 正文来自后端、再往前来自抓取的网页，`v-html` 会把其中的 <script> 一起执行。
 * 模板侧由 vue/no-v-html 盯着（见下面把它从 warn 提到 error），这条选择器管 script 侧：
 * 核心规则遍历的是 JS AST，看不见模板属性，而 innerHTML 三兄弟是同一个注入口，
 * 只堵模板不堵脚本等于没堵。
 */
const htmlInjectionSinks = [
  {
    selector:
      "AssignmentExpression > MemberExpression[property.name=/^(inner|outer)HTML$/], CallExpression[callee.property.name='insertAdjacentHTML']",
    message: '不要拼 HTML 交给浏览器解析。渲染后端正文用 Vue 文本插值。',
  },
]

/* 跨层引用一律走 `@/` 别名，禁止 `../../` 起步的相对路径。
 *
 * 理由不是审美：`../../api/client` 这种路径读的时候要在脑子里算「我现在在第几层」，
 * 移动文件时又必须逐条重算。同层的 `./` 与 `../` 保留——那种就近引用是可读的。
 */
const crossLayerRelativeImport = {
  patterns: [
    {
      regex: '^\\.\\./\\.\\.',
      message: '跨层引用请用 `@/` 别名，不要 `../../`。同层的 `./`、`../` 不受限。',
    },
  ],
}

/* `src/pages` 是扁平的，所以从这里出发一个 `../` 就已经跨层了，全局那条
   `^../..` 规则拦不住。页面除了同目录的兄弟文件之外没有合法的相对引用对象。 */
const pagesRelativeImport = {
  patterns: [
    {
      regex: '^\\.\\./',
      message: 'src/pages 是扁平目录，`../` 一步就出层了。跨层请用 `@/` 别名。',
    },
  ],
}

/* 依赖方向铁律。违反它不会立刻报错，只会让层次悄悄失效，所以交给 lint 守。 */
const layerBoundaries = [
  {
    files: ['src/**/*.{ts,vue}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          ...crossLayerRelativeImport,
          patterns: [
            ...crossLayerRelativeImport.patterns,
            {
              group: ['@/features/*/*', '@/features/*/*/**'],
              message:
                '禁止深度导入 Feature 内部模块。请通过 Feature 根目录 (index.ts) 的公开 API 引入。',
            },
          ],
        },
      ],
    },
  },
  {
    files: ['src/pages/**/*.{ts,vue}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          ...pagesRelativeImport,
          patterns: [
            ...pagesRelativeImport.patterns,
            {
              group: ['@/features/*/*', '@/features/*/*/**'],
              message:
                '禁止深度导入 Feature 内部模块。请通过 Feature 根目录 (index.ts) 的公开 API 引入。',
            },
          ],
        },
      ],
    },
  },
  {
    files: ['src/shared/**/*.{ts,vue}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          ...crossLayerRelativeImport,
          patterns: [
            ...crossLayerRelativeImport.patterns,
            {
              group: ['@/features/*', '@/pages/*', '@/layouts/*', '@/app/*'],
              message: 'shared/ 是被依赖的底层，不能反过来 import 上层。',
            },
          ],
        },
      ],
    },
  },
  {
    files: ['src/features/*/**/*.{ts,vue}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          ...crossLayerRelativeImport,
          patterns: [
            ...crossLayerRelativeImport.patterns,
            {
              group: ['@/pages/*', '@/layouts/*'],
              message: 'feature 不能 import 页面或布局。要共享就下沉到 shared/。',
            },
            {
              group: ['@/features/*'],
              message:
                'Feature 之间禁止相互导入，保持完全解耦。如需交互请在 pages 层组合，或将逻辑下沉至 shared/。',
            },
          ],
        },
      ],
    },
  },
  {
    /* api 层是纯 HTTP 与契约层：只依赖网络客户端、生成类型与同层的纯工具。
       它被 features 与 pages 依赖，一旦反过来引用上层（拿一个 store、引一个组件），
       依赖方向就断了，而这类引用不会立刻报错，只会让层次悄悄失效。

       今天 src/api 下没有任何一处这样的引用，这条规则是把现状钉住，不是修问题。
       ../ 起步也算跨层：src/api 是扁平目录，一个 ../ 就出了这一层。 */
    files: ['src/api/**/*.{ts,vue}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          ...crossLayerRelativeImport,
          patterns: [
            ...crossLayerRelativeImport.patterns,
            {
              regex: '^\\.\\./',
              message: 'src/api 是扁平目录，`../` 一步就出层了。同层的 `./` 不受限。',
            },
            {
              group: ['@/features/*', '@/pages/*', '@/layouts/*', '@/app/*', '@/shared/ui/*'],
              message: 'api 层不要引用 feature、页面、布局或组件。共用逻辑请下沉到 shared/model。',
            },
          ],
        },
      ],
    },
  },
]

export default withVueTs(
  {
    /* .devshots/ 是截图与一次性核验脚本的落地目录（已 gitignore）。里面的 .mjs 是开发脚手架，
       跟 scripts/ 同样不守 src/ 的分层铁律，也不该因为本地跑过可视化工具就让 npm run lint 变红。 */
    ignores: ['dist/**', 'src/api/generated/**', 'scripts/**', '.devshots/**'],
  },
  pluginVue.configs['flat/recommended'],
  vueTsConfigs.recommended,
  /* 必须排在 vue 配置之后：它关掉 39 条 vue/ 的排版规则，让格式只有 Prettier 一个说法。
     少了这一行，flat/recommended 会报出 422 条纯排版告警，且其中多条与 .prettierrc.json
     的结论相反——两边都「修好」是不可能的。 */
  prettierConfig,
  {
    rules: {
      /* recommended 里这条只是 warn。降级成告警等于允许它长期存在，而这一条不是风格问题。 */
      'vue/no-v-html': 'error',
      'no-restricted-syntax': ['error', ...htmlInjectionSinks],
      'no-restricted-imports': ['error', crossLayerRelativeImport],
    },
  },
  ...layerBoundaries,
)
