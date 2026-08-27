"""FreshRSS Google Reader 兼容 API 的异步客户端。

本模块只负责 HTTP 协议、认证和原始响应校验。它不会在这里创建业务模型、
写入 PostgreSQL 或修改 FreshRSS 中的已读状态。
"""

from collections.abc import Sequence
from types import TracebackType
from typing import Any, Self

import httpx
from pydantic import SecretStr, ValidationError

from news_vector_service.config.freshrss import FreshRSSSettings
from news_vector_service.schemas.freshrss import (
    FreshRSSItem,
    FreshRSSItemIdPage,
    FreshRSSSubscription,
)


class FreshRSSError(RuntimeError):
    """FreshRSS 请求或响应无法继续处理时的基础异常。"""


class FreshRSSAuthenticationError(FreshRSSError):
    """FreshRSS API 用户名、API 密码或认证响应无效。"""


class FreshRSSConnectionError(FreshRSSError):
    """无法连接 FreshRSS API 的 DNS、TCP 或 TLS 端点。"""


class FreshRSSTimeoutError(FreshRSSError):
    """FreshRSS API 请求超过配置的超时。"""


class FreshRSSServiceError(FreshRSSError):
    """FreshRSS 返回未单独分类的服务端错误。"""


class FreshRSSProtocolError(FreshRSSError):
    """FreshRSS 返回了不符合预期协议结构的数据。"""


