# 前端错误文案统一走 resolveErrorCopy

前端把异常翻译成用户文案，一律用 `frontend/src/api/error-copy.ts` 的 `resolveErrorCopy`，
判定顺序固定为 code → HTTP status → 兜底，非 `ApiError` 直接走兜底。共用的只有查表机制，
文案表留在各自领域文件里（`search-error.ts`、`DocumentReader.vue`、`UserAdminPage.vue`、
`LoginPage.vue`）。

在这之前四处各自手写判定，依据还不一致：检索页混用 code 和 status，全文阅读只看 status，
账号管理只看 code，登录页只看 status。新增错误码时容易只补其中一处。

## Consequences

**code 优先于 status 是有意的，不能调换。** 422 既可能来自后端应用级校验兜底（响应体没有
code，`client.ts` 合成 `validation_error`），也可能来自路由级脱敏（带 `invalid_request`）。
要让两者落到各自的文案上，就必须让 code 先说话。

**`ApiError` 的 status 缺省记 0。** 登录页靠这个 0 区分「连不上服务器」和「服务器拒绝了」。
改动 status 缺省值会静默改掉登录页的文案分支。

新增错误展示位置时不要再手写 if-else 链，加一张表传给 `resolveErrorCopy` 即可。
