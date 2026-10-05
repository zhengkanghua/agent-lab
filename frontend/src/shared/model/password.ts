/**
 * 密码强度常量。
 *
 * 与后端各密码请求 Field 的 min_length/max_length 约束一一对应（账号自助改密与
 * 管理端改密共用同一套规则），只用于提交前给出即时提示。密码策略的其余部分
 * （例如不得与邮箱相同）只由后端判定，前端读 `invalid_password` 的文案，
 * 不在这里重复实现——重复实现会在后端调整策略时静默产生两套口径。
 */

export const PASSWORD_MIN_LENGTH = 12
export const PASSWORD_MAX_LENGTH = 128

/**
 * 提交前那句长度提示：只说长度。
 *
 * 两个改密入口（设置中心的自助改密、管理端的重置密码）共用这一份。上一版两处各手写了一遍
 * 同样的句子，改措辞时会漏掉一处——所以它在这里，不在各 feature 里。
 */
export const PASSWORD_LENGTH_MESSAGE = `密码长度需要在 ${PASSWORD_MIN_LENGTH} 到 ${PASSWORD_MAX_LENGTH} 个字符之间。`

/**
 * 后端拒绝这个密码时，前端替它说的那句话：长度 + 「不能与邮箱相同」。
 *
 * 比上面那句多一节，因为它描述的是**完整策略**，而前端只实现了长度那半（见文件头）。
 * 两个入口原本各写各的措辞（「必须包含…且不能与登录邮箱完全相同」与「不符合安全要求：
 * 需要…且不能与账号邮箱相同」），说的却是同一条规则——同一件事在界面上有两种说法，
 * 用户会以为是两条不同的规定。统一成这一句。
 */
export const PASSWORD_POLICY_MESSAGE = `密码需要 ${PASSWORD_MIN_LENGTH} 到 ${PASSWORD_MAX_LENGTH} 个字符，且不能与账号邮箱相同。`
