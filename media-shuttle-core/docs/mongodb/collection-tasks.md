# Core MongoDB 集合文档：`tasks`

## 1. 集合信息
- 默认数据库：`media_shuttle`（可由 `MEDIA_SHUTTLE_MONGO_DB` 覆盖）
- 默认集合名：`tasks`（可由 `MEDIA_SHUTTLE_MONGO_TASK_COLLECTION` 覆盖）
- 主要代码入口：`core/storage/repository.py`（`MongoTaskRepository`）
- 业务用途：存储任务主记录（任务输入、状态流转、解析结果、上传产物、错误信息）

## 2. 字段说明
| 字段 | 类型 | 必填 | 作用 |
|---|---|---|---|
| `_id` | `string` | 是 | Mongo 主键，值等于 `task_id`，用于按任务 ID 快速查询/覆盖写。 |
| `task_id` | `string` | 是 | 任务唯一 ID（与 `_id` 冗余保存，便于上层逻辑直接读取）。 |
| `idempotency_key` | `string` | 是 | 幂等键，用于避免重复创建同一任务。 |
| `status` | `string` | 是 | 任务状态：`QUEUED` / `PARSING` / `DOWNLOADING` / `UPLOADING` / `SUCCEEDED` / `FAILED`。 |
| `message` | `string` | 是 | 状态描述；成功时通常为上传结果地址，失败时可为错误摘要。 |
| `sources` | `array<object>` | 是 | 解析阶段得到的源文件快照列表。 |
| `artifacts` | `array<object>` | 是 | 下载+上传阶段的结果列表（成功/失败、最终位置等）。 |
| `last_error` | `string` | 是 | 最近一次错误信息；成功结束时通常置空字符串。 |
| `requester_id` | `string` | 是 | 任务请求方标识（谁发起任务）。 |
| `url` | `string` | 是 | 待解析的原始链接。 |
| `target` | `string` | 是 | 上传目标类型（例如 `RCLONE`、`TELEGRAM`）。 |
| `destination` | `string` | 是 | 上传目标的目的位置（路径/频道/目录等）。 |
| `created_at` | `string` (ISO8601 UTC) | 是 | 任务创建时间（示例：`2026-03-04T01:23:45Z`）。 |
| `updated_at` | `string` (ISO8601 UTC) | 是 | 最近更新时间；状态或运行时字段变更时刷新。 |

## 3. 嵌套字段说明

### 3.1 `sources[]` 对象
| 字段 | 类型 | 必填 | 作用 |
|---|---|---|---|
| `site` | `string` | 是 | 来源站点标识（如 `GOFILE`、`BUNKR`）。 |
| `page_url` | `string` | 是 | 解析来源页面 URL。 |
| `download_url` | `string` | 是 | 解析得到的声明下载 URL。 |
| `file_name` | `string` | 是 | 文件名。 |
| `remote_folder` | `string \| null` | 否 | 远端目录信息（站点有目录语义时使用）。 |
| `metadata` | `object` | 是 | 站点特定扩展元数据（透传字段）。 |

### 3.2 `artifacts[]` 对象
| 字段 | 类型 | 必填 | 作用 |
|---|---|---|---|
| `ok` | `boolean` | 是 | 该条产物处理是否成功。 |
| `reason` | `string` | 是 | 失败原因；成功时一般为空字符串。 |
| `site` | `string` | 是 | 源站点。 |
| `page_url` | `string` | 是 | 源页面 URL。 |
| `declared_download_url` | `string` | 是 | 解析阶段给出的下载地址。 |
| `actual_download_url` | `string` | 是 | 实际下载时使用的地址（可能被重定向/改写）。 |
| `file_name` | `string` | 是 | 文件名。 |
| `remote_folder` | `string \| null` | 否 | 远端目录信息。 |
| `location` | `string` | 是 | 上传成功后的目标位置（如 `rclone://...`）。 |
| `download` | `object` | 否 | 仅在直跑 pipeline 路径下可能出现的下载快照（兼容字段）。 |

## 4. 示例文档
```json
{
  "_id": "task-001",
  "task_id": "task-001",
  "idempotency_key": "u1:https://example.com/a",
  "status": "SUCCEEDED",
  "message": "rclone://remote/path/file.zip",
  "sources": [
    {
      "site": "GOFILE",
      "page_url": "https://gofile.io/d/abc",
      "download_url": "https://store1.gofile.io/download/...",
      "file_name": "file.zip",
      "remote_folder": "album-a",
      "metadata": {
        "content_id": "abc"
      }
    }
  ],
  "artifacts": [
    {
      "ok": true,
      "reason": "",
      "site": "GOFILE",
      "page_url": "https://gofile.io/d/abc",
      "declared_download_url": "https://store1.gofile.io/download/...",
      "actual_download_url": "https://cdn.example.com/file.zip",
      "file_name": "file.zip",
      "remote_folder": "album-a",
      "location": "rclone://remote/path/file.zip"
    }
  ],
  "last_error": "",
  "requester_id": "u1",
  "url": "https://gofile.io/d/abc",
  "target": "RCLONE",
  "destination": "remote:path",
  "created_at": "2026-03-04T01:23:45Z",
  "updated_at": "2026-03-04T01:24:12Z"
}
```

## 5. 使用注意
- `list(status=..., limit=...)` 会按 `created_at` 倒序读取。
- `queue_stats()` 仅按 `status` 统计 `PARSING` / `DOWNLOADING` / `UPLOADING`。
- `sources`、`artifacts`、`last_error` 属于运行时字段，会在任务执行过程中多次更新。
