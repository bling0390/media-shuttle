# Issue #0001: Bunkr parser/downloader 失效 —— 把 HTML 包装页当视频上传 115

**Status**: Open / Confirmed
**Discovered**: 2026-06-09
**Severity**: **High** (silent corruption: 上传 115 的是错误页 HTML)
**Owner**: TBD

## 摘要

Media-shuttle 的 bunkr pipeline **能跑通队列、能用 rclone 上传到 115**，但下载到的不是真视频，而是 bunkr 的 HTML 包装页（3.2 KB 的 `<html>` 包含一段 `<script>` 指向真正 cdn URL）。结果：115 上传了一个伪装成 `.mp4` 名的 HTML 文件，task 标 SUCCEEDED，**没有内容校验**。

## 真实 e2e 复现

```bash
TASK_RESP=$(curl -sS -X POST http://localhost:8000/v1/tasks/parse \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://bunkr.cr/f/xkdwMPFHh372y",
    "requester_id": "jason_e2e_real",
    "target": "RCLONE",
    "destination": "115:/media-shuttle-test"
  }')
# task_id=25bbce9e-c917-4ee4-ab7a-b1e774f584d1
```

## 链路日志

```
06:25:05.696  GET bunkr.cr/f/xkdwMPFHh372y          → 200  (page HTML)
06:25:05.944  POST bunkr.cr/api/vs                  → 404  (parser 试 API 失败)
06:25:06.235  parse task queued  source_count=1
06:25:06.654  GET dl.bunkr.cr/file/57930011         → 200  (12.5s later, 这是 HTML wrapper 不是真视频)
06:25:06.656  download finished  size_bytes=3197
06:25:19.181  upload finished  location=rclone://115:/...
06:25:19.234  task SUCCEEDED
```

**整条链路 SUCCEEDED，但 `size_bytes=3197` —— 一个 3.2 KB 的 mp4 显然不是真视频。**

## 根因

`dl.bunkr.cr/file/57930011` 实际返回的是 HTML wrapper：

```html
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <script defer data-domain="get.bunkrr.su" data-t="v"
            data-v="6b8fa397-9f97-4407-9a6f-83b5fde4eb13.mp4"
            src="https://bunkr.ph/js/lv.js"></script>
    <title>Download How to Lick Pussy Right Let Lana &amp; Luna Teach You.mp4</title>
    <script>var ogname = "How to Lick Pussy Right Let Lana \u0026 Luna Teach You.mp4";</script>
</head>
```

Bunkr 现在用了 "lv.js" loader，浏览器访问时会跳到 `get.bunkrr.su` 拿真 cdn URL。Media-shuttle 的 downloader 把它当 direct file 抓了，没穿透。

具体代码问题（待确认）：

1. `core/providers/parsers_sites/bunkr.py` 用正则抓 `<a href="dl.bunkr...">` 抓到了**这个 HTML wrapper URL** 而非真 CDN URL
2. `core/providers/downloaders_sites/bunkr.py` 拿到 URL 就用 httpx GET，没 follow redirect 到 cdn 也没解析 wrapper 里的 cdn URL
3. 整个 pipeline 没有任何 content-type 校验（`text/html` 不应当被当 video 上传）

## 旁系 Issue

- **#0000** 用户提到的"前面提供的 bunkr 链接"，但**之前没真给过** —— 现在这条 issue 复现的是同一个 parser bug，但用了**真实可访问的 URL**，所以**确认了 bug 存在**

## 关联观察

- 115 backend list 抛 `Status 405 "405 Not Allowed"` —— 验证上传后用 `rclone ls 115:/` 列不动，无法直接确认 115 上文件状态。需要换种验证方式（web 端手动看，或者用 rclone cat 逐个文件读）
- 任务在 mongo 里状态正常 SUCCEEDED，artifacts 里写的 `download_url: https://dl.bunkr.cr/file/57930011` 看似合理但**指向 HTML wrapper**

## 修复方向

### 短期（不要让 115 被污染）

- 在 `media-shuttle-core/core/providers/downloaders_builtin.py` 的通用下载器里加：
  - content-type 白名单：`video/*`, `application/octet-stream`, `image/*` 才接受
  - `text/html` 触发 fail + 重试
  - size < 1KB 触发 fail（防极小错误页）
- 单独给 bunkr 加一个 `BUNKR_HTML_WRAPPER` 错误类型

### 中期（修 bunkr parser）

- 抓 `bunkr.cr/f/<slug>` 时，正则提取：
  - `<script data-v="...">` 里的 UUID（拼接成 `https://get.bunkrr.su/v/<uuid>.mp4`）
  - 或者用浏览器自动化（playwright）实际渲染拿 cdn URL
- 多个 bunkr mirror 都要支持（bunkr.cr / bunkr.si / bunkr.la / bunkr.ph / bunkr.ps 等）

### 长期

- 给所有 site parser 加**单元测试**，每个 mirror 至少 1 个 fixture
- 整套流水线加 content-type/content-length 校验

## 验证失败的副产物

115 端有两个污染文件要清理：
1. `115:/media-shuttle-test/bunkr.si/example-album-abc123` (57B HTML, 来自 Issue #0001 之前测试)
2. `115:/media-shuttle-test/How to Lick Pussy Right Let Lana & Luna Teach You.mp4/How_to_Lick_Pussy_Right_Let_Lana_amp_Luna_Teach_You.mp4` (3197B HTML wrapper, 来自这次)

清不掉是因为 115 backend 端 list 操作抛 `Status 405`。建议：手动去 115 web 端删，或者用一个**只删不列**的 rclone 路径。
