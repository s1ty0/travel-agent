# ADR-0003：响应式网站优先，首期仅 DeepSeek

日期：2026-11-08。状态：采用；后续客户端按实际用户数据立项。

## 输入与决定

用户明确初始阶段只提供 DeepSeek，要求补充网站/页面/App 取舍及技术栈，并指示继续开发。

- 首个正式产品是响应式 Web 应用，电脑与手机浏览器共用网址、API 和行程数据。一个展示页只用于原型，不是最终产品。
- M4 前端采用 TypeScript/React/Next.js 方向，与已实现的 Python/FastAPI 后端连接。领域规则、密钥、任务和审批留在服务端。
- PWA 在核心 Web 流程稳定后按弱网需求增加。小程序及 Android/iOS App 放后续，需要平台能力或留存数据证明价值，再分别设计和测试。
- 规划服务和未来客户端之间通过版本化 API/OpenAPI 契约连接；可复用服务，不承诺全部 UI 代码复用。
- M2 首期只写 DeepSeek 适配，不为早期开发引入其他模型账号。先候选字段提取，再搜索/工具/地图和 Agent 基线。
- 真实模型调用采用显式 CLI，一次请求、最多 512 输出 tokens 起步，不重试、不开放付费 HTTP 路由。单次上限不等同于整次规划费用预算。
- `.env` 只用于本地配置，默认测试模拟传输。真实模型连接成功不证明旅游事实正确或已获得搜索/地图权限。

## 验证和代价

Web 需要独立验证窄屏、触屏、弱网、刷新恢复和浏览器差异；PWA 缓存不用于批准支付或承诺实时库存。正式对外服务前仍需用户身份、资源授权、限流和预算。

候选提取通过原文片段、本地数据结构和预算解析校验。原文匹配只能限制凭空改写，不能保证角色理解完全正确，因此结果强制保持草案和“提取字段待确认”。

参考：[Next.js](https://nextjs.org/docs/app)、[PWA](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps)、[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。具体模型及接口兼容以实际调用为准；前端版本在 M4 初始化时锁定。
