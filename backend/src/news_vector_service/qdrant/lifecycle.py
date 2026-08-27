"""管理 Qdrant 物理 Collection、Payload index 和 current Alias 的生命周期。

先分清两个概念（本项目最重要的设计之一）：
- 物理 Collection：真正保存 Point/向量/Payload 的地方，名字带版本号
  （news_chunks_langchain_v1_001）；
- current Alias：一个「指针/别名」，应用永远通过它读写，部署时统一切换到新的
  物理 Collection，实现零停机的索引重建。

本模块是「唯一」允许操作物理 Collection 名称的边界：负责创建/校验 Collection、
建 Payload 过滤索引、原子切换 Alias。它不写新闻 Point、不生成 Embedding、不搜索、
不改 PostgreSQL processing_status。
"""

from collections.abc import Mapping
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from news_vector_service.config.qdrant import QdrantSettings
from news_vector_service.qdrant.index_spec import (
    VectorIndexConfigurationError,
    VectorIndexSpec,
)


class QdrantLifecycleError(RuntimeError):
    """Collection 或 Alias 生命周期操作失败。"""


class QdrantAliasConflictError(QdrantLifecycleError):
    """current Alias 已指向另一套物理 Collection，需要显式切换或排查。"""


PAYLOAD_INDEX_SCHEMAS: Mapping[str, models.PayloadSchemaType] = {
    # Qdrant 的 grouped query 要求 group_by 字段具备 keyword/integer 索引；
    # Payload 仍保存规范化 UUID 字符串，keyword 同时支持按文档精确过滤。
    "document_id": models.PayloadSchemaType.KEYWORD,
    "source_id": models.PayloadSchemaType.UUID,
    "source_provider": models.PayloadSchemaType.KEYWORD,
    "document_type": models.PayloadSchemaType.KEYWORD,
    "published_at": models.PayloadSchemaType.DATETIME,
    "labels": models.PayloadSchemaType.KEYWORD,
}


# qdrant 的 client 连接池
def build_qdrant_client(settings: QdrantSettings) -> AsyncQdrantClient:
    """依据配置创建异步 Qdrant client，但不发起网络请求。

    Args:
        settings: 已校验的 Qdrant URL、可选密钥和超时配置。

    Returns:
        尚未执行请求的 ``AsyncQdrantClient``。

    Notes:
        API Key 为空时传入 ``None``，不会构造空认证 header。客户端生命周期由应用
        进程或调用方管理；``port=None`` 保留完整 URL 的反代端口语义。本函数不创建
        Collection 或 Alias，也不执行兼容性探测。
    """

    secret = settings.api_key.get_secret_value().strip()
    return AsyncQdrantClient(
        url=str(settings.base_url),
        # qdrant-client 1.19.0 的 ``port`` 默认值是 6333，即使 ``url`` 已是完整 HTTPS
        # 反代地址也会被改写成 ``https://host:6333``。传 None 才会保留 URL 自带端口，
        # 或在未显式写端口时使用 HTTPS 443 / HTTP 80。
        port=None,
        api_key=secret or None,
        timeout=settings.request_timeout_seconds,
        # qdrant-client 1.19.0 默认在构造时启动后台线程探测服务端版本，这会让
        # “构造 Runtime”产生不可等待、不可分类的隐式网络 I/O。项目把所有外部访问
        # 留在显式 lifecycle/search 方法中，因此关闭这项非业务探测。
        check_compatibility=False,
    )


