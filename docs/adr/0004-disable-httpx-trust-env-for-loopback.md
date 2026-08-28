# 本机联调脚本必须给 httpx 关掉 trust_env

写打本机回环（localhost / 127.0.0.1）的联调或冒烟脚本时，httpx 必须显式传
`trust_env=False`。

这台 Windows 开发机配了系统级 HTTP 代理，配在注册表里而不是环境变量。httpx 默认
`trust_env=True` 会去读它，导致所有打回环的请求被代理接走，一律返回 `502` 且 body 为空。
curl 不读注册表代理，所以同一个地址 curl 正常、httpx 502，很容易误判成应用或 Vite 代理
本身的故障。

## Consequences

`env | grep -i proxy` 什么都看不到，因为它不在环境变量里。别用这个来排除代理嫌疑。

判定方法：拿一个确定没人监听的端口（例如 5999）试一次。`trust_env=True` 时连空端口也返回
502；`trust_env=False` 时才会正确报 `ConnectError [WinError 10061]`。另一个信号是收到 502
但 `response.headers['server']` 不是 `uvicorn`。

这是本机环境约束，不是仓库代码里的配置——仓库源码内目前没有需要改的 `trust_env` 调用点，
约束作用于以后新写的脚本。
