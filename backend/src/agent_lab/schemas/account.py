"""定义账号自助操作（改自己的密码）的输入与稳定错误契约。

本模块只服务「当前登录账号改自己」的用例，不含任何针对他人的字段：没有目标账号 id、
没有权限开关。改别人的能力在 ``schemas/user_admin.py``，两边刻意不共用请求模型——
共用会让「谁是操作对象」变成一个可选字段，那正是权限漏洞最容易长出来的地方。

请求体里含两个明文密码，所以承载它的路由必须挂 ``SanitizedValidationRoute``：
Pydantic 校验失败时 ``input`` 字段装的就是原始请求体。响应侧不返回任何密码相关内容，
成功是 204 空响应。
"""

from pydantic import BaseModel, ConfigDict, Field


class AccountPasswordChangeRequest(BaseModel):
    """当前登录账号修改自己密码的请求。

    两个字段都是必填的明文密码，只在单次请求内存在：旧密码用于校验身份，新密码校验强度后
    立即 Hash。都不写日志、不进异常消息、不回显。

    ``current_password`` 不设 ``min_length``：它是「用户当时设的那个密码」，不是新策略的
    校验对象。给它加长度下限会让历史上用短密码建的账号连改密入口都进不去——校验旧密码只该
    问「对不对」，不该问「合不合现在的规矩」。
    """

    current_password: str = Field(
        min_length=1,
        max_length=128,
        description="账号当前的登录密码；仅用于本次请求的身份校验，不落库、不回显。",
    )
    new_password: str = Field(
        min_length=12,
        max_length=128,
        description="账号的新密码；通过强度校验后立即 Hash，明文不落库、不回显。",
    )

    model_config = ConfigDict(extra="forbid")


class AccountErrorResponse(BaseModel):
    """账号自助操作的稳定、脱敏错误结构。

    字段与其他链路的错误响应同构（``code``/``detail``/``retryable``），前端按 ``code``
    取文案，不解析 ``detail``。
    """

    code: str = Field(description="供前端稳定识别的错误代码。")
    detail: str = Field(description="不含密码、Hash、Token 或数据库异常文本的安全说明。")
    retryable: bool = Field(description="相同请求稍后重试是否可能成功。")
