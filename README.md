# 行迹 · travel-agent

[打开公网正式版](https://travel-agent-web-tau.vercel.app)。使用 DeepSeek 实时模型与搜索、高德地点、附近餐饮和步行路线查询，不设两城白名单。可输入日期、人数、预算，生成带午晚餐、转场和出发前清单的行程单并导出。首次打开可填写自己的 DeepSeek Key 或先试用，网页和后台均已部署到 Vercel，历史保存在独立的 Neon PostgreSQL。

从一句旅行想法生成带来源、可修改的1～3天行程草案。提供响应式网页、景点与餐饮候选、确定性时间和交通检查、私有历史版本及三种AI研究策略。

当前交付包含公网真实规划和本机离线测试模式。本机可使用原有 DeepSeek 与高德配置；公网恢复高德真实查询；OSM 城区快照保留为显式选择的适配器与测试资料，不强制用于公网。合成数据仅用于本机或 CI。营业、预约、票务、酒店和旅行总费用缺失时保留待核实。[发布验证](docs/devlogs/2026-11-11-public-live-release.md)

公网真实流程截图见[发布验证](docs/devlogs/2026-11-11-public-live-release.md)，历史合成界面截图仍保留在本机演示文档中。

## 一键演示

需要Windows、Python3.11以上、uv与Node.js24/npm。克隆仓库后，在根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/demo.ps1
```

打开<http://127.0.0.1:3000>，输入“杭州两天，预算2000以内”，再发送“第二天下午五点结束”。查看来源、检查、历史版本并导出Markdown。首次安装依赖需要网络，规划演示不需要密钥。完整流程、真实模式及可选Docker文件见[演示说明](docs/DEMO.md)。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/stop-web.ps1
```

## 已实现的闭环

- **先给草案，再改条件。** 每日景点、餐厅候选、交通缓冲、午晚餐和晚间留白，未知费用不记作零。
- **模型研究，程序检查。** 标准联合研究、并行工具、体验/餐饮专精并行可切换；来源ID、数量、逐日覆盖、时间、步行与禁去约束由程序检查。
- **有界消耗。** 每次5元服务估算上限、并发原子预留、调用次数/截止时间；未知用量、过期价格卡和高德免费配额故障停止后续调用。
- **任务与版本可恢复。** SQLite事务幂等、独占认领、心跳、中断不重放、取消、匿名私有会话逐资源授权、7天版本保留和整次删除。
- **可复现验证。** 30个冻结合成场景×三策略回归、真实受控批次、桌面/手机浏览器联调；失败、拒绝和草案分开统计。

Next.js / React / TypeScript + FastAPI / Pydantic / HTTPX + SQLite WAL + DeepSeek。多Agent采用自建固定分支汇合，未使用LangGraph或向量数据库。

## 文档与结果

| 入口 | 内容 |
| --- | --- |
| [架构](docs/ARCHITECTURE.md) | 数据流、任务可靠性、费用、授权和事实边界 |
| [演示](docs/DEMO.md) | 一键启动、三分钟讲解、真实模式与手机截图 |
| [评测报告](docs/EVALUATION_REPORT.md) | 数据指纹、对照协议、真实/合成及失败记录 |
| [简历与面试](docs/RESUME.md) | 可复用描述、设计取舍与不能夸大的内容 |
| [总体计划](PLAN.md) / [长期记忆](AGENT.md) | 已确认需求、当前交付与后续工作 |
| [网页工作台](docs/WEB.md) / [测试](docs/TESTING.md) | 使用、开发和正式上线门禁 |
| [模型](docs/DEEPSEEK.md) / [来源](docs/SOURCES.md) | 本地配置与只读权限 |
| [原单Agent](docs/PLANNER.md) / [旧冻结契约](docs/EVALUATION.md) | 保留基线与历史回归 |
| [调研](docs/research/2026-11-08-landscape.md) / [决定](docs/decisions/0021-offline-demo-and-release-evidence.md) | 接口权限与技术选择 |
| [S4开发日志](docs/devlogs/2026-11-11-resume-s4.md) / [变更](CHANGELOG.md) | 验证、版本与未完成事项 |

一次10例真实模型/固定来源验收：6草案、4例预期拒绝，模型费用上界估算0.048764元，账单未对账。一次全部真实来源的双Agent南京冒烟：7052毫秒、角色请求重叠1076毫秒。小样本不证明稳定加速或完整真实旅行质量，详见报告。

## 开发检查

```powershell
uv sync --locked
uv run --no-sync python scripts/check.py
npm --prefix web ci --registry=https://registry.npmjs.org
npm --prefix web run lint
npm --prefix web run format:check
npm --prefix web run check
npm --prefix web test
npm --prefix web run build
npm --prefix web exec -- playwright install chromium
npm --prefix web run test:e2e
```

浏览器测试启动本机合成API，不使用真实供应商密钥。先停止已有本机服务；复用到真实模式时，完整联调测试在提交前拒绝。原Python CLI、资料核查与修正实验继续保留，安装wheel仅提供Python服务；完整网页演示请使用仓库源码。

周边城市发现、正式账户、独立事实核验、酒店票务实时库存、真实交易审批、原生App与持续生产运维仍在后续计划。小红书/美团全量查询授权未确认；没有真实用户规模或省时数据。许可证尚未选择，公开仓库不等于已授权任意复用。
