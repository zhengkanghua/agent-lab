"""用量库配置的启动行为与环境变量模板的一致性。

工单 08 把这条边界划得很清：**配置缺失或不合法 → 启动失败**（改了配置就能好，静默降级只会让
「用量统计悄悄没在工作」难以发现）；**配置没问题但运行期连不上库 → 降级、只记日志**（那是
07 的行为，用量库的故障不升级成对话不可用）。本文件守前一半，并核对模板里有全部键。

不连 PostgreSQL：这里只读配置、只断言装配的时机。
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import PostgresDsn, ValidationError

from agent_lab.config.usage_database import UsageDatabaseSettings
from agent_lab.usage.assembly import UsageRuntime

from tests.app_helpers import FakeSearchRuntime, create_offline_app


def run(coroutine: Any) -> Any:
    """执行异步测试，不引入额外 pytest 异步插件。"""

    return asyncio.run(coroutine)


def test_the_missing_setting_is_named_in_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置缺失时报错要能看出缺的是哪一项。

    ``_env_file=None`` 让这个类不读仓库里那份本地 ``.env``——否则开发机上永远配好了这一项，
    这条用例在本地会绿、在新克隆上才是真红。
    """

    monkeypatch.delenv("LLMOPS_DATABASE_URL", raising=False)

    with pytest.raises(ValidationError) as info:
        UsageDatabaseSettings(_env_file=None)

    assert [item["loc"] for item in info.value.errors()] == [("database_url",)]


def test_a_missing_usage_database_url_stops_the_process_from_starting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置缺失时进程起不来，而不是静默变成「不记账」。

    读取点在 lifespan 的**外层** try 里（Agent 装配之外），所以这个异常不会被那个
    「只让 /agent/* 返 503」的 except 咽掉——咽掉的话，配错的表现就是用量统计悄悄停止工作。
    """

    monkeypatch.delenv("LLMOPS_DATABASE_URL", raising=False)

    def usage_factory() -> UsageRuntime:
        return UsageRuntime.build(UsageDatabaseSettings(_env_file=None))

    app = create_offline_app(
        runtime_factory=FakeSearchRuntime,
        usage_runtime_factory=usage_factory,
    )

    async def verify() -> None:
        with pytest.raises(ValidationError):
            async with app.router.lifespan_context(app):
                pass

    run(verify())


def test_an_unreachable_usage_database_does_not_stop_the_process() -> None:
    """配置正确但用量库暂时不可达时进程能起来。

    装配只建 Engine、不建连，所以「库连不上」不会变成「进程起不来」——那正是「一个次要数据库
    的故障不升级成主要功能不可用」。第一条 SQL 才建连，成败由刷写时那条日志记录。
    """

    async def scenario() -> bool:
        settings = UsageDatabaseSettings.model_construct(
            database_url=PostgresDsn("postgresql+psycopg://usage:usage@127.0.0.1:1/llmops"),
        )
        usage_runtime = UsageRuntime.build(settings)
        app = create_offline_app(
            runtime_factory=FakeSearchRuntime,
            usage_runtime_factory=lambda: usage_runtime,
        )
        async with app.router.lifespan_context(app):
            return app.state.usage_runtime is usage_runtime
        return False

    assert run(scenario()) is True


def test_the_env_template_lists_every_usage_database_setting() -> None:
    """环境变量模板里有用量库的每一项，新克隆的仓库照它就能配起来。

    字段名与键名的对应是 ``LLMOPS_`` + 字段名大写；漏了一项的表现是「新克隆按模板配完仍然
    起不来」，而那条报错只会说某个字段缺失，不会说模板漏了它。
    """

    template = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    keys = {line.split("=", 1)[0].strip() for line in template.splitlines() if "=" in line and not line.startswith("#")}

    expected = {f"LLMOPS_{name.upper()}" for name in UsageDatabaseSettings.model_fields}

    assert expected <= keys, f"模板缺少用量库配置项：{sorted(expected - keys)}"
