# 用户资料、引文定位与离线重放

本模块解决“依据在哪里”，不替代“这件事现在是否真实”的核验。支持用户有权提供的正文、搜索摘要或单独链接，不自动抓取网站。

## 输入与独立授权

每份资料有 `title`、可选公开 `url`、可选 `text`，链接/正文至少有一个。正文最多 8000 个 Unicode 字符，一次最多 5 份。类型仅为 `user_text` 或 `search_snippet`；只有链接自动标为 `link_only`，不能输入“官方已核实”来提升可信度。

| 字段 | 默认 | 含义 |
| --- | --- | --- |
| `allow_model_use` | false | 明确允许将该份资料的标题、链接、正文送给模型；未共享资料不进入提示和模型结果的参考列表。 |
| `allow_snapshot` | false | 明确允许本地保存正文与元数据；允许模型使用不代表允许保存，反之亦然。 |
| `valid_from/valid_until` | null | 用户声明的适用日期；不是系统核实的营业日期。 |

导入不读密钥、不调用模型、不访问链接、不自动保存正文。标题与链接也可能包含私人需求。`allow_snapshot=true` 表示用户确认有权在私有运行目录保存这些内容，不能代替平台许可核查。

## 本地使用

以下是合成测试资料，不是真实旅游规则。保存为本地 `runtime/reference-input.json`：

```json
{"references":[{"title":"合成参观说明，非实际事实","text":"测试资料第一行。\n合成规则：请提前预约。","allow_model_use":false,"allow_snapshot":true}],"quotes":[]}
```

```powershell
Get-Content -Raw -Encoding utf8 runtime/reference-input.json | uv run --no-sync travel-agent-evidence inspect
Get-Content -Raw -Encoding utf8 runtime/reference-input.json | uv run --no-sync travel-agent-evidence save
```

`inspect` 不创建保存目录，返回资料 ID、正文哈希、类型和导入时间。`save` 仅在每份资料 `allow_snapshot=true` 时写入 `runtime/reference_snapshots/`，返回 `snapshot_id`，不覆盖已有快照。

用返回的 `source_id` 构造 `runtime/quote-input.json`：

```json
{"quotes":[{"source_id":"替换成返回的doc_ID","quote":"请提前预约","at_date":"2026-11-08"}]}
```

```powershell
Get-Content -Raw -Encoding utf8 runtime/quote-input.json | uv run --no-sync travel-agent-evidence replay --snapshot <snapshot_id>
uv run --no-sync travel-agent-evidence delete --snapshot <snapshot_id>
```

输入管道使用 UTF-8。`replay` 不联网、不消耗模型/搜索额度。`delete` 仅删除指定快照，不删除用户原文件或已导出的副本，也不能撤回曾发给供应商的资料。

## 定位结果

| 状态 | 实际含义 |
| --- | --- |
| `matched` | 引文存在且唯一，或显式 `start_offset` 精确匹配。 |
| `ambiguous` | 引文出现多次，不随意选第一处；可指定偏移再查。 |
| `missing` | 正文或指定位置无该引文；不改写、不近似补齐。 |
| `link_only` | 没有可检查正文；不能声称已读、链接无法读取或其中存在某句话。 |
| `unknown_source` | ID 不在本次资料里，不能拿未共享或未加载内容支持引用。 |

位置为从 0 开始的 Unicode 码点偏移，结束位置不包含在内，附从 1 开始的行号。不是 UTF-8 字节或 JavaScript UTF-16 偏移，前端需转换。

日期独立返回 `outside_declared_range/within_declared_range/unknown`。只有检查日期和两个边界齐全且在范围内，才显示在声明区间内；单侧日期不补齐未知边界。任何状态均保留 `facts_verified=false`。匹配不证明来源官方、内容真实、断章取义不存在或当日有效。

## 快照与保存边界

资料 ID 哈希包含正文、元数据和权限。修改权限会产生新 ID；正文哈希另存。相同内容重复导入可有同一 ID、不同导入时间。

快照名是精确文件内容的 SHA-256。重放先校验大小和文件哈希，再核对格式版本、正文哈希、资料 ID、保存权限。拒绝路径穿越和快照文件符号链接，不执行文件中的代码。哈希不是签名，不防御可重写数据库和快照的本地用户。

快照是本地明文，尚无多用户隔离、加密、自动保留期限，不能直接作为公开网站资料库。快照目录受 Git 忽略。以前的 SQLite 最小来源索引不含全文；本模块也不代表已完成真实 DeepSeek/高德 API 响应快照或整个模型运行重放。

## 接入规划与 API

规划输入增加 `references` 数组。只有 `allow_model_use=true` 的资料进入模型，模型引用其 ID 与逐字正文后由程序再定位。规划 CLI 不因允许快照就自动保存全文；未共享、只有链接、缺失或重复歧义引用不能通过。资料中的购买/升级指令不能增加工具权限。

单份导入上限不保证全部资料都能进入规划。模型请求连同历史、工具定义和共享资料总计最多 48000 UTF-8 字节；超过时停止，不偷偷截断正文或绕过服务预算。可减少资料或由用户提供必要摘录。

`POST /v1/references/inspect` 是本地无状态离线检查；即使输入允许共享或保存，也不调用模型、不存正文。重复资料或非法输入返回 422。仍需网站阶段鉴权，不直接对外部署。

当前完成用户提供正文的定位。站点正文获取、官方身份、事实语义、矛盾和完整保留评测仍待完成。[DeepSeek Fetch](https://api-docs.deepseek.com/guides/search/) 虽有读取接口，本轮没有自动接入或消费该配额。
