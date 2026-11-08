# 搜索、地点与路线来源

来源层供后续 Agent 共用，不执行模型生成、订单或支付。搜索摘要和供应商记录是待核查资料，不能因接口成功就标为旅游事实已核实。

| 操作 | 用途与单位 | 边界 |
| --- | --- | --- |
| `search` | DeepSeek 结构化搜索，默认 5 条，允许明确设置 1～50。 | 标题、短摘要、链接、获取时间；不获取全文、不自动翻页。 |
| `places` | 高德指定城市内搜索 POI（地点）。 | 保留地点 ID、地址、行政编码、可用坐标；不承诺营业或可预订。 |
| `nearby` | 在 GCJ-02 中心坐标周围找地点，半径 1～50000 米。 | 这是附近 POI，不是“南京周边旅游城市”发现。 |
| `walking` | 高德步行路径，距离为米，时间为预计秒数。 | 不是驾车、火车或实时票务；不使用直线距离推断路线。 |

地图关键词/周边查询每页 1～25 条，显式页码最多 8。供应商给出有效总数时才提示下一页；不会自动翻页或重试。坐标只接受标记的 GCJ-02，不把其他坐标系静默当成高德坐标。未知坐标保留 null，非法条目单独剔除并显示缺口。

## 本地运行

DeepSeek 使用 `.env` 的 `DEEPSEEK_API_KEY`。高德使用 `AMAP_WEB_API_KEY`，类型为 **Web 服务 API**，不是浏览器 JS Key。当前密钥均只保存在 Git 忽略的本地文件，不上传。

```powershell
uv sync --locked --cache-dir .cache/uv
'{"query":"南京博物院 官方 预约 开放时间","count":3}' | uv run --cache-dir .cache/uv --env-file .env --no-sync travel-agent-sources search --live
'{"keywords":"博物馆","city":"南京","count":5}' | uv run --cache-dir .cache/uv --env-file .env --no-sync travel-agent-sources places --live
'{"keywords":"餐厅","center":{"longitude":118.8,"latitude":32.0},"radius_meters":2000}' | uv run --cache-dir .cache/uv --env-file .env --no-sync travel-agent-sources nearby --live
'{"origin":{"longitude":118.8,"latitude":32.0},"destination":{"longitude":118.81,"latitude":32.01}}' | uv run --cache-dir .cache/uv --env-file .env --no-sync travel-agent-sources walking --live
```

示例坐标仅说明输入格式，不是推荐路线。使用 UTF-8 管道；旧版 Windows PowerShell 可先设置 `$OutputEncoding = [System.Text.UTF8Encoding]::new()`。没有 `--live` 不联网，每次命令最多一条请求。单次请求控制不等于已经实现整次规划的人民币费用上限。

## 状态和留痕

- 每批都有 `run_id`、供应商、操作、UTC 获取时间、查询哈希、来源 ID、耗时和请求次数。实际账单金额未知时为 null。
- `ok` 仅表示记录解析成功；`facts_verified=false` 始终保留。搜索摘要、地点记录、路线估算分别标记证据类型。
- 正常空列表为 `empty`。坏格式、重复或链接不合规为 `partial`；全部被剔除也不能伪装成正常空结果。
- 展示链接只允许 HTTP/HTTPS，拒绝登录凭据、已识别的 token 参数和明显本地/私有地址。此层不请求链接；链接过滤不是完整网页抓取或 SSRF 防护。
- CLI 在 `runtime/evidence.sqlite3` 保存最小来源索引：查询哈希、来源定位、状态和结果哈希。默认不保存查询明文、标题、摘要、坐标、请求头、密钥或完整响应。
- 来源索引可定位和比较结果，不能完整重放原始正文或恢复路线参数。可重放快照需要另行核实许可和私有保存范围。
- 用户主动提供的正文另有独立授权的快照与离线定位，见 [资料说明](REFERENCES.md)；这不代表已保存真实供应商响应，也不把粘贴正文标成网站抓取。
- 重复 `run_id` 不覆盖旧记录；这不是对有数据库写权限者的防篡改保证。SQLite 使用事务并显式关闭连接。
- 保存失败时仍输出可用结果，同时给安全警告和退出码 3。数据库及 QA 报告被 Git 忽略，不能自动上传 Public 仓库。
- 查询哈希用于关联，不等于匿名化。来源链接本身也可能反映旅行需求，仅供私有运行存储。

HTTP 错误正文不回显；鉴权、余额、限流和超时分别返回安全错误码。高德 HTTP 200 还检查业务 `status/infocode`。默认全量测试用模拟传输，不读取密钥，不消耗额度。

真实调用结果见当次开发日志。搜索不会自动覆盖小红书等受限平台。单 Agent 只读工具编排与有限草案已实现，见 [规划说明](PLANNER.md)；网页正文获取、现实事实核验和完整旅游需求仍未通过验收。

依据：[DeepSeek 搜索](https://api-docs.deepseek.com/guides/search/)、[高德 POI](https://developer.amap.com/api/webservice/guide/api/search/)、[步行路线](https://developer.amap.com/api/webservice/guide/api/direction)。按实际账号权限与联调结果使用，扩大保存内容前核实来源许可和保留规则。
