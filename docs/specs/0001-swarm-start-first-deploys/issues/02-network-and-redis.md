# 02: 建网并接通 Redis（一次性准备）

**要交付什么：** 一次性的人工准备：建出本项目自己的 swarm 网络，并把已有 Redis 容器接到这张网上（或按 01 的结论把连接地址改成容器可达的宿主地址）。做完之后，挂在项目网络上的任何容器都能像服务一样解析并连上 Redis；这一步只做一次，之后的运维动作（面板重建 Redis 后重新接入）照留下的命令做。

**被谁阻塞：** 01: 开工前把四个事实量完并落结论

**状态：** 已完成

**范围修正（工单 01 实测）：** 容器内依赖是**三个**不是规格写的「只有 Redis」：`redis`（6379）、`postgresql`（业务库 + 用量库同一个容器，5432）、`minio`（S3_ENDPOINT，9000）。三个都按规格给出的口径「有就按 Redis 的同一种接法处理」接到 `agent-lab-net` 上，`.env` 里的 `REDIS_URL`/`DATABASE_URL`/`LLMOPS_DATABASE_URL`/`S3_ENDPOINT` 都按容器名解析，不用改。

**要写进工单 10 手册的连带后果：** 接上的不只 Redis，所以「线丢了」的症状不再只是「队列与缓存报错而页面照常」——面板重建 `postgresql` 或 `minio` 容器会分别表现成「业务库连不上（进程起不来 / 接口大量 500）」与「原件上传与读取失败」，恢复命令与验证命令要覆盖三个。

实测命令见 `output/swarm-02-network.sh`（幂等；会把可复跑的验证件落到 `/root/agent-lab-net-verify.py` 与 `/root/agent-lab-net-verify.sh`）。

- [x] 项目网络存在，作用域是 swarm、允许普通容器接入，名字与编排文件里声明的一致
- [x] 从任意挂在该网络上的临时容器能解析到 Redis 并 ping 通（改地址路线下：进程使用的那个地址在容器内可达且 ping 通）
- [x] Redis 原来所在网络照旧、面板上其他应用访问 Redis 不受影响（它同时挂在两张网上）
- [x] 留下一条可复跑的验证命令，后续接线复核与「线丢了」的排查都用它

### 验收记录（2026-09-30 实测，服务器 `instance-20260723-1451`）

建网结果：`driver=overlay scope=swarm attachable=true subnet=10.88.1.0/24`（网段来自 `docker swarm init` 时指定的 `--default-addr-pool 10.88.0.0/16`，与 VCN 的 10.0.0.0/24、各 bridge 的 172.x 都不冲突）。

接线结果（三个容器都变成挂在两张网上，没有重启、原网 IP 未变）：

```text
redis       现在挂在：1panel-network(172.18.0.14) agent-lab-net
postgresql  现在挂在：1panel-network(172.18.0.12) agent-lab-net
minio       现在挂在：1panel-network(172.18.0.20) agent-lab-net
```

在 `agent-lab-net` 上的临时容器里跑真检查（地址全部从 `.env` 现读）：Redis PING、`DATABASE_URL` 与 `LLMOPS_DATABASE_URL` 各一次 `SELECT 1`、`S3_ENDPOINT` 解析 + TCP → **全部通过**。同一份检查在 `1panel-network` 上再跑一次 → 也全部通过（老的路子照旧能用）。三个依赖容器状态：`minio Up 3 weeks`、`redis Up 2 weeks`、`postgresql Up 7 weeks (healthy)`，与接线前一致。

留下的可复跑命令：`bash /root/agent-lab-net-verify.sh`（默认查 `agent-lab-net`）与 `bash /root/agent-lab-net-verify.sh 1panel-network`（查面板那张网）；断开单根线回退用 `docker network disconnect agent-lab-net <容器名>`。这三条要原样进工单 10 的手册。
