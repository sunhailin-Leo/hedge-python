# hedge-python

[English](README.md) | **简体中文** | [日本語](README.ja.md)

[![CI](https://github.com/sunhailin-Leo/hedge-python/actions/workflows/ci.yml/badge.svg)](https://github.com/sunhailin-Leo/hedge-python/actions)
[![Coverage](https://img.shields.io/badge/coverage-97%25-brightgreen.svg)](#测试)
[![Python](https://img.shields.io/badge/python-3.9%E2%80%933.14-blue.svg)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 📘 完整文档以 **[英文版](README.md)** 为主，本文为中文摘要，便于快速了解。

[bhope/hedge](https://github.com/bhope/hedge) 的 Python 移植版本 —— **面向尾延迟优化的自适应对冲请求库**。

`hedge-python` 使用 [DDSketch](https://arxiv.org/abs/2004.08604) 学习每个目标主机的延迟分布，当主请求超过估算的 p90 时立即发起备份请求，并通过令牌桶限制对冲速率，避免在故障期间放大流量。**零配置开箱即用**，原生支持 **httpx**、**aiohttp**、**niquests**、**tornado** 和 **gRPC**（unary + server-streaming）。同时支持通过 `http_client` 参数无缝集成 **OpenAI Python SDK**。

灵感来自 Dean & Barroso 的 [_The Tail at Scale_](https://research.google/pubs/the-tail-at-scale/)（CACM 2013）。

---

## 为什么需要对冲？

少量慢响应主导用户感知的延迟。对冲机制在主请求超过预期截止时间后发起一个重复请求 —— 谁先返回就用谁，另一个被取消。

**在 5% 慢请求（10× 延迟）的基准测试中：**

![多框架基准测试](eval_multi_framework.png)

| 框架      | 配置                |  p50  |  p90  |   p95  |   p99  |  p999   | 额外开销 |
|-----------|---------------------|-------|-------|--------|--------|---------|----------|
| httpx     | 不对冲              |  5.8  | 10.3  |  12.2  |  51.3  |   78.3  |   0.0%   |
| httpx     | **自适应对冲**      |  6.2  | 10.5  |  12.1  | **18.8** | **22.2** |   7.0%   |
| aiohttp   | 不对冲              |  6.3  | 10.7  |  13.0  |  52.4  |   79.0  |   0.0%   |
| aiohttp   | **自适应对冲**      |  6.5  | 11.3  |  13.8  | **20.5** | **25.1** |   4.6%   |
| grpc      | 不对冲              |  6.5  | 10.8  |  12.7  |  59.9  |   82.0  |   0.0%   |
| grpc      | **自适应对冲**      |  6.9  | 11.6  |  13.7  | **20.4** | **23.5** |   5.6%   |

三个框架的 p99 延迟均下降 **60–66%**，仅多消耗 5–7% 的后端流量。可通过 `make bench-multi && make bench-plot` 复现。

---

## 快速开始

```bash
# 按需安装框架支持
pip install hedge-python[httpx]
pip install hedge-python[aiohttp]
pip install hedge-python[niquests]
pip install hedge-python[tornado]
pip install hedge-python[grpc]
pip install hedge-python[all]   # 全部框架
```

### httpx

```python
import asyncio
import httpx
from hedge import HedgeConfig
from hedge.transport import HedgedHttpxTransport

async def main():
    transport = HedgedHttpxTransport(config=HedgeConfig())
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.get("https://api.example.com/data")
        print(resp.status_code)

asyncio.run(main())
```

### aiohttp

```python
import asyncio
from hedge import HedgeConfig
from hedge.transport import HedgedAiohttpSession

async def main():
    async with HedgedAiohttpSession(config=HedgeConfig()) as session:
        resp = await session.get("https://api.example.com/data")
        data = await resp.json()
        print(data)

asyncio.run(main())
```

### gRPC（Unary）

```python
import grpc.aio
from hedge import HedgeConfig
from hedge.interceptor import HedgedUnaryInterceptor

async def make_channel():
    return grpc.aio.insecure_channel(
        "localhost:50051",
        interceptors=[HedgedUnaryInterceptor(config=HedgeConfig(estimated_rps=500))],
    )
```

### gRPC（Server Streaming —— 适用于 LLM 推理、日志拉取等）

```python
import grpc.aio
from hedge import HedgeConfig
from hedge.interceptor import HedgedServerStreamInterceptor

async def make_channel():
    return grpc.aio.insecure_channel(
        "localhost:50051",
        interceptors=[HedgedServerStreamInterceptor(config=HedgeConfig())],
    )
```

### niquests

```python
import asyncio
from hedge import HedgeConfig
from hedge.transport import HedgedNiquestsSession

async def main():
    async with HedgedNiquestsSession(config=HedgeConfig()) as session:
        resp = await session.get("https://api.example.com/data")
        print(resp.status_code)

asyncio.run(main())
```

### tornado

```python
import asyncio
from hedge import HedgeConfig
from hedge.transport import HedgedTornadoClient

async def main():
    async with HedgedTornadoClient(config=HedgeConfig()) as client:
        resp = await client.fetch("https://api.example.com/data")
        print(resp.code)

asyncio.run(main())
```

### OpenAI SDK

OpenAI Python SDK 底层使用 httpx，可通过 `http_client` 参数直接注入 `HedgedHttpxTransport`：

```python
import httpx
from openai import AsyncOpenAI
from hedge import HedgeConfig
from hedge.transport import HedgedHttpxTransport

transport = HedgedHttpxTransport(config=HedgeConfig(percentile=0.95))
client = AsyncOpenAI(
    api_key="sk-...",
    http_client=httpx.AsyncClient(transport=transport),
)
```

> **注意**：OpenAI 核心 API（Chat Completions、Embeddings 等）使用 POST，默认**不会**被对冲——避免双倍计费。仅 GET 端点（如模型列表）会被对冲。完整示例见 [`examples/openai_hedged.py`](examples/openai_hedged.py)。

对于 server-streaming，对冲信号是 **首消息到达时间（TTFM）**：若主流在估算的 p90 内仍未返回首个 chunk，则发起备份流。先返回首 chunk 的流胜出并继续接管，败者在传输层被取消。

> 每个框架的可运行示例位于 [`examples/`](examples/) 目录 —— gRPC 示例**完全自包含**（启动本地 server + 注入 straggler，无需任何外部依赖即可看到对冲触发）。详见 [`examples/README.md`](examples/README.md)。

---

## 工作原理

### 1. DDSketch 分位数估计器

每个目标主机持有一个 `WindowedSketch` —— 由两个 DDSketch 组成、每 30 秒轮换。DDSketch 通过对数分桶映射提供 **相对误差保证**：任意分位数估计与真实值的偏差 ≤ ±1%，与底层分布无关。

### 2. 自适应触发

每次请求时，传输层向 sketch 查询配置的分位数（默认 p90）。如果主请求未在该截止时间内返回，则发起备份请求。先到的响应作为结果返回，败者被取消（gRPC 流会同时取消底层 `Call`）。

```
              ┌─ 主请求    ─────────── ✓（快） ──→ 返回
请求 ─────────┤
              └─ p90 后触发对冲 ─── ✗（被取消）
```

### 3. 令牌桶预算

对冲请求由令牌桶限速，速率为 `estimated_rps × budget_percent / 100` tokens/s。在真实故障期间，桶会被耗尽并自动停止对冲 —— 防止"对冲放大故障"的死亡螺旋。

### gRPC 实现细节

gRPC 的 `intercept_unary_unary` continuation 几乎立即返回 `Call` 对象，真正的 RTT 消耗在后续的 `await call` 上。我们将**两个步骤**合并到同一个 asyncio task 中，这样对冲计时器才能反映真实的端到端 RPC 延迟。取消败者时先调 `call.cancel()`（通知服务端）再调 `task.cancel()`（清理协程）。

---

## 配置项

所有参数都在 `HedgeConfig` 上：

| 参数 | 类型 | 默认值 | 说明 |
|-----|------|-------|-----|
| `percentile` | `float` | `0.90` | 用作对冲触发的 sketch 分位数 |
| `max_hedges` | `int` | `1` | 单次调用允许的最大对冲并发数 |
| `budget_percent` | `float` | `10.0` | 对冲请求占总流量的最大百分比 |
| `estimated_rps` | `float` | `100.0` | 预估 QPS，决定令牌桶容量 |
| `min_delay` | `float` | `0.001` | 对冲延迟下限（秒） |
| `warmup_requests` | `int` | `20` | 使用固定延迟的预热请求数 |
| `warmup_delay` | `float` | `0.01` | 预热阶段的固定对冲延迟（秒） |
| `window_duration` | `float` | `30.0` | sketch 窗口轮换周期（秒） |
| `stats` | `Stats \| None` | `None` | 注入自定义 `Stats` 用于可观测性 |

> **`estimated_rps` 调参建议**：选择接近真实 QPS 的值，令牌桶容量（`rps × budget_percent / 100`）才有意义。不确定时从默认 `100.0` 开始，观察 stats 快照的 `hedge_rate` / `budget_exhausted`。

---

## 可观测性

```python
from hedge import HedgeConfig, Stats
from hedge.transport import HedgedHttpxTransport

stats = Stats()
transport = HedgedHttpxTransport(config=HedgeConfig(stats=stats))

# ... 跑一段流量后 ...
snap = stats.snapshot()
print(f"total={snap.total_requests} hedged={snap.hedged_requests}")
print(f"hedge_wins={snap.hedge_wins} primary_wins={snap.primary_wins}")
print(f"budget_exhausted={snap.budget_exhausted}")
print(f"hedge_rate={stats.hedge_rate():.2%}")
```

`Stats` 是完全线程安全的，可在多个 transport / interceptor 间共享以聚合指标。

---

## 基准测试与图表

项目自带两套 benchmark：

| 命令                 | 内容                                                                | 输出                                  |
|---------------------|---------------------------------------------------------------------|---------------------------------------|
| `make bench-compare` | 仅 httpx：不对冲 vs 静态 10ms vs 静态 50ms vs 自适应                | `benchmark/results.csv`               |
| `make bench-multi`   | httpx vs aiohttp vs gRPC，不对冲 vs 自适应                          | `benchmark/results_multi.csv`         |
| `make bench-plot`    | 将两个 CSV 渲染成图表                                                | `eval.png`、`eval_multi_framework.png` |

每套 benchmark 跑 500 个请求，模拟 lognormal 延迟（`mean=5ms, stddev=2ms`），5% 概率出现 10× straggler。

---

## 开发

```bash
# 安装 uv（如未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh

make install            # 用 uv 安装全部 extras
make lint               # ruff 检查
make typecheck          # mypy
make test               # 全部测试
make test-unit          # 仅单元测试
make test-integration   # 集成测试（需 httpx / aiohttp / grpcio）
make coverage           # 覆盖率报告（当前 97%）
make bench-multi        # 多框架基准测试
make bench-plot         # 渲染图表
make ci                 # lint + typecheck + test + coverage
```

### 测试

* **单元测试** (`tests/unit/`)：DDSketch、令牌桶、调度器、stats、options、lazy import shim、gRPC 拦截器分支（含 fake continuation）。
* **集成测试** (`tests/integration/`)：真实 httpx transport、真实 aiohttp session、**真实本地 gRPC server**（含 `.proto` 与生成的 pb2）。
* **基准测试** (`tests/benchmark/`)：DDSketch 微基准、令牌桶微基准、四配置对比、三框架对比。

当前覆盖率：**97%**（150 个测试，约 7 秒）。

---

## 更新日志

发布历史见 [CHANGELOG.md](CHANGELOG.md)。

## 引用文献

- Jeffrey Dean and Luiz André Barroso. ["The Tail at Scale."](https://research.google/pubs/the-tail-at-scale/) *Communications of the ACM*, 56(2):74–80, February 2013.
- Charles Masson, Jee E. Rim, and Homin K. Lee. ["DDSketch: A Fast and Fully-Mergeable Quantile Sketch with Relative-Error Guarantees."](https://arxiv.org/abs/2004.08604) *Proceedings of the VLDB Endowment*, 12(12):2195–2205, 2019.

## 许可证

`hedge-python` 基于 [MIT License](LICENSE) 发布。
