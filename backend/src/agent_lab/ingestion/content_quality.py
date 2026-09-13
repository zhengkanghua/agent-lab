"""FreshRSS 标题和 Docling HTML 正文共用的确定性文本规则。

这里只规范化单个文本块、生成标题比较键；正文是否可用及段落去重由结构解析负责，
不另设一套纯文本质量状态或最小长度门槛。
"""

from html import unescape
import unicodedata


def normalize_inline_text(value: str) -> str:
    """解码 entity，统一 NFC，并把块内 Unicode 空白压缩成普通空格。"""

    normalized = unicodedata.normalize("NFC", unescape(value))
    return " ".join(normalized.split())


def title_comparison_key(value: str) -> str:
    """生成仅用于正文首尾完整块与标题比较的键。

    忽略 Unicode 标点、大小写和空白，保留字母、数字和符号；调用方负责限定比较位置，
    不能用此键删除正文中的近似句子。
    """

    comparable = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in comparable
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
    )
