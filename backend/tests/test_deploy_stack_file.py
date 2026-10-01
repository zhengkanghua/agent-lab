"""部署形态的结构断言：栈编排文件与部署工作流里那些「删掉不会让任何东西变红」的声明。

这些断言只覆盖一类东西：**删掉它、或把它改个值，不会有任何别的测试变红，而后果只在真机上以
停机、周期任务重复投递或回滚失效的形式暴露出来**。它们都是一行声明，没有单元测试能覆盖它们的
语义，所以这里只做「声明在不在、值对不对」这一层，不起容器、不连数据库、不碰集群。

范围刻意收得很窄：不比对整段文本、不比对字段拼写、不追求把文件格式钉死。换一种等价写法
（例如把 `start-first` 写成别的等价字段组合、把 `180s` 写成 `3m`）时，这些断言仍然应当为真——
它们守的是承诺，不是写法。同理，这里不断言 `image`、`env_file` 之类「改错了会立刻报错」的
字段：那些有更早、更响的失败路径。

为什么值得单独一个文件：这些承诺分别在两个文件里（栈定义在 ``backend/docker-stack.yml``，
镜像清理在部署工作流里），而它们都是「部署方式」这件事的一部分。
"""

import re
from pathlib import Path
from typing import Any

import yaml

from agent_lab.api.readiness import READINESS_PROBE_TIMEOUT_SECONDS


BACKEND_DIR = Path(__file__).resolve().parents[1]
STACK_FILE = BACKEND_DIR / "docker-stack.yml"
WORKFLOW_FILE = BACKEND_DIR.parent / ".github" / "workflows" / "deploy.yml"

# 三个服务各自的停止宽限。**三个数都不许跟别人共用**：它们的活不同——API 要等一个 superstep
# 排空完，Beat 没有手上活，Worker 要让手里的文档批次做完。见 docs/adr/0039 与 0040。
STOP_GRACE_PERIOD_SECONDS = {
    "backend": 180,
    "task-beat": 30,
    "task-worker": 360,
}


def load_stack() -> dict[str, Any]:
    """读栈编排文件。

    用 YAML 解析而不是正则：文件里有 ``x-backend`` 锚点与 ``<<`` 合并键，正则读不出合并之后的
    取值，而断言要判的正是取值。
    """

    return yaml.safe_load(STACK_FILE.read_text(encoding="utf-8"))


def duration_seconds(value: Any) -> float:
    """把 ``180s`` / ``3m`` / ``1h`` 这样的时长声明换算成秒。

    刻意接受等价写法：把 ``180s`` 改成 ``3m`` 是同一个承诺，断言不该因为这种改动变红。
    """

    text = str(value).strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)?", text)
    assert match, f"不认识的时长写法：{text}"
    amount = float(match.group(1))
    return amount * {"ms": 0.001, "s": 1, "m": 60, "h": 3600, None: 1}[match.group(2)]


def test_stack_declares_the_shared_network_as_external() -> None:
    """三个服务挂在同一张声明为 external 的项目网络上，名字写死。

    栈服务挂不上 local 作用域的网络（Docker 的硬限制），所以这张网必须自己建、且在文件里
    external。名字写死是有意的：靠栈名前缀生成的话，每次敲命令都得先查它实际叫什么。
    """

    stack = load_stack()

    assert stack["networks"] == {"agent-lab-net": {"external": True}}
    for service in stack["services"].values():
        assert service["networks"] == ["agent-lab-net"]


def test_api_uses_start_first_so_a_deploy_does_not_stop_serving() -> None:
    """API 是先起后停——整条「零停机」承诺就压在这一行上。

    改成 ``stop-first`` 不会有任何测试变红，真机上的表现是：旧任务先被停掉，新任务还在装配，
    这段时间端口上没人服务、请求被拒。
    """

    update = load_stack()["services"]["backend"]["deploy"]["update_config"]

    assert update["order"] == "start-first"
    assert update["parallelism"] == 1
    assert update["failure_action"] == "rollback"
    # 监视窗口要盖过「健康检查判不健康」所需的时间：间隔 5 秒 × 连续 3 次。
    assert duration_seconds(update["monitor"]) >= 3 * 5


def test_beat_uses_stop_first_so_a_cron_tick_is_never_delivered_twice() -> None:
    """Beat 必须先停后起：两个调度器同时活着会让同一个周期任务被投两次。

    改成 ``start-first`` 没有任何测试会红，表现是周期任务重复受理——而那要等到业务上看到
    重复数据才发现。
    """

    assert load_stack()["services"]["task-beat"]["deploy"]["update_config"]["order"] == "stop-first"


