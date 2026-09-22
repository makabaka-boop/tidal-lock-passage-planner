# 潮闸航程调度 API

FastAPI + PostgreSQL 实现的潮闸检修后航程规划服务。系统保存不可变的开放日历和已采纳方案，根据左闭右开的整数时间窗口，返回所有可行出发时刻的合并区间。

## 关键语义

- 闸门 ID：非空、互不重复的 ASCII 字符串。
- 所有日历时刻、时长：`0..10^12` 的整数秒。
- 窗口与搜索区间均为 `[start, end)`；`start < end`，恰在 `end` 到达表示关闭。
- 同一闸门的窗口按 `start` 升序、互不重叠，可以首尾相接。
- 航程包含 `1..200` 个互异闸门。
- `travel_times[i]` 是第 `i` 闸到第 `i+1` 闸的航行时间。
- `max_wait_times[i]` 是在第 `i` 闸最多可等待的秒数；允许在窗口开始前等待，等待截止时刻等于到达时刻加最大等待。
- 第一闸的到达时刻即出发时刻；之后的到达时刻等于上一闸入闸时刻加航行时间。
- 见证按“最早可入”原则生成。
- 新日历重放不会修改原方案；有效返回 `STILL_VALID`，无效返回首个失效闸门、到达时刻和等待截止时刻。

## 启动

要求 Docker Compose v2。宿主 API 端口由 `API_PORT` 控制，默认 `8000`。可以直接在命令行传入，或复制 `.env.example` 为 `.env` 后调整：

```bash
API_PORT=8000 docker compose up --build
```

健康检查：

```bash
curl http://localhost:8000/health
```

交互式文档：

- Swagger UI: <http://localhost:8000/docs>
- OpenAPI: <http://localhost:8000/openapi.json>

## 请求示例

### 1. 发布日历

```bash
curl -X POST http://localhost:8000/calendars \
  -H 'Content-Type: application/json' \
  -d '{
    "id": "cal-2026-09-22",
    "gates": [
      {
        "gate_id": "A",
        "windows": [
          {"start": 10, "end": 20},
          {"start": 20, "end": 30}
        ]
      },
      {
        "gate_id": "B",
        "windows": [
          {"start": 25, "end": 40}
        ]
      }
    ]
  }'
```

`id` 可省略，服务会生成 UUID。非法整版请求返回 400，不写库；重复 ID 返回 409。

### 2. 查询全部可行出发区间

```bash
curl -X POST http://localhost:8000/calendars/cal-2026-09-22/voyages \
  -H 'Content-Type: application/json' \
  -d '{
    "gate_ids": ["A", "B"],
    "travel_times": [5],
    "max_wait_times": [5, 0],
    "search_start": 0,
    "search_end": 30
  }'
```

响应示例：

```json
{
  "intervals": [
    {"start": 20, "end": 30}
  ]
}
```

无可行解时返回 `{"intervals": []}`。

### 3. 选择出发时刻并采纳见证

```bash
curl -X POST http://localhost:8000/calendars/cal-2026-09-22/voyages/adopt \
  -H 'Content-Type: application/json' \
  -d '{
    "voyage": {
      "gate_ids": ["A", "B"],
      "travel_times": [5],
      "max_wait_times": [5, 0],
      "search_start": 0,
      "search_end": 30
    },
    "departure": 20
  }'
```

响应包含方案 ID 与每个闸门的 `arrival`、`entry`、`wait`。选择不可行时刻返回 400，不产生方案。

### 4. 在新日历上重放方案

```bash
curl -X POST \
  http://localhost:8000/calendars/new-calendar-id/plans/<plan-id>/replay
```

有效响应：

```json
{
  "status": "STILL_VALID",
  "witnesses": [ ... ],
  "gate_id": null,
  "arrival": null,
  "wait_deadline": null
}
```

失效响应：

```json
{
  "status": "FAILING_GATE",
  "witnesses": null,
  "gate_id": "B",
  "arrival": 25,
  "wait_deadline": 25
}
```

## 算法

核心代码在 `app/domain.py`。算法不逐秒枚举，而是从最后一闸开始做区间逆映射：

1. 最后一闸的可行入闸集合是其全部开放窗口的合并区间。
2. 对每个闸门，把“可行入闸时刻”映回“可行到达时刻”：
   - 到达落在窗口内：入闸时刻等于到达时刻；
   - 到达早于窗口开始：当且仅当窗口开始时刻本身可入，且等待不超过最大等待时，整个等待前缀可行；
   - 已经位于前一窗口内的到达会在前一窗口即时入闸，因此等待前缀从上个窗口结束处开始。
3. 用延迟的统一偏移量表示航段时间，避免每层重复复制全部区间。
4. 与搜索区间求交并合并首尾相接区间。

随机小规模用例在 `tests/test_bruteforce.py` 中与逐秒穷举对照；`tests/test_performance.py` 构造总计 20 万个窗口，要求三秒内完成。

## 测试

```bash
./verify
```

或：

```bash
pytest -q
```

本地无 Docker 时，API 测试使用 FastAPI `TestClient` 和内存替身，不要求运行 PostgreSQL。Docker Compose 启动的容器会在 API 启动时自动创建数据库表。
