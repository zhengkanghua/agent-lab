"""上游渠道凭据的加密：Fernet 对称加密，主密钥来自环境变量。

**加解密器在真的要用密钥的那一刻才构造**。检索、文档处理这些不碰凭据的路径完全不经过本模块，
所以没配 ``LLM_CREDENTIAL_KEY`` 的部署照样起得来；密钥缺失或不是合法 Fernet 密钥时抛
``LlmCredentialKeyUnavailableError``，由 HTTP 层映射成 503，不在启动阶段炸。

明文只有两条短路径：写入侧「调用方传入 → 加密 → 返回密文」，读取侧「密文 → 解密 → 交给客户端
构造函数」。本模块不写日志、不把明文写进异常消息；失败时也不转发底层异常文本
（``raise ... from None``），因为它是从密钥算出来的错误。
"""

from cryptography.fernet import Fernet, InvalidToken

from agent_lab.config.llm_credential import get_llm_credential_settings


class LlmCredentialKeyUnavailableError(RuntimeError):
    """凭据主密钥没有配置、不是合法的 Fernet 密钥，或解不开已存的密文。

    单独一个异常类型而不是 ``ValueError``：HTTP 层只按异常类型就能把它翻成
    ``llm_catalog_unavailable``，不必读取任何文本——读文本意味着那段文字要经过日志与响应，
    而它的来源是密钥本身。

    最后一种情况（解不开）也是「密钥不对」：换掉主密钥之后，之前用旧密钥写下的密文再也
    解不回明文，那些渠道的凭据要重新填一遍（见 ``backend/.env.example`` 的
    ``LLM_CREDENTIAL_KEY``）。它与「没配密钥」对使用者是同一件事——要去部署里处理密钥——
    所以共用一个异常，不另开一条。
    """


class CredentialCipher:
    """把渠道凭据在明文与 Fernet 密文之间转换。

    Notes:
        只持有 ``Fernet`` 实例（内含主密钥），不持有任何明文；实例无状态，可跨请求复用。
    """

    def __init__(self, key: str) -> None:
        """按主密钥构造加解密器。

        Args:
            key: urlsafe base64 编码的 32 字节 Fernet 密钥。

        Raises:
            LlmCredentialKeyUnavailableError: 密钥不是合法 Fernet 密钥（长度不对或不是
                urlsafe base64）。不转发底层 ``ValueError`` 的文本。
        """

        try:
            self._fernet = Fernet(key.encode("utf-8"))
        # Fernet 对长度不对抛 ValueError，对非法 base64 抛 binascii.Error（它是 ValueError
        # 的子类），对非 ASCII 字符抛 UnicodeEncodeError（也是 ValueError 的子类）；三类都
        # 只有一个含义——这个密钥不能用。
        except ValueError:
            raise LlmCredentialKeyUnavailableError() from None

    def encrypt(self, credential: str) -> str:
        """把凭据明文加密成可落库的密文字符串。

        Args:
            credential: 凭据明文，只在本次调用的入参与返回值之间存活。

        Returns:
            ASCII 的 Fernet token，可直接写入 ``llm_providers.credential_ciphertext``。

        Notes:
            纯内存运算，不写日志、不执行 I/O。
        """

        return self._fernet.encrypt(credential.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        """把落库的密文解回凭据明文，用于按渠道构造模型客户端。

        Args:
            ciphertext: ``llm_providers.credential_ciphertext`` 里那一串 Fernet token。

        Returns:
            凭据明文；调用方把它直接交给客户端构造函数，之后不再持有。

        Raises:
            LlmCredentialKeyUnavailableError: 密文不是主密钥加密出来的（换过密钥、或密文
                被改过）。不转发底层 ``InvalidToken``，理由与构造时那条相同：它的文本来自
                密钥这一侧，不该经过日志与响应。

        Notes:
            纯内存运算，不写日志、不执行 I/O。
        """

        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError):
            raise LlmCredentialKeyUnavailableError() from None


def build_credential_cipher() -> CredentialCipher:
    """按当前环境构造加解密器。

    Returns:
        用 ``LLM_CREDENTIAL_KEY`` 构造的 ``CredentialCipher``。

    Raises:
        LlmCredentialKeyUnavailableError: 主密钥留空或不是合法 Fernet 密钥。

    Notes:
        每次调用都新建一个实例；密钥只在这一步从配置里取出，不从调用方传入。
    """

    key = get_llm_credential_settings().credential_key.get_secret_value()
    if not key.strip():
        raise LlmCredentialKeyUnavailableError()
    return CredentialCipher(key)


__all__ = [
    "CredentialCipher",
    "LlmCredentialKeyUnavailableError",
    "build_credential_cipher",
]
