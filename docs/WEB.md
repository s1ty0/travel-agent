# 行迹网页工作台

网页是正式网站的首条开发链路。用户先用自然语言描述旅行，再确认目的城市、天数、人数和总预算。点击生成后，页面调用现有 DeepSeek、高德和行程校验流程，显示逐日安排、来源、待核实事项及可下载的 Markdown 草案。

## 本地运行

需要 Python 3.11+、uv 和 Node.js 24。在仓库根目录执行：

```powershell
uv sync --locked
npm --prefix web ci
npm --prefix web run build
powershell -ExecutionPolicy Bypass -File scripts/start-web.ps1 -Live -Production
```

打开 <http://127.0.0.1:3000>。选择「南京 · 博物馆慢游」可填入示例问题，再确认条件并发起一次真实规划。示例按钮只填输入。自定义问题的城市、预算等硬条件由用户确认，当前没有自动识别整段问题的全部条件。

`-Live` 使用现有被忽略的根目录 `.env`。本机开发时省略 `-Production` 启动热更新。省略 `-Live` 可检查页面，但不能提交真实规划。脚本检查端口占用和前后端连通性。服务、进程记录和诊断日志在 `artifacts/web/` 对应的忽略目录中留痕，监听地址为本机 3000 与 8000。

## 当前能力

- 已选国内城市、1～3 天和总旅行预算，支持单值、上限及范围。出发日期与出发地可选。全程景点上限与一次冲突修正可在更多条件里调整。
- 行程按天展示。来源区显示搜索摘要、地图地点、路线估算和获取时间。检查区区分确定冲突与未知，旅行费用未知时不会显示为零或预算通过。
- 规划最多 2 元服务调用上限，与旅行预算分开。高德沿用已确认的免费模式，额度耗尽停止。页面发起真实任务时使用原有模型、来源、超时和预算规则。
- 刷新可重新读取同一临时任务。页面以相同请求 ID 找回可能已接收的提交。单进程同一时刻只接一个任务，任务结果可读 30 分钟或直到进程重启。用户可以主动导出 Markdown。关闭页面只停止观察。
- 预约、下单和支付由后续人工审批阶段设计。周边候选发现、票务与酒店查询、完整费用，以及按对话连续编辑仍待开发。

## 本机接入与上线门禁

Next.js 服务端通过固定回环地址和路由白名单访问 FastAPI，启动脚本生成一次服务间凭据。浏览器使用同源 HttpOnly 会话，任务读取检查会话归属。访问中的任务在内存中暂存，已有最小审计保存在忽略目录 `runtime/web-audit.sqlite3`。浏览器会话存储只记录任务 ID。生产需要正式身份与逐资源授权、持久任务和取消、跨进程幂等与配额、数据删除、HTTPS、故障恢复和真实预发布验收。

前端采用 Next.js App Router、React、TypeScript、npm 锁文件与原生 CSS。Biome 和 Prettier 检查代码，Vitest 检查组件和同源代理，Playwright 在 CI 检查桌面与手机视口。浏览器测试只注入合成任务，真实联调结果另行记录。[Next.js 安装文档](https://nextjs.org/docs/app/getting-started/installation)

```powershell
npm --prefix web run lint
npm --prefix web run format:check
npm --prefix web run check
npm --prefix web test
npm --prefix web run build
uv run --no-sync python scripts/check.py
```
