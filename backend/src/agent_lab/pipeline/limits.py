"""定义 CLI 与手动 HTTP 流水线共同使用的有界执行参数。

本模块位于应用边界的共享配置层，只保存不会访问环境或外部服务的常量。它不解析
HTTP/CLI 输入、不执行同步或索引，也不表达自动调度频率。
"""

# 这些常量是 CLI 和手动 HTTP 流水线「共同」用的安全上限，保证每次手动执行都是
# 有界工作量。修改它们 = 同时改了 CLI 和 HTTP 的默认/上限。

DEFAULT_LIMIT_PER_SOURCE = 2
MAX_LIMIT_PER_SOURCE = 100
DEFAULT_INDEX_BATCH_SIZE = 20
MAX_INDEX_BATCH_SIZE = 1000
DEFAULT_STALE_AFTER_MINUTES = 60
MAX_STALE_AFTER_MINUTES = 7 * 24 * 60


__all__ = [
    "DEFAULT_INDEX_BATCH_SIZE",
    "DEFAULT_LIMIT_PER_SOURCE",
    "DEFAULT_STALE_AFTER_MINUTES",
    "MAX_INDEX_BATCH_SIZE",
    "MAX_LIMIT_PER_SOURCE",
    "MAX_STALE_AFTER_MINUTES",
]
