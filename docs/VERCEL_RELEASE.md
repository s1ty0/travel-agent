# Vercel 公开发布检查

当前版本可以在本机使用真实来源生成单城行程草案。面向公众的真实规划服务暂缓发布。以下判断针对现有仓库和公开文档，尚未创建 Vercel 项目或预发布环境。

首发按用户确认跳过小红书。匿名会话首次进入可保存自己的 DeepSeek Key 或先试用；不引入账号。

| 检查项 | 当前状态 | 发布前要完成 |
| --- | --- | --- |
| 网页到 API | 本机继续用回环地址。云端要求显式配置 HTTPS 网页与 API 地址，保留同源、路径白名单和桥接鉴权，会话 Cookie 使用 Secure。 | 在 Vercel Preview 和真实后台验证连接与重定向行为。 |
| 任务与历史 | 本机 SQLite；云端 API 和工作入口使用共享 PostgreSQL。创建和认领使用数据库事务和事务级锁。 | 用 Vercel 前后端 Preview 验收持久任务、服务中断和跨实例幂等。已启动任务失联后保留中断状态。 |
| 访问和费用 | 不设账号。匿名会话最多3个管理员额度；同网络滚动24小时最多3个，全站试用总量默认0。自己的 Key 可加密保存7天并复用。 | 预发布使用 Vercel Deployment Protection，验收可信网络标识、费用与供应商配额告警和关停。 |
| 高德权限 | 现有 Key 已通过本机联调，免费配额耗尽即停。 | 按实际账号和产品确认公开访问、数据展示及保存范围。核实地点和路线能否进入任务历史与导出。如有缺口，按获准范围改数据保留或替换来源。 |
| 发布验收 | 本机真实冒烟和离线 CI 已有记录。 | 在候选部署环境执行[正式测试门禁](TESTING.md)，重点实测中国大陆目标网络、真实来源、权限隔离、并发和限额、进程重启、备份恢复与回滚。 |

创建两个 Vercel 项目。Next.js 项目的代码根目录是 `web`，FastAPI 项目使用仓库根目录 `app.py`。后者只挂载已有桥接鉴权的规划接口，`/health` 只返回健康状态。数据库从 Vercel Marketplace 接入 Neon Preview，持久历史与工作进程共享。供应商密钥放在后端项目。[Vercel FastAPI 文档](https://vercel.com/docs/frameworks/backend/fastapi) · [仓库根目录设置](https://vercel.com/docs/monorepos) · [Vercel 的 SQLite 说明](https://vercel.com/kb/guide/is-sqlite-supported-in-vercel)

## 预发布配置

- Vercel 前端 Preview 配置 `TRAVEL_UPSTREAM_URL`、`TRAVEL_WEB_TOKEN`、`TRAVEL_VERCEL_WORKER=1`，需要绕过 API Preview 自身访问保护时在前端服务端配置 `TRAVEL_UPSTREAM_VERCEL_BYPASS`。访问默认生成的部署域名时由 `VERCEL_URL` 提供网页来源地址。使用固定域名时，设置 `TRAVEL_PUBLIC_ORIGIN` 为实际访问的 HTTPS Origin。地址不带路径、查询或用户名。
- API Preview 连接 Neon，提供 `DATABASE_URL`。可用 `TRAVEL_DATABASE_URL` 显式指定。后端使用相同的桥接密钥。真实模式需要独立的 `TRAVEL_TASK_KEY_ENCRYPTION`。合成 Preview 设置 `TRAVEL_DEMO_MODE=1`、`TRAVEL_WEB_LIVE=0`。
- 网页接单后用 Next.js `after` 在响应发出后触发 FastAPI 工作入口，轮询发现任务仍在排队时也可触发。后台检查桥接凭据和任务归属，数据库确保最多一次认领。已启动任务失联后不自动再付费。浏览器离开后，如果触发动作在工作入口认领前丢失，回来轮询可以恢复排队任务。云端任务结果保存来源与运行事件，单独本地 SQLite 审计不启用。[Next.js after 文档](https://nextjs.org/docs/app/api-reference/functions/after)
- 真实后端需设置 `TRAVEL_DEMO_MODE=0`、`TRAVEL_TRIAL_ENABLED=1`，按发布决定启用 `TRAVEL_WEB_LIVE=1`。`TRAVEL_TRIAL_GLOBAL_CAP` 是整个数据库生命周期内的管理员试用任务总量。`0` 只允许自带 Key；提高上限会允许额外管理员消耗。用户提交的 Key 供该会话的 DeepSeek 模型和搜索使用，高德仍使用后台配额。按同一请求去重；成功入队即占一格，取消或失败不返还。
- 代理仅信任 Vercel 注入的网络地址，以桥接凭据做 HMAC，后端只存摘要。无可信地址时仍可使用自带 Key。IPv4 按地址、IPv6 按 `/64`，共享网络会共用额度。网络变化不能绕过全站总量。
- 用户 Key 通过单独接口提交，只保存密文和过期时间，页面只读到是否已保存。入队给 worker 的加密副本最多保留5分钟，认领时清除。用户移除 Key 会停止新任务使用，已接受任务需用取消操作停止后续调用。部署方需禁止请求头和明文凭据进入日志。

验收顺序为检查服务连接、跨域拒绝、不同浏览器互不可见、试用三次与换 Cookie 限额、自己的 Key 优先、删除 Key 后的行为，最后验收真实草案、任务恢复和费用。回滚时先在后台停用真实规划，再切回上一已验证网页，避免未完成任务被自动重跑。[Vercel 环境变量](https://vercel.com/docs/environment-variables) · [预发布保护](https://vercel.com/docs/deployment-protection) · [可信请求头](https://vercel.com/docs/headers/request-headers)

高德协议区分个人研究学习等用途及技术服务许可，且对结果展示与数据保存设有条件。上线前以实际账号权限和高德答复确定方案。用户现有免费配额与停用规则仍适用。[高德服务协议](https://developer.amap.com/pages/terms/) · [高德技术服务许可](https://developer.amap.com/pages/authorization/)

首个公开版本的功能范围可以是已选城市 1～3 天的只读草案。周边城市发现、独立核验营业预约、实时票务酒店、购买审批和其他平台数据可分批交付。上线前要验收草案的来源与缺口展示，以及当前范围内的真实行程质量。

## 美团和小红书

两个平台可按授权范围作为独立数据源。平台接入前先核对可查字段、结果展示、留存、配额和商用范围。首轮接入只读候选和可验证的官方链接，交易功能按项目的人审规则另行验收。

- **美团或大众点评**可补门店或团购信息。官方合作中心提供到店餐饮等场景，餐饮直连包含团购核销等商家能力。目前尚未确认项目有面向消费者的全量门店、点评、实时团购或购买权限。[美团技术服务合作中心](https://developer.meituan.com/) · [餐饮直连平台](https://o.dianping.com/)
- **小红书**可补攻略、体验和避坑线索。已查官方电商平台与开发文档，公开资料尚未确认本项目可用的全站笔记搜索或 AI 搜索总结接口。可先接经核实且获准处理的用户分享内容，再评估官方合作。平台摘要可辅助找候选，预约和价格按相应来源核实。[小红书电商开放平台](https://open.xiaohongshu.com/) · [前期调研](research/2026-11-08-landscape.md)

聚合时按店名、地址和地点标识匹配同一家门店。地图地点、攻略、实时价格各带来源和查询时间。先查少量候选，只有缺证据时才扩查。
