# HTTP 接口约定

本文描述当前 `server.py` 的本机接口，不代表 Dify 或豆包原始 API。默认基础地址 `http://127.0.0.1:8000`，网页同源调用；目前没有用户鉴权。

## 1. 通用规则

- 所有 POST 请求使用 `Content-Type: application/json`，正文必须是 JSON 对象。
- 普通 POST 正文最多8192字节；`/api/test/plan` 和 `/api/test/prepare` 最多131072字节。
- 无效 JSON / 字段通常返回400；空正文或超限返回413；Content-Type 不符返回415。
- API 成功的 JSON 与流式响应均带 `Cache-Control: no-store`。
- 非流式 API 错误形状为 `{"error":"可读的错误信息"}`。静态文件及未登记的 GET 路径使用标准 HTML 错误页，不能假定所有404都是 JSON。
- 流式响应一旦发出响应头，后续失败通过 `event:error` 表示，HTTP 状态可能仍为200。必须看到有效 `done` 才算完成；EOF 不是成功标记。
- 音色由后端固定，不支持通过请求切换模型、音色或采样率。密钥从本机配置读取，网页不提交供应商密钥。
- 没有自动重试或请求级幂等标识。超时后不要自动重发有云端用量的请求。

## 2. GET 接口

| 路径 | 用途 | 主要响应 |
| --- | --- | --- |
| `/api/health` | 启动器确认服务及项目目录 | `app`、`project` |
| `/api/avatar` | 检测固定人物素材 | `image`、`video`，缺少时为 `null` |
| `/api/tts` | 检查语音配置和缓存状态 | `configured`、`voice`、`cache_available`、`cache` |
| `/api/test/cases` | 读取完整测试配置 | `cases`、`revision` |

### `GET /api/health`

```json
{"app":"dth-avatar","project":"当前项目的绝对路径"}
```

不调用模型。启动器还会核对 `project`，避免打开其他目录的同类后端。

### `GET /api/avatar`

```json
{"image":"png/avatar.png","video":"png/avatar.webm"}
```

视频优先级为 WebM、MP4、无视频。路径必须是固定入口，不返回任意用户指定路径。前端对允许值再次验证。

### `GET /api/tts`

```json
{
  "configured": false,
  "voice": "魅力女友 2.0",
  "cache_available": true,
  "cache": {"entries": 0, "bytes": 0, "hits": 0}
}
```

`configured` 只表示密钥非空，不证明权限或额度有效。`cache_available` 表示本机缓存可用，不表示目标音频已经存在；数据库尚未创建时也可为 true。统计包含库中不同音频格式的条目。

此请求不会合成语音。没有密钥但有对应缓存时仍允许播放。

### `GET /api/test/cases`

`cases` 与 `test_cases.json` 内容一致；`revision` 是内容摘要，用于拒绝过期准备计划。配置缺失、损坏或字段不合法时返回503。字段约束见 [架构](ARCHITECTURE.md)。

## 3. 模型聊天

### `POST /api/chat`

```json
{"message":"你好","conversation_id":""}
```

- `message`：去首尾空格后1～500字。
- `conversation_id`：可省略或为空，创建新会话；非空时需为合法 UUID。
- 要流式回复，发送 `Accept: text/event-stream`。否则返回完整 JSON。
- 本机没有 Dify 密钥时返回503；不会回退到本地规则。本地规则分支在 JS 内执行。

完整 JSON：

```json
{"reply":"模型回答","conversation_id":"00000000-0000-4000-8000-000000000001"}
```

SSE 每个事件以空行结束，格式为 `data: <JSON>`。客户端处理 UTF-8 跨分包和事件跨分包，不按一次网络读取等于一条消息处理。

| `event` | 字段 | 处理方式 |
| --- | --- | --- |
| `start` | 无附加字段 | 本机流已启动，不代表模型已开始输出 |
| `conversation` | `conversation_id` | 新会话编号尽早保存 |
| `task` | `task_id` | 当前云端任务编号，可用于停止 |
| `delta` | `text` | 追加正文 |
| `replace` | `text` | 替换整段正文，旧朗读需要停止 |
| `ping` | 无附加字段 | 保持事件解析，不当作首字 |
| `done` | `conversation_id` | 完成；应已有非空正文 |
| `error` | `error` | 失败，保留已显示文字并标记未完成 |

新会话的编号和任务事件可能早于首字；已有会话不保证再次收到 `conversation`。不要依赖除控制含义以外的固定事件数量。

接口仅转发必要结果，不把工作流内部输入输出或供应商凭据发给网页。JSON 路径的上游 HTTP 错误返回502，连接错误返回504；SSE 路径在流内报告错误。

### `POST /api/chat/stop`

```json
{"task_id":"00000000-0000-4000-8000-000000000010"}
```

成功为 `{"status":"stopped"}`。后端仅接受当前活动任务，使用发起时相同的 Dify user。

| 状态 | 含义 |
| --- | --- |
| 400 | 任务编号非法 |
| 404 | 已结束或不属于本服务 |
| 502 | Dify 未确认停止，可人工重试 |
| 200 | 收到停止确认，非已用 token 的退款或计算终止时间证明 |

同一活动任务停止确认后不重复调用上游；失败时允许再次尝试。停止与自然完成竞争的处理见架构文档。