class FreshRSSClient:
    """封装项目实际需要的 FreshRSS Google Reader API 操作。

    客户端通过异步上下文管理器使用，确保底层 HTTP 连接池总能关闭：

    .. code-block:: python

        async with FreshRSSClient(settings) as client:
            article = await client.fetch_latest_article()
    """

    _CLIENT_LOGIN_PATH = "greader.php/accounts/ClientLogin"
    _ITEM_IDS_PATH = "greader.php/reader/api/0/stream/items/ids"
    _ITEM_CONTENTS_PATH = "greader.php/reader/api/0/stream/items/contents"
    _SUBSCRIPTION_LIST_PATH = "greader.php/reader/api/0/subscription/list"
    _CATEGORY_STREAM_PREFIX = "user/-/label/"

    def __init__(self, settings: FreshRSSSettings) -> None:
        """根据已校验配置创建复用连接池的异步 HTTP 客户端。

        Args:
            settings: FreshRSS 地址、凭据、超时和 TLS 校验等配置。
        """

        self._settings = settings
        self._auth_token: SecretStr | None = None

        # 末尾斜杠很重要，否则 HTTP URL 合并规则可能把 /api 当成文件名替换掉。
        base_url = f"{str(settings.api_base_url).rstrip('/')}/"
        # HTTPX 只会在建立连接失败或连接超时时重试，不会重试 401 等业务响应。 
        # 连接层重试2次
        transport = httpx.AsyncHTTPTransport(retries=2)
        self._http_client = httpx.AsyncClient(
            base_url=base_url,
            timeout=settings.request_timeout_seconds,
            verify=settings.verify_ssl,
            transport=transport,
            headers={"User-Agent": "news-vector-service/0.1.0"},
        )

    async def __aenter__(self) -> Self:
        """进入异步上下文并返回当前客户端。

        Returns:
            当前客户端实例，供 ``async with ... as client`` 使用。
        """

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """退出异步上下文时关闭 HTTP 连接池。

        Args:
            exc_type: 上下文内部异常类型；正常退出时为 ``None``。
            exc_value: 上下文内部异常对象；正常退出时为 ``None``。
            traceback: 上下文内部异常的回溯；正常退出时为 ``None``。
        """

        await self.close()

    async def close(self) -> None:
        """释放 HTTPX 持有的网络连接。"""

        await self._http_client.aclose()

    async def authenticate(self) -> None:
        """
        懒登录
        使用 FreshRSS 用户名和 API 密码换取短期认证 Token。

        Token 只保存在当前客户端内存中，并使用 ``SecretStr`` 包装，避免对象
        被打印时意外泄露。调用方不需要也不应该持久化这个 Token。
        """

        try:
            # 1. 用用户名 + API 密码调 ClientLogin 换 Token
            response = await self._http_client.post(
                self._CLIENT_LOGIN_PATH,
                data={
                    "Email": self._settings.username,
                    "Passwd": self._settings.api_password.get_secret_value(),
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # 2. 把 HTTP 状态分类成稳定领域异常（认证/超时/服务端），不泄露凭据
            if exc.response.status_code in {401, 403}:
                raise FreshRSSAuthenticationError(
                    "FreshRSS 拒绝了 API 凭据。"
                ) from exc
            if exc.response.status_code in {408, 504}:
                raise FreshRSSTimeoutError("FreshRSS 登录请求超时。") from exc
            raise FreshRSSServiceError(
                f"FreshRSS 登录返回 HTTP {exc.response.status_code}。"
            ) from exc
        except httpx.TimeoutException as exc:
            raise FreshRSSTimeoutError("FreshRSS 登录请求超时。") from exc
        except httpx.RequestError as exc:
            raise FreshRSSConnectionError("无法连接到 FreshRSS。") from exc

        # 3. ClientLogin 用 key=value 文本行返回（非 JSON）；partition 能安全处理
        #    值里可能出现的 '='
        login_fields: dict[str, str] = {}
        for line in response.text.splitlines():
            # 使用partition ，=号也会被保留下来，而且也是只切从左到右第一个=号
            key, separator, value = line.partition("=")
            if separator:
                login_fields[key] = value

        # 4. 从响应里取出 Auth token，用 SecretStr 包住（打印对象时不会泄露）
        auth_token = login_fields.get("Auth")
        if not auth_token:
            raise FreshRSSAuthenticationError(
                "FreshRSS 登录响应中没有 Auth 令牌。"
            )

        self._auth_token = SecretStr(auth_token)

    async def fetch_category_item_ids(
        self,
        *,
        category: str,
        limit: int = 1,
    ) -> list[str]:
        """
        根据传入的str分类，获取limit数量的freshRSS数据
        从新到旧

        Args:
            category: FreshRSS 中可见的分类名称，例如 ``财经``。客户端会把它
                转换成 Google Reader Stream ID；HTTPX 会负责中文 URL 编码。
            limit: 本次从该分类最多返回的文章数量，必须大于零。

        Returns:
            指定分类中的 FreshRSS 文章 ID 列表。

        Notes:
            本方法没有提供读取总阅读列表的默认值。调用方必须明确给出分类，
            这样将来用户在 FreshRSS 新增个人分类时，不会被服务意外同步。
        """

        normalized_category = category.strip()
        if not normalized_category:
            raise ValueError("category 不能为空")
        if limit < 1:
            raise ValueError("limit 必须大于零")

        stream_id = f"{self._CATEGORY_STREAM_PREFIX}{normalized_category}"
        return await self._fetch_stream_item_ids(stream_id=stream_id, limit=limit)

    async def fetch_subscription_item_ids(
        self,
        *,
        subscription_id: str,
        limit: int = 1,
    ) -> list[str]:
        """
        根据freshRss的分类id，获取limit条数据
        从新到旧
        Args:
            subscription_id: ``subscription/list`` 返回的订阅 ID，例如 ``feed/12``。
                调用方应先根据分类白名单筛选订阅，不能用本方法绕过同步范围。
            limit: 本次从该订阅源最多返回的文章数量，必须大于零。

        Returns:
            指定订阅源中的 FreshRSS 文章 ID 列表。

        Notes:
            “按分类取两篇”可能全部来自分类中更新最频繁的 Feed；按订阅源取两篇
            才能保证低频来源也参与小批量导入验证。
        """

        normalized_subscription_id = subscription_id.strip()
        if not normalized_subscription_id.startswith("feed/"):
            raise ValueError("subscription_id 必须以 'feed/' 开头")
        if limit < 1:
            raise ValueError("limit 必须大于零")
        return await self._fetch_stream_item_ids(
            stream_id=normalized_subscription_id,
            limit=limit,
        )

    async def fetch_subscription_item_id_page(
        self,
        *,
        subscription_id: str,
        limit: int,
        continuation: str | None = None,
        order: str = "newest",
    ) -> FreshRSSItemIdPage:
        """读取订阅源的一页文章 ID，并保留 FreshRSS continuation 游标。

        Args:
            subscription_id: ``subscription/list`` 返回的 ``feed/...`` ID。
            limit: 本页最多读取的文章数；调用方的 ``limit_per_source`` 安全上限仍由
                上层校验，本方法不会自行扩大它。
            continuation: 上一次成功持久化的十进制游标；首次读取传 ``None``。
            order: ``newest`` 读取最近文章，``oldest`` 按 continuation 从旧到新追赶。

        Returns:
            已校验的文章 ID 元组和可供下一页使用的 numeric continuation。

        Raises:
            ValueError: 来源 ID、limit、order 或 continuation 不合法。
            FreshRSSProtocolError: 响应缺少或包含损坏的 itemRefs/continuation。
            FreshRSSError: FreshRSS 认证、网络、超时或 HTTP 请求失败。

        Notes:
            这是 FreshRSS 只读网络 I/O。FreshRSS 的 ``c`` 游标由服务端 entry ID
            定义，不能用文章发布时间或 Python 偏移量替代；调用方只有在对应页面的
            新闻成功写入 PostgreSQL 后才可以保存返回的 continuation。
        """

        normalized_subscription_id = subscription_id.strip()
        if not normalized_subscription_id.startswith("feed/"):
            raise ValueError("subscription_id 必须以 'feed/' 开头")
        if limit < 1:
            raise ValueError("limit 必须大于零")
        if order not in {"newest", "oldest"}:
            raise ValueError("order 必须是 'newest' 或 'oldest'")
        if continuation is not None:
            normalized_continuation = continuation.strip()
            if (
                not normalized_continuation
                or not normalized_continuation.isascii()
                or not normalized_continuation.isdecimal()
            ):
                raise ValueError("continuation 必须是十进制字符串")
            continuation = str(int(normalized_continuation))

        return await self._fetch_stream_item_id_page(
            stream_id=normalized_subscription_id,
            limit=limit,
            continuation=continuation,
            order=order,
        )

    async def _fetch_stream_item_ids(
        self,
        *,
        stream_id: str,
        limit: int,
    ) -> list[str]:
        """
        读取freshRss 某个分类 limit 条数据

        Args:
            stream_id: Google Reader API Stream ID。它可以表示分类，也可以表示
                单个 Feed；该值只能由已经完成业务校验的公开方法构造或传入。
            limit: 最多读取的 ID 数量。公开方法已经保证它大于零。

        Returns:
            FreshRSS 按从新到旧顺序返回的文章 ID。

        Raises:
            FreshRSSProtocolError: 响应不是包含 ``itemRefs`` 列表的协议对象。
            FreshRSSError: 认证、网络或 HTTP 请求失败。
        """

        # 读取某个分类的一页数据，一页limit条数据
        page = await self._fetch_stream_item_id_page(
            stream_id=stream_id,
            limit=limit,
            continuation=None,
            order="newest",
        )
        return list(page.item_ids)

    async def _fetch_stream_item_id_page(
        self,
        *,
        stream_id: str,
        limit: int,
        continuation: str | None,
        order: str,
    ) -> FreshRSSItemIdPage:
        """
        读取FreshRSS板块的一页数据。

        Args:
            stream_id: 已由公开方法校验的 Google Reader Stream ID。
            limit: 本页最大文章数。
            continuation: 可选 numeric continuation。
            order: ``newest`` 或 ``oldest``。

            oldest / newest 是“排序方向”，不是“筛选条件”：
            order="newest"（r=n）：从最新的文章开始往下取 N 条。
            order="oldest"（r=o）：从最早的文章开始往新取 N 条。

        Returns:
            结构化 ``FreshRSSItemIdPage``。

        Raises:
            FreshRSSProtocolError: itemRefs 不是严格的对象列表或响应模型校验失败。
            FreshRSSError: 认证、网络、超时或 HTTP 请求失败。
        """

        response = await self._request(
            "GET",
            self._ITEM_IDS_PATH,
            params={
                "s": stream_id,
                "n": str(limit),
                "r": "o" if order == "oldest" else "n",
                "output": "json",
                **({"c": continuation} if continuation is not None else {}),
            },
        )
        payload = self._read_json_object(response)
        item_refs = payload.get("itemRefs")
        if not isinstance(item_refs, list):
            raise FreshRSSProtocolError(
                "FreshRSS 条目 ID 响应缺少 itemRefs 列表。"
            )

        item_ids: list[str] = []
        for item_ref in item_refs:
            if not isinstance(item_ref, dict) or not isinstance(
                item_ref.get("id"), str
            ):
                raise FreshRSSProtocolError(
                    "FreshRSS 条目 ID 响应包含无效的 itemRef。"
                )
            item_ids.append(item_ref["id"])

        try:
            return FreshRSSItemIdPage(
                item_ids=tuple(item_ids),
                continuation=payload.get("continuation"),
            )
        except ValidationError as exc:
            raise FreshRSSProtocolError(
                "FreshRSS 条目 ID 响应不符合预期的页面结构。"
            ) from exc

    async def fetch_configured_category_item_ids(
        self,
        *,
        limit_per_category: int = 1,
    ) -> list[str]:
        """
        读取白名单分类的freshRSS数据，返回文章id

        Args:
            limit_per_category: 每个允许分类最多读取多少条，而不是所有分类合计
                的数量。这个语义能避免文章很多的分类挤掉更新较少的分类。

        Returns:
            按配置分类顺序合并后的文章 ID。若同一订阅属于多个允许分类，文章
            可能在多个 Stream 中出现，本方法会保留第一次出现并删除重复 ID。
        """

        if limit_per_category < 1:
            raise ValueError("limit_per_category 必须大于零")

        unique_item_ids: list[str] = []
        seen_item_ids: set[str] = set()
        # 获取配置白名单的分类 其实就是FreshRSS的分板块
        for category in self._settings.sync_categories:
            category_item_ids = await self.fetch_category_item_ids(
                category=category,
                limit=limit_per_category,
            )
            for item_id in category_item_ids:
                if item_id not in seen_item_ids:
                    seen_item_ids.add(item_id)
                    unique_item_ids.append(item_id)

        return unique_item_ids

    async def fetch_items(self, item_ids: Sequence[str]) -> list[FreshRSSItem]:
        """
        根据文章id获取FreshRSS,返回list[FreshRSSItem]

        Args:
            item_ids: 要批量读取的 FreshRSS 文章 ID。空序列不会发送 HTTP 请求，
                而是直接返回空列表。

        Returns:
            经过 Pydantic 字段和类型校验的外部协议对象列表。

        Raises:
            FreshRSSProtocolError: FreshRSS JSON 结构或文章字段不符合预期。
            FreshRSSError: 认证、网络或 HTTP 请求失败。
        """

        if not item_ids:
            return []

        response = await self._request(
            "POST",
            self._ITEM_CONTENTS_PATH,
            # HTTPX 会把列表编码为多个同名 i 字段，符合 Google Reader API。
            data={"i": list(item_ids)},
        )
        payload = self._read_json_object(response)
        items = payload.get("items")
        if not isinstance(items, list):
            raise FreshRSSProtocolError("FreshRSS 条目响应缺少 items 列表。")

        try:
            # .model_validate 将任意 Python 对象（如字典、类实例等）验证并转换为当前 Pydantic 模型的实例。
            # Pydantic 模型就是 FreshRSSItem 这个类这种
            return [FreshRSSItem.model_validate(item) for item in items]
        except ValidationError as exc:
            raise FreshRSSProtocolError(
                "FreshRSS 条目响应不符合预期结构。"
            ) from exc

    async def fetch_latest_article(self) -> FreshRSSItem | None:
        """获取白名单分类中最新的一篇外部协议对象；没有文章时返回 ``None``。

        每个分类先取最新一个 ID，然后批量读取内容并比较发布时间。这样即使配置
        了多个分类，也不会退回读取 FreshRSS 的总阅读列表。

        Returns:
            所有允许分类中发布时间或抓取时间最新的文章；没有 ID 时返回
            ``None``。

        Raises:
            FreshRSSProtocolError: FreshRSS 返回 ID 却没有对应文章内容时抛出。
            FreshRSSError: 认证、网络或 HTTP 请求失败时抛出。
        """

        # 获取白名单分类中，各自的最新一条数据的文章id集合
        item_ids = await self.fetch_configured_category_item_ids(limit_per_category=1)
        if not item_ids:
            return None

        # 根据文章id获取文章对象
        items = await self.fetch_items(item_ids)
        if not items:
            raise FreshRSSProtocolError(
                "FreshRSS 返回了条目 ID 但没有对应的文章内容。"
            )

        # published 是来源声明的秒级发布时间；缺失时再使用 FreshRSS 抓取时间。
        # int(... or 0) 同时兼容 API 把 timestampUsec/crawlTimeMsec 返回为字符串。
        def item_timestamp(item: FreshRSSItem) -> int:
            """把不同精度的时间统一成微秒整数，供 ``max`` 比较。

            Args:
                item: 要提取排序时间的 FreshRSS 文章。

            Returns:
                优先使用发布时间、再使用抓取时间的 Unix 微秒整数；文章没有
                任何时间字段时返回零。
            """

            if item.published is not None:
                return item.published * 1_000_000
            if item.timestamp_usec is not None:
                return int(item.timestamp_usec)
            if item.crawl_time_msec is not None:
                return int(item.crawl_time_msec) * 1_000
            return 0

        return max(items, key=item_timestamp)

    async def fetch_subscriptions(self) -> list[FreshRSSSubscription]:
        """获取 FreshRSS 订阅列表，用于补全 Feed URL 和用户分类。

        Returns:
            经过 Pydantic 校验的全部 FreshRSS 订阅协议对象。

        Raises:
            FreshRSSProtocolError: 响应结构或订阅字段不符合预期时抛出。
            FreshRSSError: 认证、网络或 HTTP 请求失败时抛出。
        """

        response = await self._request(
            "GET",
            self._SUBSCRIPTION_LIST_PATH,
            params={"output": "json"},
        )
        payload = self._read_json_object(response)
        subscriptions = payload.get("subscriptions")
        if not isinstance(subscriptions, list):
            raise FreshRSSProtocolError(
                "FreshRSS 订阅响应缺少 subscriptions 列表。"
            )

        try:
            return [
                FreshRSSSubscription.model_validate(subscription)
                for subscription in subscriptions
            ]
        except ValidationError as exc:
            raise FreshRSSProtocolError(
                "FreshRSS 订阅响应不符合预期结构。"
            ) from exc

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """发送带认证信息的请求，并统一转换网络和 HTTP 错误。

        Args:
            method: HTTP 方法，例如 ``GET`` 或 ``POST``。
            path: 相对于 FreshRSS API 根地址的路径。
            **kwargs: 透传给 ``httpx.AsyncClient.request`` 的查询参数、表单等选项。

        Returns:
            已通过 HTTP 状态检查的 HTTPX 响应对象。

        Raises:
            FreshRSSError: FreshRSS 返回错误状态码或底层网络请求失败。
            FreshRSSAuthenticationError: 首次请求触发认证且凭据无效。
        """

        # 1. 首次请求自动先登录拿 Token（懒登录）
        if self._auth_token is None:
            await self.authenticate()

        # 2. 带上 GoogleLogin 认证头请求；所有真实 HTTP 请求都集中走这里
        headers = {
            "Authorization": (f"GoogleLogin auth={self._auth_token.get_secret_value()}")
        }

        try:
            response = await self._http_client.request(
                method,
                path,
                headers=headers,
                **kwargs,
            )
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            # 3. 按 HTTP 状态分类成稳定领域异常，不让响应正文/凭据外泄
            if exc.response.status_code in {401, 403}:
                raise FreshRSSAuthenticationError(
                    "FreshRSS API 身份验证被拒绝。"
                ) from exc
            if exc.response.status_code in {408, 504}:
                raise FreshRSSTimeoutError("FreshRSS API 请求超时。") from exc
            raise FreshRSSServiceError(
                f"FreshRSS API 返回 HTTP {exc.response.status_code}。"
            ) from exc
        except httpx.TimeoutException as exc:
            raise FreshRSSTimeoutError("FreshRSS API 请求超时。") from exc
        except httpx.RequestError as exc:
            raise FreshRSSConnectionError("FreshRSS API 请求失败。") from exc

    @staticmethod
    def _read_json_object(response: httpx.Response) -> dict[str, Any]:
        """解析 JSON 对象，并把格式异常转换成领域明确的协议错误。

        Args:
            response: 已通过 HTTP 状态检查的 HTTPX 响应。

        Returns:
            顶层为 JSON object 的字典。

        Raises:
            FreshRSSProtocolError: 响应不是合法 JSON 或顶层不是 object 时抛出。
        """

        try:
            payload = response.json()
        except ValueError as exc:
            raise FreshRSSProtocolError("FreshRSS 返回了无效的 JSON。") from exc

        if not isinstance(payload, dict):
            raise FreshRSSProtocolError("FreshRSS JSON 响应不是对象。")

        return payload
