# T-21 NapCat Token 安全边界验收

- 日期：2026-09-15
- NapCat：`NapCat.Onebot 4.18.19`
- OneBot 协议：`v11`
- 结论：`loopback_only`

## 实测结果

监听地址：

```text
TCP 127.0.0.1:3000 LISTENING
TCP 127.0.0.1:3001 LISTENING
```

错误 Token 测试使用固定测试值 `Bearer invalid-token`：

| 通道 | 结果 |
| --- | --- |
| HTTP action | 403 Forbidden，错误 Token 被拒绝 |
| 正向 WebSocket | 握手成功，错误 Token 仍被接受 |

NapCat 版本通过正确的现有配置查询，仅记录版本信息，不记录 Token。

## 判定

- NapCat 的 WS Token 校验仍存在缺陷。
- 当前 HTTP 与 WS 都只监听 `127.0.0.1`，风险受本机边界限制。
- 因此结论为 `loopback_only`，不是 `fixed`，也不是 `unsafe_remote`。

## QQF 缓解

- `qq.wsUrl` 和 `qq.httpUrl` 默认只允许 `127.0.0.1`、`localhost`、`::1`。
- 非回环地址默认拒绝配置。
- 只有显式设置 `qq.allowRemote=true` 才允许远端连接，并记录 WARN。
- 该开关不表示 NapCat 鉴权已验证；在 NapCat 修复 WS Token 校验前仍推荐回环部署。

## 后续

升级 NapCat 后应重跑错误 Token 测试。如果 WS 错误 Token 变为拒绝，可将结论更新为 `fixed`；
如果监听地址变为非回环且错误 Token 仍可连接，必须判定为 `unsafe_remote` 并立即整改。
