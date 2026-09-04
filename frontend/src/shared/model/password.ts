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