## 4. 语音播放与合成

### `POST /api/tts`

```json
{"text":"你好呀，很高兴又见到你。","cache_only":false}
```

`text` 去首尾空格后1～200字。`cache_only` 可省略，默认 false；如果提供必须是布尔值。true 表示未命中时禁止合成。

- `Accept: application/x-ndjson`：返回 PCM 音频事件流，网页使用此方式。
- 其他 Accept 或未指定：返回完整 `audio/mpeg` MP3；响应头 `X-Audio-Cache` 为 `hit` 或 `miss`。
- PCM 与 MP3 的缓存编号不同，不能互换。
- 缓存命中不要求密钥，也不占云端合成锁。
- 未命中时，仅普通路径会尝试合成。

常见 HTTP 错误：400字段无效，412只读缓存未命中，429已有云端合成进行中，503缺少密钥或严格缓存路径不可用。MP3 路径的供应商错误为502/504；已打开的 NDJSON 流用错误事件报告。

### NDJSON 事件

每行一个 JSON，以换行分帧。下面的 `data` 只是格式占位，不能作为真实音频播放：

```text
{"event":"start","sample_rate":24000,"format":"pcm_s16le","cache":"hit"}
{"event":"audio","data":"<Base64 编码的音频块>"}
{"event":"done","first_chunk_ms":1,"synthesis_ms":2,"cache_saved":true,"warning":null}
```

| 事件 | 校验与含义 |
| --- | --- |
| `start` | 仅一次；格式 `pcm_s16le`，采样率24000；`cache` 为 hit/miss |
| `audio` | 按到达顺序解码 Base64；单个采样可能跨块，不能丢掉多余的一字节 |
| `done` | 至少收到非空、总字节数为偶数的音频；仅此事件表示完成 |
| `error` | 带 `error` 字符串；本轮失败，没有自动重试 |

`first_chunk_ms` 与 `synthesis_ms` 从后端开始处理音频流计时，包含读取/保存等开销；命中时是本地读取转发耗时，不是实际云端推理。浏览器“开始播放”另行计时。

`cache_saved` 表示音频已在库中（包括命中），或本次完整合成后保存成功。普通模式保存失败可附带 `warning` 并继续播放；准备音频未保存成功必须报错。上游读取有边界限制，最大音频量4MiB。

## 5. 开发测试接口

### `POST /api/test/reply`

```json
{"message":"你好呀"}
```

```json
{"reply":"你好呀，很高兴又见到你。","source":"test_cache","case_id":"greeting"}
```

仅去除首尾空格后匹配已登记的别名。未收录返回404，配置错误返回409。此接口不调用 Dify，不创建、返回或改变会话编号。

无需外部服务的 PowerShell 示例：

```powershell
$body = @{ message = '你好' } | ConvertTo-Json
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/test/reply' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

### `POST /api/test/tts`

正文和响应格式与 `/api/tts` 一致，但后端强制只用缓存。即使提交 `cache_only:false` 也不能触发合成。未命中返回412，文本测试回答不因此丢失。

### `POST /api/test/plan`

输入对象结构：

| 字段 | 来源 |
| --- | --- |
| `revision` | 最近一次 `GET /api/test/cases` |
| `cases` | 覆盖完整配置，每项为 `{id, segments}` |
| `segments` | 对该项 `reply` 调用 `ReplySpeech.split(reply)` 得到的数组 |

单项最多24段，每段1～200字。所有段拼接后，忽略空白应与原台词完全一致；不能用缺省的一条案例代替完整配置。编号重复、缺失或正文变化都拒绝。

响应包含：

- `plan_id`：本次配置、分段与合成参数的摘要。
- `voice`、`can_generate`：当前音色名称、是否配置语音密钥；不验证云端授权。
- `items`：去重后的音频项，每项为 `{text,key,cached}`。

该操作不生成音频，但会重设当前测试音频的保护标记。配置、计划、数据库错误返回409。

### `POST /api/test/prepare`

正文为原计划请求的 `revision`、完整 `cases`，加上返回的 `plan_id` 和待准备项的 `index`。

`index` 是 `items` 的零起始整数索引；不能使用案例序号代替。合成前重新核对计划、台词与音色，失效时返回409，不请求供应商。

必须发送 `Accept: application/x-ndjson`，否则415。响应为与播放相同的音频事件流，但准备操作只解析并保存，不播放声音。

命中则直接返回；缺少密钥则503；未命中且具备密钥时调用豆包并保护保存的条目。网页逐项执行，取消后不再发起后续项；已经发出的上游合成可能仍计费。

## 6. 静态文件

`GET` / `HEAD` 仅服务 `PUBLIC_FILES` 登记的文件，支持单段 `Range: bytes=...`：

- 正常完整响应200，合法范围206，无效范围416。
- `Accept-Ranges: bytes`；部分响应包含 `Content-Range`。
- `HEAD` 只返回头部；API 路径没有单独的 HEAD 实现。
- 当前所有静态响应为 no-store，尚未实现 ETag / Last-Modified。
- Python 源码、配置、数据库、Git 内容和项目文档未开放为静态资源。

不要把供应商原始接口地址或密钥直接放到前端。当前后端上游适配见 `make_dify_request`、`stop_dify_task`、`tts.stream_speech`。