def test_every_service_declares_its_own_stop_grace_period() -> None:
    """三个服务各自的停止宽限，值也各自钉住。

    这三个数不是调优参数，是三种不同收尾的产物（见上面的常量说明）。把 API 的 180 秒改成
    30 秒，表现是排空还没走完进程就被杀掉、在途运行变成未完成；把 Worker 的 360 秒压到两分钟，
    表现是文档批次半途中断、留下需要人工确认的待核实记录。
    """

    services = load_stack()["services"]

    assert sorted(services) == sorted(STOP_GRACE_PERIOD_SECONDS)
    for name, expected in STOP_GRACE_PERIOD_SECONDS.items():
        assert duration_seconds(services[name]["stop_grace_period"]) == expected, name


def test_api_healthcheck_points_at_the_readiness_endpoint() -> None:
    """API 的健康检查目标是只读的就绪端点，四个时间参数按初值声明。

    它是「坏版本不接班」唯一能被更新器看见的信号：目标一旦退回 ``/health``（只问进程与数据库），
    一个「检索能用、提问用不了」的新版本就会被判成启动成功，从而替掉正在服务的那一份。
    """

    healthcheck = load_stack()["services"]["backend"]["healthcheck"]

    assert "/ready" in " ".join(str(each) for each in healthcheck["test"])
    assert duration_seconds(healthcheck["interval"]) == 5
    assert duration_seconds(healthcheck["timeout"]) == 3
    assert healthcheck["retries"] == 3
    assert duration_seconds(healthcheck["start_period"]) >= 9.7  # 实测的启动耗时


def test_readiness_probe_budget_stays_inside_the_healthcheck_timeout() -> None:
    """就绪探针自己的上限必须小于健康检查的单次超时。

    两者一起改才安全：探针上限一旦超过外面那个，请求会在回答之前被掐断，应用日志里什么都不剩，
    排查时只看到「健康检查失败」。这条断言横跨 Python 常量与编排文件，所以只能在这里守。
    """

    healthcheck = load_stack()["services"]["backend"]["healthcheck"]

    assert READINESS_PROBE_TIMEOUT_SECONDS < duration_seconds(healthcheck["timeout"])


def test_beat_and_worker_declare_no_healthcheck() -> None:
    """只有 API 声明健康检查。

    在 Swarm 里健康检查不过等于 agent 杀掉容器重启（不是 compose 里那种「只显示 unhealthy」），
    而 Worker 手上的文档批次被杀会留下需要人工确认的待核实记录。给它们「顺手加一个健康检查」
    是个看起来很合理的改动，所以在这里挡住：那会把「温和退出、把手上批次做完」变成「到点被杀」。
    """

    services = load_stack()["services"]

    assert "healthcheck" in services["backend"]
    assert "healthcheck" not in services["task-beat"]
    assert "healthcheck" not in services["task-worker"]


def test_stack_avoids_the_shapes_the_stack_loader_rejects_or_ignores() -> None:
    """栈加载器不认或会静默忽略的写法一个都不留。

    这几条的共同点是**没有报错**：``container_name`` 被忽略（服务身份变成栈名_服务名，日志与
    排查命令里的名字全变），``restart:`` 被忽略（容器退出后不再被拉起，而重启策略要写在
    ``deploy.restart_policy``），相对路径的卷挂载在栈里按别的目录解析（挂上去的是空目录或
    干脆失败）。所以用断言把它们挡在文件外，而不是等到真机上发现「改了没生效」。
    """

    stack = load_stack()

    for name, service in stack["services"].items():
        assert "container_name" not in service, name
        assert "restart" not in service, name
        assert service["deploy"]["restart_policy"]["condition"] == "any", name
        assert service["deploy"]["replicas"] >= 1, name
        # 注入方式显式写出来，且不靠相对路径的卷挂载沿用原目录。
        assert service["env_file"] == [".env"], name
        assert "volumes" not in service, name


def test_api_publishes_the_same_port_through_the_routing_mesh() -> None:
    """对外端口不变，走路由网格（全网卡监听是它的已知行为）。

    端口号在外层（OpenResty 反代）与云安全组里各写了一遍，改这里就是改对外契约。
    """

    ports = load_stack()["services"]["backend"]["ports"]

    assert ports == [
        {
            "target": 8000,
            "published": "${BACKEND_PORT:-18000}",
            "protocol": "tcp",
            "mode": "ingress",
        }
    ]


def test_image_cleanup_never_removes_every_untagged_image() -> None:
    """部署工作流里的镜像清理必须带「只删多久以前的」，不能无条件清空。

    秒级回滚要回到的那一版镜像必须还在本地：``docker image prune -f`` 会把被顶替下来的上一版
    当无名镜像删掉，于是「一条命令切回上一版」变成一个报错的按钮。这条写在部署工作流里而不是
    编排文件里，但同属部署方式的承诺，所以一起守——只判「那一行有没有带时限」，不比对整段文本。
    """

    text = WORKFLOW_FILE.read_text(encoding="utf-8")
    prune_lines = [line for line in text.splitlines() if "docker image prune" in line]

    assert prune_lines, "部署工作流里没有镜像清理这一步？"
    for line in prune_lines:
        assert "until=" in line, f"这一行会无条件删掉上一版镜像：{line.strip()}"
