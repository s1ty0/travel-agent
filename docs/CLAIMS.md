# 首版预约主张核查

本模块区分三个问题：资料属于谁、正文是否支持这句话、正文给出的日期是否覆盖出行日。首版只检查预约要求，不是通用事实认证，也不判断余票或是否已完成预约。

## 输入与结果

规划活动可选 `claims`，每项最多 3 条。当前只接受 `predicate="reservation_required"`，`value=true` 表示候选主张“需要预约”，false 表示“无需预约”。模型不能提交 `facts_verified`、认证身份或自行指定证据有效期。

```json
{"claims":[{"subject":"合成测试馆","predicate":"reservation_required","value":true,"source_id":"替换为实际取得的来源ID","quote":"须预约"}]}
```

这是活动字段片段，须并入完整规划输入/模型提交。主题名称应与地图地点完整名称一致；字符串相同也只算名称匹配，不证明已经完成实体消歧。同名、别名和分馆不能静默合并。

| 结果 | 含义 |
| --- | --- |
| `needs_review` | 仍需核实。可以是正文支持但身份/日期不足，也可以是语法、条件或上下文无法可靠解释。 |
| `rejected_evidence` | 这份引用不能支撑所提交的主张，例如未取得来源、引文缺失/歧义、正文明确相反或出行日不在明确范围内。不是断言现实中的场馆规则一定相反。 |
| `facts_verified=false` | 当前没有官方身份与完整事实核验闭环，始终保留。汇总 `verified_facts=0`，不提供自动变成已认证事实的路径。 |

普通 `citations` 继续只检查引用位置；需比较具体预约主张时使用 `claims`。模型不提交主张时，不从一句普通引文里自动猜出全部事实。

## 来源身份

- 用户正文和链接始终是用户提供，不因标题含“官方”或 URL 指向政府网站而升级身份。
- 搜索摘要是检索线索，不能替代网页正文进行本模块的语义支持判定。
- Fetch 正文按精确请求地址查询本地站点档案，区分场馆站点候选与政府门户历史新闻页。政府页主办信息及观察到的发布日期来自本轮页面核对，属于本地观察记录，不是供应商返回正文的认证。
- 当前 Fetch 契约仍无最终网址、跳转链或签名，`content_origin_verified=false`。档案处于审查有效期内只说明档案未过期，不证明官方身份或旅游规则有效。档案期限仍到 2026-11-15。

## 正文支持

规则版本为 `reservation-rule-v1.1`。先在实际取得的文本版本中精确定位引文，再检查包含引文的完整段落；不能只截取“无需预约”等词就忽略前面的否定、例外或主题。

首版仅识别以完整主题名开头的明确句式：`实行“全员预约机制”`、`须预约`、`须实名预约`、`需要预约`、`需要实名预约`、`无需预约`、`不需要预约`。允许末尾句号；全员预约机制还接受英文双引号。

包含其他说明、例外、条件、部分人群、改写、转述或复杂语句的段落不自动补齐含义。跨段引用保持不足；全文中的可识别反驳/历史表述或相反规则会进入待复核，不擅自按段落顺序选“最新”。只有另一完整规则的明确日期范围排除该出行日时，才排除该条冲突。

`semantics.status` 为 `supported/contradicted/insufficient`，含义是有限句式内的文本关系。支持仍限定 `reservation_policy_text_only`；`broader_context=not_exhaustively_interpreted` 明确没有完成全文语言理解。普通“上述预约渠道”本身不是反证，不能仅凭一个提示词拦截。

## 日期适用

出行日由活动天数加 `trip.start_date` 计算。只认同一完整规则段落里明确写出的双边日期范围，例如合成句：

```text
2026-11-01至2026-11-30，合成测试馆须预约。
```

两个边界均须含年份，支持 `YYYY-MM-DD` 或 `YYYY年M月D日`，连接词为“至”或“到”，后接逗号或冒号。范围包含起止两日。其他格式、缺年份、开放边界、节假日例外或复杂排期不能自行推断。

| 日期状态 | 含义 |
| --- | --- |
| `within_textual_range` | 出行日在该段文字声明的范围内；不是现实规则已认证。 |
| `outside_textual_range` | 这份引用的明确范围不覆盖出行日，拒绝该份依据。 |
| `travel_date_missing` | 未提供出行日。 |
| `invalid_textual_range` | 文字日期非法或范围倒置。 |
| `unknown` | 没有可检查的明确范围。 |

采集时间、URL 内日期、发布日期、其他段落日期和用户资料的声明字段都不自动变成生效范围。旧新闻不能被推成当前规则，也不能只因年代旧就断言规则必定已失效。

## 本地离线检查

`POST /v1/claims/inspect` 接收用户资料、结构化主张和可选 `at_date`。不读密钥、不联网、不送模型、不保存正文，即使输入资料允许共享/快照也不触发相应动作。重复资料或非法字段返回 422。当前仍是本地接口，未完成网站鉴权与线上验收。

可直接用 Python 跑合成例子，资料 ID 由同一份输入计算：

```python
from datetime import date
from travel_agent.claim_review import ClaimInspection, FactClaim, inspect_claims
from travel_agent.references import ReferenceInput, import_reference

material = ReferenceInput(
    title="合成示例，非现实规则",
    text="2026-11-01至2026-11-30，合成测试馆须预约。",
)
source_id = import_reference(material).source_id
result = inspect_claims(
    ClaimInspection(
        references=(material,),
        claims=(
            FactClaim(
                subject="合成测试馆",
                predicate="reservation_required",
                value=True,
                source_id=source_id,
                quote="须预约",
            ),
        ),
        at_date=date(2026, 11, 10),
    )
)
```

结果为文本支持、文字范围覆盖、用户来源未认证，最终仍待核实。

## 规划、费用与留痕

核查在服务端提交校验时执行，不增加模型回合、来源调用或费用。已有规划 2 元上限、来源次数和失败停止规则不变。`rejected_evidence` 会给出 `claim_evidence_review` 失败，将带该错误引用的方案标为 `invalid_plan`；其他不足保持草案并明确待核实。

终端展示候选主张和判定，并提示“需预约不表示已预约或有名额”。审计只存规则版本、主张/核查/正文哈希、已知来源 ID 和状态计数，不存主题名、主张引文或完整正文；未知的模型自造来源 ID 只参与哈希，不原样落盘。哈希用于关联，不等于匿名化或防篡改签名。

核查指纹绑定输入、来源版本、出行日、检查日、地点名称和规则版本；规则语义变更时更新版本。开发合成回归不属于独立保留评测，结果与限制见 [本轮日志](devlogs/2026-11-09-m2-claim-review.md)。

同一接口和规划现会另做 [多份资料比较](COMPARISON.md)，返回 `comparisons/cross_source_summary`，检查未被模型选中的可用正文、候选关联与冲突。它不替代本页单条引文判定，不会用其他正文自动修复错误引用，也不把来源数量提升为独立证明。
