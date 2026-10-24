"""算出「换容器之前能安全跑到哪一条迁移」。

这个边界之所以存在，是因为部署顺序是「先起新、后停旧」：新镜像的迁移在旧容器还在排空时就跑完了，
而从那一刻到旧容器完全退出之间，旧容器的代码还在读同一个库。删列一类的迁移会让旧代码读到一个
已经不存在的列，直接把还在排空的旧容器打死——最坏的组合就是这样出现的：新容器没起来，旧容器
又被自己身后跑过的迁移弄坏，两端都没了。把删列推到旧容器消失之后再跑，代价只是那段时间里库结构
比代码新一点；反过来先删列，则没有任何时刻能退回旧版本。

它算出来的只是**一个给部署脚本读的值**：``python -m agent_lab.scripts.alembic_pre_head`` 把「兼容段」
的最后一个 revision 打印出来，脚本拿它去 ``alembic upgrade <它>``；旧容器退出、新容器就绪之后
再 ``alembic upgrade head`` 把剩下的跑完。业务代码不依赖这个模块，运行期任何地方都不该 import 它，
迁移文件也不该反向依赖它——它只服务部署顺序。

标记的写法：在迁移文件里写一行模块级常量 ``POST_DEPLOY = True``，位置放在文件顶部 docstring
之后、import 之前。没写这一行的迁移一律按「向后兼容」对待，会在旧容器还在服务时就跑掉。
"""

import os
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory


# 配置文件路径默认与仓库里 alembic 命令的默认值一致：在 backend/ 目录下直接跑。
DEFAULT_CONFIG_PATH = "alembic.ini"
# 部署脚本换一份 ini（比如用量库那条链）时用这个环境变量指定，不引入命令行参数解析。
CONFIG_PATH_ENV = "ALEMBIC_CONFIG"


def pre_deploy_head(script: ScriptDirectory) -> str | None:
    """返回换容器之前可以安全跑到的最后一个 revision。

    Args:
        script: 已经指向版本目录的 Alembic ``ScriptDirectory``。

    Returns:
        从 head 往回走（``walk_revisions`` 沿 ``down_revision`` 下行）遇到的第一个没有
        ``POST_DEPLOY`` 标记的 revision，也就是「兼容段」的链尾；整条链都带标记时返回
        ``None``，表示换容器之前什么都不该跑。

    Raises:
        SystemExit: 迁移链有多个 head。多链时「新旧代码都能读的那一段」没有唯一答案，
            猜一个比直接报错更危险，所以在这里停下并带上实际的 heads 列表。
    """

    heads = list(script.get_heads())
    if len(heads) != 1:
        raise SystemExit(
            f"迁移链有 {len(heads)} 个 head（{sorted(heads)}），无法确定换容器的边界："
            "多链时兼容段没有唯一答案，先合成一条链再看。"
        )

    # 1、从 head 往下走，跳过所有打了 POST_DEPLOY 标记的迁移。
    for revision in script.walk_revisions():
        if not getattr(revision.module, "POST_DEPLOY", False):
            # 2、第一个不带标记的就是兼容段的链尾。
            return revision.revision

    # 3、整条链都带标记：兼容段为空。
    return None


def from_config(config_path: str) -> str | None:
    """按 ini 路径算出兼容段的链尾。

    Args:
        config_path: Alembic 配置文件路径（``script_location`` 由它自己解析）。

    Returns:
        与 :func:`pre_deploy_head` 相同的 revision，或 ``None``。
    """

    config = Config(config_path)
    return pre_deploy_head(ScriptDirectory.from_config(config))


def main() -> int:
    """把兼容段的链尾打印到 stdout，供部署脚本直接取用。

    Returns:
        恒为 0；结果为 ``None`` 时什么都不打印，脚本侧拿到空串自己决定怎么处理。
    """

    config_path = os.environ.get(CONFIG_PATH_ENV) or DEFAULT_CONFIG_PATH
    revision = from_config(config_path)
    if revision is not None:
        print(revision)
    return 0


if __name__ == "__main__":
    sys.exit(main())
