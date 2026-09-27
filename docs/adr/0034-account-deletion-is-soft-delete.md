---
status: accepted
---

# 账号注销保留数据，不删除行

账号的「删除」从 `DELETE FROM users` 改成在行上盖一个注销时间（`deleted_at`），账号行、会话归属、个人偏好全部保留，只清掉登录 Token。理由是用户体系的依赖太多：账号一没，邮箱就查不到（用量记录只能显示「账号已删除」）、会话归属与个人偏好被一起清掉，而它们不是账号的附属数据，是这个人在系统里留下的记录。生产上的通行做法也是保留。对使用者来说这个动作就叫「注销」。

## Considered Options

**继续硬删除。** 实现最省事，但把「这个人不再存在」和「他做过的事不再存在」绑成了一件事，而我们要的只是前者。

**用状态枚举字段取代 `is_active`，让它成为唯一真源**（`status: active | suspended | deleted`，`is_active` 降级成由它算出来的视图）。概念上更干净，放弃它有两个具体代价：`is_active` 是 fastapi-users 的字段，登录（`.venv/.../router/auth.py:57`）与认证（`.venv/.../authentication/authenticator.py:182`）两处直接读它，改成算出来的值必须用 `hybrid_property` 并同时给出 SQL 表达式——现有两处拿它做 `SELECT ... WHERE` 过滤，普通 `property` 会 `AttributeError: 'property' object has no attribute 'is_'`；而且 `users` 表那条 `ck_users_environment_admin_privileges` 直接引用了 `is_active` 列，删列会让建表报 `no such column: is_active`。加一个独立时间字段不动这两处。

## Consequences

注销必须**同时**写两个字段：`is_active=false` 和 `deleted_at=当前时刻`。只写后者挡不住登录——上游两条路都只看 `is_active`，那样注销等于没做。`is_superuser` 保留，不改角色。

注销与封禁（停用）因此共用一个 `is_active=false`，区别只在 `deleted_at` 有没有值。代价是：账号注销之后，「他之前被封禁过没有」看不出来了。

`uq_users_email_lower` 不变，所以已注销账号的邮箱仍被占用、不能建新号；代价是超管会遇到「列表里查不到这个人、建号却说邮箱被占用」，所以建号冲突的提示要能解释这一点。

`ck_users_environment_admin_privileges` 加上 `AND deleted_at IS NULL`，让「环境托管账号被注销」在数据层写不进去；业务层仍要先检查并给出清楚原因，否则表现是一个数据库错误。启动同步（`auth/bootstrap.py`）遇到已注销的目标账号必须**抛错拒绝启动**，不能按恢复通道的本意把它拉回「活跃的超管」——那等于把一个已注销的人复活。

`document_review_records.actor_id` 的处理**反向变化**：原来置空（理由是账号行没了），现在保留（账号还在，指向仍有意义）。这是刻意改的，不是漏改。

`is_active` 这个名字在知识库、来源、定时任务上指的是完全不同的东西（那些对象是否启用），本次改动只碰 `users` 表。
