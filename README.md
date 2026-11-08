# travel-agent

面向国内旅行的多 Agent 规划助手。目标是把分散的攻略、地点、路线和费用信息整理成有依据、可修改的行程，并在购买等操作前由人审批。

## 当前状态

2026-11-08：完成 M1 离线需求解析和费用核算，提供本地 CLI 与只读 API。尚未接入实时来源、Agent 编排、完整行程或交易，尚不可正式上线。

远程仓库：[s1ty0/travel-agent](https://github.com/s1ty0/travel-agent)。

## 核心需求

- 目的地支持“南京”“上海”等具体地点，也支持“南京附近”“上海周边”等范围。
- 预算支持“5000以内”“2000元”“2000～3000”，同时明确总额/每人/每天和费用包含项。
- 国内模型和来源优先，逐步融合攻略、地图、餐饮、交通住宿及后续供给。
- 多 Agent 按任务专精和并行；预算、时间、证据及权限由程序校验。
- 推荐附来源与获取时间，区分事实、估算和待核实项目。
- MVP 只读规划、修改、导出和平台跳转；后续交易实行具体动作审批。

## 文档入口

| 文档 | 用途 |
| --- | --- |
| [AGENT.md](AGENT.md) | 项目长期记忆、已确认需求、可信与审批边界。 |
| [AGENTS.md](AGENTS.md) | 开发助手的读取入口和维护约定。 |
| [PLAN.md](PLAN.md) | 总体开发计划、任务依赖和验收标准。 |
| [首轮调研](docs/research/2026-11-08-landscape.md) | 同类产品、开源项目、接口与核验程度。 |
| [ADR-0001](docs/decisions/0001-evidence-first-workflow.md) | 当前架构选择、替代方案和验证条件。 |
| [开发日志](docs/devlogs/2026-11-08-bootstrap.md) | 本次实际操作、验证与未完成事项。 |
| [M1 开发日志](docs/devlogs/2026-11-08-m1.md) | 离线模块、测试、安装包验证和远端状态。 |
| [CHANGELOG.md](CHANGELOG.md) | 面向使用者的变更。 |
| [测试门禁](docs/TESTING.md) | 本地检查命令、分层测试及正式上线条件。 |

## 如何继续开发

下一步进入 M2：验证一个国内模型、一个搜索源与高德，建立带证据的单 Agent 基线，再比较多 Agent 的质量、耗时和成本。

需要 Python 3.11 或以上，以及 uv。本次本地验证使用 Python 3.13.14、uv 0.12.5；应用和测试依赖已锁定于 `uv.lock`。

```powershell
uv sync --locked
uv run --no-sync travel-agent --destination "南京附近" --budget "2000~3000"
uv run --no-sync uvicorn travel_agent.api:app --host 127.0.0.1 --port 8000
```

本地 API 文档位于 `http://127.0.0.1:8000/docs`。`GET /health` 显示阶段；`POST /v1/intake` 解析专用目的地和预算字段；`POST /v1/budget/check` 核算声明范围内的费用。当前不从整段聊天自动抽取全部日期/人数，也不核实地图实体。

预算口径支持总额、每人、每天及每人每天。单值先作为目标；“以内”作为上限；区间下限默认是偏好。房晚、人数、税费与预备金分别核算；未知成本或未确认口径不显示为通过。API 返回金额单位为分。

```powershell
uv run --no-sync python scripts/check.py
uv build --out-dir artifacts/dist
```

测试报告保存在被忽略的 `artifacts/qa/`。当前全量指全部已实现的离线功能；真实集成、端到端、安全、性能与预发布测试仍是正式上线前的必需门禁。

## 数据与许可

这是 Public 仓库。真实个人信息、密钥、Cookie、订单及运行日志不进入 Git。后续使用脱敏样例和独立私有存储。

本项目许可证待定；公开可见不等于已经授予开源许可。复用第三方组件时单独核对具体版本、许可证和保留要求。