class QdrantCollectionLifecycle:
    """集中执行物理 Collection 创建、规格校验和 current Alias 切换。

    核心约束：应用运行时（写 Point / 搜索）只能使用 Alias；只有本类在创建/重建时
    直接使用物理 Collection 名。这样「数据去哪了、Alias 指向谁」只有一个地方能改，
    其他地方想绕过也绕不过（拿不到物理名）。
    实例可复用；每个方法的 Qdrant 网络 I/O 都是异步的，不做 PostgreSQL/Embedding I/O。
    """

    def __init__(
        self,
        client: AsyncQdrantClient,
        settings: QdrantSettings,
        spec: VectorIndexSpec,
    ) -> None:
        """绑定 client、命名配置和不可变索引规格。

        Args:
            client: 已构造的异步 Qdrant client，可在测试中注入 fake。
            settings: 提供物理 Collection 名和 current Alias。
            spec: 用于创建和校验向量维度、Distance、版本 metadata 的规格。
        """

        self._client = client
        self._settings = settings
        self._spec = spec

    @property
    def collection_name(self) -> str:
        """返回生命周期操作使用的物理 Collection 名称。"""

        return self._settings.collection_name

    @property
    def collection_alias(self) -> str:
        """返回应用数据读写使用的 current Alias 名称。"""

        return self._settings.collection_alias

    async def ensure_current_collection(self) -> str:
        """创建或校验当前物理 Collection，并确保 current Alias 正确指向它。

        基本逻辑：
            1. 查询 current Alias 当前指向哪个物理 Collection
            2. 如果 Alias 指向了错误的 Collection，报冲突
            3. 检查目标物理 Collection 是否存在
            4. 不存在：创建 Collection
            5. 存在：读取 Collection 配置并校验
            6. 检查 Payload index 是否存在
            7. 缺少的 index 创建出来
            8. 确保 current Alias 指向目标 Collection

        Returns:
            当前物理 Collection 名称；调用方通常不应使用它进行业务读写。

        Raises:
            QdrantAliasConflictError: current Alias 已指向另一 Collection。
            QdrantLifecycleError: 创建、读取、索引或 Alias 操作失败。
            VectorIndexConfigurationError: 已有 Collection 的规格不匹配。

        Notes:
            这是 Qdrant 网络 I/O，但不写入新闻 Point。首次调用可以创建 Collection
            和 Payload index；后续调用只校验，不会删除或重建已有数据。
        """

        # 1. 查 current Alias 现在指向谁；指向了别的 Collection → 冲突，显式报错
        alias_target = await self._alias_target(self.collection_alias)
        # 1.2. 与传入的collection_name对比
        if alias_target is not None and alias_target != self.collection_name:
            raise QdrantAliasConflictError(
                f"Alias {self.collection_alias!r} 指向 {alias_target!r}，"
                f"而不是期望的 {self.collection_name!r}。"
            )
        # 2. 创建（或校验）物理 Collection + Payload index
        await self.ensure_collection(self.collection_name)
        # 3. Alias 还没建过 → 创建它指向物理 Collection（幂等：已指向就直接用）
        if alias_target is None:
            await self._create_alias(self.collection_alias, self.collection_name)
        return self.collection_name

    async def ensure_collection(self, collection_name: str) -> None:
        """创建或校验一个物理 Collection，并确保其 Payload index 存在。

        Args:
            collection_name: 仅限生命周期管理使用的物理 Collection 名称；不能传
                current Alias。重建时可传入新的 generation 名称。

        Raises:
            QdrantLifecycleError: Collection 创建或 Payload index 操作失败。
            VectorIndexConfigurationError: 已有 Collection 维度、Distance 或 metadata 不符。

        Notes:
            该方法进行 Qdrant 网络 I/O，但不会创建 Alias，也不会写入 Point。禁止业务
            Service 直接调用它进行日常数据写入。
        """

        if collection_name == self.collection_alias:
            raise QdrantLifecycleError(
                "物理集合的生命周期管理不能创建或校验当前 Alias。"
            )
        try:
            # 1. 不存在才创建（幂等）：按规格建向量配置 + 可审计 metadata
            # 检查是否有存在这个collection_name
            exists = await self._client.collection_exists(collection_name)
            if not exists:
                # 不存在，则创建collection
                created = await self._client.create_collection(
                    collection_name=collection_name,
                    vectors_config=self._spec.vector_params,
                    metadata=self._spec.collection_metadata,
                    timeout=int(self._settings.request_timeout_seconds),
                )
                if not created:
                    raise QdrantLifecycleError(
                        f"Qdrant 未创建集合 {collection_name!r}。"
                    )
            # 2. 已存在则校验：维度/距离/metadata 必须和规格一致（不一致就停，不自动删）
            # 获取collections
            info = await self._client.get_collection(collection_name)
            # 调用封装好的校验函数
            self._spec.validate_collection_info(info)
            # 3. 补齐过滤用的 Payload index（已有但类型错的会拒绝）
            # 建立索引
            await self._ensure_payload_indexes(collection_name, info)
        except (QdrantLifecycleError, VectorIndexConfigurationError):
            raise
        except Exception as exc:
            raise QdrantLifecycleError(
                f"Qdrant 集合生命周期操作失败（{collection_name!r}）："
                f"{type(exc).__name__}。"
            ) from None

    async def switch_current_alias(self, collection_name: str) -> None:
        """原子地把 current Alias 切换到已校验的物理 Collection。

        Args:
            collection_name: 已创建且符合当前 ``VectorIndexSpec`` 的物理 Collection。

        Raises:
            QdrantLifecycleError: 目标不存在、规格不符或 Alias 更新失败。

        Notes:
            先校验目标，再以一个 Qdrant ``ChangeAliasesOperation`` 完成删除旧指向和
            创建新指向。Alias 切换是 Qdrant 原子操作；本方法不移动或复制 Point。
        """

        # 1. 目标 Collection 必须真实存在且通过规格校验
        try:
            exists = await self._client.collection_exists(collection_name)
        except Exception as exc:
            raise QdrantLifecycleError(
                f"无法检查目标集合 {collection_name!r}："
                f"{type(exc).__name__}。"
            ) from None
        if not exists:
            raise QdrantLifecycleError(
                f"不能将当前 Alias 切换到不存在的集合 {collection_name!r}。"
            )
        await self.ensure_collection(collection_name)
        # 2. 已经是目标就什么都不做（幂等）
        current_target = await self._alias_target(self.collection_alias)
        if current_target == collection_name:
            return
        # 3. 构造「删旧指向 + 建新指向」两个动作，一次原子提交给 Qdrant
        actions: list[models.CreateAliasOperation | models.DeleteAliasOperation] = []
        if current_target is not None:
            actions.append(
                models.DeleteAliasOperation(
                    delete_alias=models.DeleteAlias(alias_name=self.collection_alias)
                )
            )
        actions.append(
            models.CreateAliasOperation(
                create_alias=models.CreateAlias(
                    collection_name=collection_name,
                    alias_name=self.collection_alias,
                )
            )
        )
        try:
            updated = await self._client.update_collection_aliases(
                actions,
                timeout=int(self._settings.request_timeout_seconds),
            )
            if not updated:
                raise QdrantLifecycleError(
                    f"Qdrant 未切换 Alias {self.collection_alias!r}。"
                )
        except QdrantLifecycleError:
            raise
        except Exception as exc:
            raise QdrantLifecycleError(
                f"无法切换 Alias {self.collection_alias!r}：{type(exc).__name__}。"
            ) from None

    async def delete_collection(self, collection_name: str) -> None:
        """删除不再被 Alias 使用的物理 Collection。

        Args:
            collection_name: 待删除的物理 Collection 名称。

        Raises:
            QdrantLifecycleError: 目标仍被 Alias 指向或删除请求失败。

        Notes:
            这是破坏性运维 I/O，不会由单篇索引流程调用。删除前必须确认 current Alias
            不再指向目标，避免应用突然失去正在使用的数据。
        """

        if collection_name == self.collection_alias:
            raise QdrantLifecycleError(
                "不能把当前 Alias 当作物理集合（Collection）来删除。"
            )
        try:
            aliases = await self._client.get_collection_aliases(collection_name)
        except Exception as exc:
            raise QdrantLifecycleError(
                f"无法检查集合 {collection_name!r} 的 Alias："
                f"{type(exc).__name__}。"
            ) from None
        if any(alias.collection_name == collection_name for alias in aliases.aliases):
            raise QdrantLifecycleError(
                f"集合 {collection_name!r} 仍有 Alias 指向，不能删除。"
            )
        try:
            deleted = await self._client.delete_collection(
                collection_name,
                timeout=int(self._settings.request_timeout_seconds),
            )
            if not deleted:
                raise QdrantLifecycleError(
                    f"Qdrant 未删除集合 {collection_name!r}。"
                )
        except QdrantLifecycleError:
            raise
        except Exception as exc:
            raise QdrantLifecycleError(
                f"无法删除 Qdrant 集合 {collection_name!r}："
                f"{type(exc).__name__}。"
            ) from None

    async def _create_alias(self, alias_name: str, collection_name: str) -> None:
        """创建alias和collection的关联"""
        try:
            created = await self._client.update_collection_aliases(
                [
                    models.CreateAliasOperation(
                        create_alias=models.CreateAlias(
                            collection_name=collection_name,
                            alias_name=alias_name,
                        )
                    )
                ],
                timeout=int(self._settings.request_timeout_seconds),
            )
            if not created:
                raise QdrantLifecycleError(
                    f"Qdrant 未创建 Alias {alias_name!r}。"
                )
        except QdrantLifecycleError:
            raise
        except Exception as exc:
            raise QdrantLifecycleError(
                f"无法创建 Qdrant Alias {alias_name!r}：{type(exc).__name__}。"
            ) from None

    async def _alias_target(self, alias_name: str) -> str | None:
        """"
        查 current Alias 现在指向谁

        下面会遍历所有的aliases
        """
        try:
            # 获取client的Alias 集合，一个向量数据库可以配置多个物理collection和alias
            aliases = await self._client.get_aliases()
        except Exception as exc:
            raise QdrantLifecycleError(
                f"无法读取 Qdrant Alias {alias_name!r}：{type(exc).__name__}。"
            ) from None
        # 获取到alias 集合进行遍历，找到我们需要的那个，看看对应的collection是不是我们需要的。
        for alias in aliases.aliases:
            if alias.alias_name == alias_name:
                return alias.collection_name
        return None

    async def _ensure_payload_indexes(
        self,
        collection_name: str,
        info: models.CollectionInfo,
    ) -> None:
        """
        就是建立索引的方法。
        建立过滤/分组索引，并迁移旧 document_id UUID 索引。
        """

        for field_name, expected_type in PAYLOAD_INDEX_SCHEMAS.items():
            # 看这个字段现在有没有索引
            existing = info.payload_schema.get(field_name)
            if existing is not None:
                # 已有索引：看类型对不对
                if existing.data_type != expected_type:
                    # document_id 特例：旧的 UUID 索引可逆迁移成 keyword
                    if (
                        field_name == "document_id"
                        and existing.data_type == models.PayloadSchemaType.UUID
                        and expected_type == models.PayloadSchemaType.KEYWORD
                    ):
                        await self._replace_payload_index(
                            collection_name,
                            field_name,
                            expected_type,
                        )
                        continue
                    raise VectorIndexConfigurationError(
                        f"Payload 索引类型不匹配（{field_name!r}）：期望 "
                        f"{expected_type.value}，实际 {existing.data_type.value}。"
                    )
                continue
            # 没有索引则创建索引
            await self._create_payload_index(collection_name, field_name, expected_type)

    async def _replace_payload_index(
        self,
        collection_name: str,
        field_name: str,
        expected_type: models.PayloadSchemaType,
    ) -> None:
        """删除一个已知旧类型索引后，以当前规格原地重建。"""

        try:
            deleted = await self._client.delete_payload_index(
                collection_name=collection_name,
                field_name=field_name,
                wait=True,
                timeout=int(self._settings.request_timeout_seconds),
            )
            if deleted.status != models.UpdateStatus.COMPLETED:
                raise QdrantLifecycleError(
                    f"旧版 Payload 索引 {field_name!r} 删除未完成："
                    f"status={deleted.status.value}。"
                )
            await self._create_payload_index(
                collection_name,
                field_name,
                expected_type,
            )
        except QdrantLifecycleError:
            raise
        except Exception as exc:
            raise QdrantLifecycleError(
                f"无法替换 {field_name!r} 的 Payload 索引："
                f"{type(exc).__name__}。"
            ) from None

    async def _create_payload_index(
        self,
        collection_name: str,
        field_name: str,
        expected_type: models.PayloadSchemaType,
    ) -> None:
        """创建一个 Payload index，并要求同步等待结果为 completed。"""

        try:
            result = await self._client.create_payload_index(
                collection_name=collection_name,
                field_name=field_name,
                field_schema=expected_type,
                wait=True,
                timeout=int(self._settings.request_timeout_seconds),
            )
            if result.status != models.UpdateStatus.COMPLETED:
                raise QdrantLifecycleError(
                    f"Payload 索引 {field_name!r} 操作未完成："
                    f"status={result.status.value}。"
                )
        except QdrantLifecycleError:
            raise
        except Exception as exc:
            raise QdrantLifecycleError(
                f"无法为 {field_name!r} 创建 Payload 索引："
                f"{type(exc).__name__}。"
            ) from None
