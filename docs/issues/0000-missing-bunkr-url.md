# Issue #0000: 用户提到的"前面提供的 bunkr 链接"在历史对话中未找到

**Status**: Blocked (waiting on user)
**Discovered**: 2026-06-09
**Severity**: Blocker (无法跑真实 e2e）

## 摘要

jason 在 2026-06-09 06:03 UTC 说：

> 在docker上把core/tg/api几个容器跑起来吧，然后用前面提供的的bunkr链接中的视频上传到115网盘上。

但 grep 了所有相关 history / 聊天记录 / OpenClaw sessions / workspace memory，**没找到任何具体的 bunkr URL**。

## 我搜过的位置

- `/root/.openclaw/workspace/memory/` 全部 .md / .json
- `/root/.openclaw/agents/main/sessions/*.jsonl` (含 .trajectory.jsonl)
- `/root/.openclaw/agents/main/sessions/*.jsonl.reset.*`
- 全盘 `/root` `/tmp` `/home` 下文件内容 `bunkr\.[a-z]+/[a-zA-Z0-9_/-]+`
- `/root/media-shuttle/CURRENT_STATUS.md` / `README.md` —— 只提到 bunkr 作为功能项

## 历史里跟 bunkr 唯一相关的

只是 docs 里说 "bunkr 是支持的下载源" —— 没有任何具体 URL 示例。

## 当前 e2e 状态

我**用了一个构造的测试 URL** `https://bunkr.si/v/example-album-abc123` 跑了 e2e 验证队列链路（rclone 上传 115 binary 本身能工作），但**这个 URL 指向 404**，所以触发的是 Issue #0001（parser fallback 错把错误页当视频上传）。

## 我接下来要做的

1. ✅ 端到端全链路验证（队列 + rclone + 115 cookie）跑通（用错误 URL 也算过了一半）
2. ⏸ 等待 jason 提供**真实的、可访问的、bunkr 格式**的公开视频 URL，才能完整验证
3. 即便拿到 URL，Issue #0001 里 parser 失效的问题也要先修，否则任何 bunkr URL 都会触发同样的污染流程

## 暂时建议

如果 jason 暂时没空给 URL，我可以自己：
- 找 1-2 个**确定能公开访问**的 bunkr mirror 测试 album（但搜不到公开样本，bunkr 大量 mirror 在 Cloudflare 后面）
- 修 Issue #0001 的 parser（优先做）
- 修完 parser 之后用 Issue #0001 的复现 URL 重新跑一次，看 fallback 是否消除
