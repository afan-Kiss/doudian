# 抖店飞鸽 CDP 监控与 WebSocket 发消息

基于 Playwright + Chrome DevTools Protocol（CDP）的抖店飞鸽（卖家客服）自动化工具：

- **CDP 抓包**：监听飞鸽 WebSocket / HTTP 流量，实时日志 + JSON 落盘
- **WebSocket 发消息**：通过页面内 Mona SDK `im.sendText()` 发送，买家端可收到
- **实时入站监听**：解析 Frontier WS 二进制帧，打印买家/卖家聊天消息

> 仓库：<https://github.com/afan-Kiss/doudian.git>

## 环境要求

- Python 3.10+
- Google Chrome（`config.yaml` 中 `browser.channel: chrome`）
- Windows / macOS / Linux

## 安装

```bash
git clone https://github.com/afan-Kiss/doudian.git
cd doudian
pip install -r requirements.txt
playwright install chromium
```

首次运行会自动创建独立浏览器 Profile（`profiles/debug`），需在打开的飞鸽页面中**手动登录**卖家账号。

## 项目结构

```
抖店/
├── config.yaml              # 浏览器、URL、抓包、入站监听配置
├── requirements.txt
├── README.md
├── scripts/
│   ├── debug_mode.py        # 调试模式：CDP 抓包 + 入站监听
│   ├── listen_inbound.py    # 实时监听买家/卖家消息
│   ├── send_ws_to_contact.py # 打开会话并发送消息（含 ACK 校验）
│   ├── test_inbound_parser.py # 离线回放抓包测试解析器
│   ├── probe_sdk.py
│   ├── inspect_pigeon_globals.py
│   ├── analyze_har.py
│   └── ...
├── src/
│   ├── browser/launcher.py  # 隔离 Profile 启动 Chrome
│   ├── monitor/             # CDP 监控、WS 帧解析、入站监听
│   ├── sender/              # SDK 发消息、DOM 兜底、导航
│   ├── analyzer/            # 抓包分类、Schema 导出
│   └── cli.py               # 统一命令行入口
└── captures/                # 抓包输出（gitignore，本地生成）
```

## 配置说明

`config.yaml` 主要字段：

| 字段 | 说明 |
|------|------|
| `browser.user_data_dir` | Chrome Profile 目录（默认 `profiles/debug`） |
| `browser.debug_port` | CDP 调试端口（默认 `9222`） |
| `urls.feige` | 飞鸽工作台地址 |
| `monitor.filter_hosts` | 只抓这些域名的 WS/HTTP |
| `inbound.roles` | 默认监听角色：`buyer` / `seller` / `system` |
| `inbound.dedupe_window_ms` | 消息去重时间窗（毫秒） |

## 常用命令

### 实时监听买家消息

```bash
python scripts/listen_inbound.py
# 或
python -m src.cli listen
python -m src.cli listen --roles buyer,seller,system --ws-log
```

启动后保持飞鸽登录，买家发消息会实时打印，例如：

```
[01:02:40] [买家] 一只小青蛙 | 会话=nAQCnSRsg6VjCVV6... | 嗯嗯
```

### 发送消息给指定买家

```bash
python scripts/send_ws_to_contact.py --contact "一只小青蛙" --text "你好"
python -m src.cli send --text "你好" --wait 15
```

发送链路优先走页面内 SDK：

```javascript
window.__monaGlobalStore.getData('initContextData').im.sendText(convId, text)
```

成功时服务端会回 ACK 帧（含 `message_logid`、`s:direct_call_rpc: true`）。

### 调试抓包

```bash
python scripts/debug_mode.py
python -m src.cli analyze
python -m src.cli status
```

### 离线测试入站解析

```bash
python scripts/test_inbound_parser.py
```

对 `captures/raw/*ws_frame_received.json` 回放解析，验证买家消息能否被识别。

## 技术说明

### WebSocket 协议

飞鸽实时消息走 `wss://ws.fxg.jinritemai.com/ws/v2`，帧体为 **protobuf 二进制**（CDP 中以 base64 传递，落盘时同时保存 `payload_hex`）。

解析要点（见 `src/monitor/pigeon_frame_parser.py`）：

- 消息元数据在内嵌 JSON：`{"direction":1,"msg_type":1000,...}`  
  - `direction=1` 买家，`2` 卖家，`3/10` 系统
- 正文在 protobuf 字段 `#8`（tag `0x42`）或 `type|text` 标记前的嵌套字符串
- **注意**：十六进制字符串必须先于 base64 解码，否则会把 hex 误当 base64 导致解析失败

### 发送 vs 模板重放

手改 protobuf 模板会破坏 `e807` 签名字段，服务端会静默丢弃。正确做法是调用页面 SDK 的 `sendText`，由飞鸽前端生成合法帧。

### 入站监听

`CDPMonitor` 收到 `Network.webSocketFrameReceived` 后交给 `InboundListener`：

1. `parse_inbound_frame()` 解析 protobuf + JSON 元数据
2. 去重、过滤 UI 噪声（欢迎语卡片、时间戳、UUID 等）
3. 控制台打印 `[时间] [角色] 昵称 | 会话=... | 正文`

## 开发说明

- `profiles/`、`captures/` 已在 `.gitignore` 中排除，不会上传登录态和抓包数据
- 修改解析逻辑后可用 `test_inbound_parser.py` 对历史抓包回归
- 参考项目：`抖店机器人` 中的 `feige-web-message-parser.js`

## 许可证

私有项目，仅供学习与内部使用。
