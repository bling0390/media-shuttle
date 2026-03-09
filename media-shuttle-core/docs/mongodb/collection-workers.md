# Core MongoDB 集合文档：`workers`

## 1. 集合信息
- 默认数据库：`media_shuttle`（可由 `MEDIA_SHUTTLE_MONGO_DB` 覆盖）
- 默认集合名：`workers`（可由 `MEDIA_SHUTTLE_MONGO_WORKER_COLLECTION` 覆盖）
- 主要代码入口：`core/storage/worker_registry.py`（`MongoWorkerRegistry`）
- 业务用途：记录 core worker 生命周期、心跳和运行参数，支撑 worker 观测与调度控制

## 2. 字段说明
| 字段 | 类型 | 必填 | 作用 |
|---|---|---|---|
| `_id` | `string` | 是 | Mongo 主键，值等于 `hostname`。 |
| `hostname` | `string` | 是 | worker 主机名（例如 `core-worker-parse@media-shuttle-core`）。 |
| `role` | `string` | 是 | worker 角色：`parse` / `download` / `upload` / `control` 等。 |
| `queues` | `array<string>` | 是 | 当前 worker 订阅的队列列表。 |
| `queue` | `string` | 是 | `queues` 的逗号拼接字符串，兼容旧调用方展示逻辑。 |
| `status` | `string` | 是 | 生命周期状态：常见值 `STARTING` / `READY` / `SHUTDOWN` / `CRASHED`。 |
| `concurrency` | `number` | 是 | 当前生效并发度（最小值 1）。 |
| `desired_concurrency` | `number` | 是 | 期望并发度（保留字段；更新时会尽量沿用旧值）。 |
| `node_id` | `string` | 否 | 所属节点 ID（由 `MEDIA_SHUTTLE_NODE_ID` 或主机名归一化得到）。 |
| `pid` | `number \| null` | 否 | worker 进程 PID。 |
| `exit_code` | `number \| null` | 否 | 进程退出码；崩溃/退出后可用于定位原因。 |
| `last_error` | `string` | 否 | 最近一次错误/退出原因（如 `exit_code=1`、`signal=SIGTERM`）。 |
| `started_at` | `string` (ISO8601 UTC) | 是 | 本 worker 首次启动时间（同一 `_id` 记录会尽量保留）。 |
| `last_heartbeat_at` | `string` (ISO8601 UTC) | 是 | 最近心跳时间（`heartbeat()` 或周期 upsert 会刷新）。 |
| `updated_at` | `string` (ISO8601 UTC) | 是 | 最近一次文档更新时间。 |

## 3. 示例文档
```json
{
  "_id": "core-worker-download@media-shuttle-core",
  "hostname": "core-worker-download@media-shuttle-core",
  "role": "download",
  "queues": [
    "media_shuttle:task_download@GOFILE",
    "media_shuttle:task_download@BUNKR"
  ],
  "queue": "media_shuttle:task_download@GOFILE,media_shuttle:task_download@BUNKR",
  "status": "READY",
  "concurrency": 2,
  "desired_concurrency": 2,
  "node_id": "NODE_A",
  "pid": 21877,
  "exit_code": null,
  "last_error": "",
  "started_at": "2026-03-04T01:20:01Z",
  "last_heartbeat_at": "2026-03-04T01:25:01Z",
  "updated_at": "2026-03-04T01:25:01Z"
}
```

## 4. 使用注意
- `upsert_worker()` 每次会覆盖整条 worker 文档，`started_at` 会从旧记录继承。
- 运行中可通过周期性 upsert 或 `heartbeat()` 刷新 `last_heartbeat_at`。
- `desired_concurrency` 在当前 core 代码里主要用于保留状态兼容，不直接触发进程伸缩。
