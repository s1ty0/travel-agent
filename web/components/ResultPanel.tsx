import { useState } from "react";
import { Icon } from "./Icon";
import { JourneyArt } from "./JourneyArt";
import { DailyItinerary } from "./DailyItinerary";
import {
  errorText,
  stageText,
  checkDescription,
  safeLink,
  type Task,
} from "../lib/contracts";

function keyed<T>(items: T[]) {
  const occurrences = new Map<string, number>();
  return items.map((value) => {
    const identity = JSON.stringify(value);
    const count = occurrences.get(identity) ?? 0;
    occurrences.set(identity, count + 1);
    return { key: `${identity}:${count}`, value };
  });
}

export function ResultPanel({ task }: { task: Task | null }) {
  const city = task?.output?.city;
  const [tab, setTab] = useState("itinerary");
  const result = task?.output?.result;
  const plan = result?.plan;
  const links =
    result?.sources.flatMap((batch) =>
      batch.hits.map((hit) => ({
        ...hit,
        provider: batch.provider,
        retrieved_at: batch.retrieved_at,
      })),
    ) ?? [];
  const sources = links.filter(
    (item, index) =>
      links.findIndex((other) => other.source_id === item.source_id) === index,
  );
  const failures = plan?.checks.filter((item) => item.status === "fail") ?? [];
  const unknowns =
    plan?.checks.filter((item) => item.status === "unknown") ?? [];
  function download() {
    if (!task?.output) return;
    const url = URL.createObjectURL(
      new Blob([task.output.markdown], { type: "text/markdown;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = result?.trip_sheet
      ? "旅行行程单-待核实.md"
      : "旅行规划草案.md";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <section className="workspace" aria-label="规划结果">
      <div className="workspace-heading">
        <div>
          <span className="eyebrow">YOUR NEXT JOURNEY</span>
          <h2>
            {result
              ? `${city || "你的旅行"} · ${result.trip_sheet ? "旅行行程单" : "行程草案"}`
              : "你的下一站，听你的。"}
          </h2>
        </div>
        <span
          className={`status-pill ${result?.status === "draft" ? "green" : ""}`}
        >
          <span className="dot" />
          {task?.state === "running"
            ? "正在规划"
            : result?.status === "draft"
              ? "草案 · 待核实"
              : result
                ? "未完成 · 请查看缺口"
                : "等待你的灵感"}
        </span>
      </div>
      <div className="tabbar" role="tablist" aria-label="结果视图">
        {[
          ["itinerary", "行程安排", "compass"],
          ["sources", "来源与依据", "book"],
          ["checks", "检查与缺口", "shield"],
        ].map(([id, title, icon]) => (
          <button
            type="button"
            key={id}
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={tab === id ? "active" : ""}
          >
            <Icon name={icon} size={17} />
            {title}
            {id === "sources" && sources.length > 0 && (
              <span className="count">{sources.length}</span>
            )}
          </button>
        ))}
      </div>
      {task?.state === "running" ? (
        <div className="empty-panel" role="status">
          <div className="loading-orbit">
            <Icon name="compass" size={35} />
          </div>
          <h3>正在把想法，串成旅程</h3>
          <p>
            {stageText(task.stage)}，完成后会展示已有安排与检查结果。
            <br />
            页面刷新后可继续查看；没有自动重试。
          </p>
          <span className="small-note">离开页面不会停止已经发出的调用。</span>
        </div>
      ) : !result ? (
        <div className="empty-panel">
          <JourneyArt />
          <span className="art-label">少一点攻略切换，多一点出发的期待。</span>
          <h3>
            {task?.state === "cancelled"
              ? "本次规划已取消"
              : task?.state === "interrupted"
                ? "规划进程已中断"
                : task?.state === "failed"
                  ? "这次没能完成规划"
                  : "把期待告诉我们，剩下的一起安排。"}
          </h3>
          <p>
            {task?.state === "cancelled"
              ? errorText.cancelled
              : task?.error
                ? (errorText[task.error] ?? "服务暂不可用，请稍后检查。")
                : "发送你的旅行想法，即可先看初稿，再补充条件完善安排。"}
          </p>
          <div className="promise-row">
            <span>
              <Icon name="book" size={16} />
              建议附带来源
            </span>
            <span>
              <Icon name="shield" size={16} />
              未知明确标注
            </span>
            <span>
              <Icon name="check" size={16} />
              预订由你决定
            </span>
          </div>
        </div>
      ) : (
        <div className="result-body">
          {result.is_demo && (
            <div className="notice warning" role="note">
              <Icon name="info" />
              <div>
                <strong>合成演示数据 · 未调用真实服务</strong>
                <p>
                  地点、路线和资料均为模拟，仅展示规划与修改流程，不可据此旅行。
                </p>
              </div>
            </div>
          )}
          {task?.output?.change_summary && (
            <div className="notice">
              <Icon name="info" />
              <div>
                <strong>本版修改 · v{task.version ?? "更新"}</strong>
                <p>
                  新增{task.output.change_summary.added}项，移除
                  {task.output.change_summary.removed}项， 调整时段
                  {task.output.change_summary.time_changed}
                  项。旧版保留在历史版本中。
                </p>
              </div>
            </div>
          )}
          {failures.length > 0 && (
            <div className="notice warning" role="alert">
              <Icon name="info" />
              <div>
                <strong>需要处理的具体问题</strong>
                {keyed(failures).map(({ value, key }) => (
                  <p key={key}>{checkDescription(value)}</p>
                ))}
              </div>
            </div>
          )}
          {plan?.draft_note && (
            <div className="notice">
              <Icon name="info" />
              <div>
                <strong>根据已有地点起草的轻量初稿</strong>
                <p>{plan.draft_note}</p>
              </div>
            </div>
          )}
          {!!task?.output?.assumptions?.length && (
            <div className="notice">
              <Icon name="info" />
              <div>
                <strong>初稿采用的条件</strong>
                {task.output.assumptions.map((text) => (
                  <p key={text}>{text}</p>
                ))}
              </div>
            </div>
          )}
          <div
            className={`notice ${result.status !== "draft" ? "warning" : ""}`}
          >
            <Icon name="info" />
            <div>
              <strong>
                {result.status === "draft"
                  ? result.trip_sheet
                    ? "行程单已生成，请核对出发前清单"
                    : "已生成草案，出发前还需要核实"
                  : "这份安排未通过，暂不能按此执行"}
              </strong>
              <p>
                {result.issue
                  ? (errorText[result.issue] ?? `本次停止：${result.issue}`)
                  : "营业、预约、票务和旅行总费用尚未确认。没有执行预订或付款。"}
              </p>
            </div>
          </div>
          {tab === "itinerary" && (
            <>
              {result.trip_sheet && (
                <div className="notice">
                  <div>
                    <strong>出行信息</strong>
                    <p>
                      出发地 {result.trip_sheet.origin ?? "待确认"} ·{" "}
                      {result.trip_sheet.adults
                        ? `${result.trip_sheet.adults}人`
                        : "人数待确认"}{" "}
                      · 预算 {result.trip_sheet.budget ?? "待确认"}
                    </p>
                  </div>
                </div>
              )}
              {plan ? (
                <DailyItinerary plan={plan} dates={result.trip_sheet?.dates} />
              ) : (
                <p className="muted">本次尚未形成活动安排，请查看具体缺口。</p>
              )}
              <div className="budget-box">
                <div>
                  <span>旅行总费用</span>
                  <strong>尚不能确认</strong>
                </div>
                <p>
                  报价与必要费用仍有缺失。未知金额不会按 ¥0
                  计算，也不代表已满足预算。
                </p>
              </div>
              {result.trip_sheet && (
                <div className="notice">
                  <div>
                    <strong>出发前清单</strong>
                    <ul>
                      {result.trip_sheet.todo.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}
              {result.sources.some(
                (source) => source.provider === "openstreetmap",
              ) && (
                <p className="section-note">
                  地图数据 ©{" "}
                  <a
                    href="https://www.openstreetmap.org/copyright"
                    target="_blank"
                    rel="noreferrer"
                  >
                    OpenStreetMap contributors
                  </a>
                  ，ODbL。
                </p>
              )}
            </>
          )}
          {tab === "sources" && (
            <div className="source-list">
              <p className="section-note">
                搜索摘要和地图记录是依据线索，不代表独立事实核验。相关来源可能共用同一内容。
              </p>
              {sources.map((item) => (
                <article key={item.source_id} className="source-card">
                  <div className="source-kind">
                    <Icon name="book" size={16} />
                    {item.evidence_kind === "search_snippet"
                      ? "搜索摘要"
                      : item.evidence_kind === "provider_place_record"
                        ? "地图地点"
                        : "路线估算"}
                    <span>未独立核实</span>
                  </div>
                  {item.provider === "openstreetmap" &&
                  /^(node|way)\/[0-9]+$/.test(item.poi_id ?? "") ? (
                    <a
                      href={`https://www.openstreetmap.org/${item.poi_id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {item.name}
                    </a>
                  ) : !result.is_demo && safeLink(item.url) ? (
                    <a
                      href={safeLink(item.url)}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {item.title || item.name || "来源记录"}
                      <Icon name="external" size={14} />
                    </a>
                  ) : (
                    <strong>{item.name || item.title || "地图路线估算"}</strong>
                  )}
                  {item.snippet && <p>{item.snippet}</p>}
                  <small>
                    {result.is_demo
                      ? "合成提供者"
                      : item.provider === "openstreetmap"
                        ? "OpenStreetMap"
                        : item.provider === "amap"
                          ? "高德"
                          : "DeepSeek 搜索"}
                    {item.retrieved_at &&
                      ` · 获取于 ${new Date(item.retrieved_at).toLocaleString("zh-CN")}`}
                  </small>
                </article>
              ))}
            </div>
          )}
          {tab === "checks" && (
            <div className="check-list">
              {!plan && (
                <p className="section-note">
                  本次尚未形成待校验的行程。
                  {errorText[result.issue ?? ""] ??
                    "请补充城市、日期等条件后再生成。"}
                </p>
              )}
              <div className="check-summary">
                <strong>{failures.length}</strong> 项确定冲突 <b>·</b>
                <strong>{unknowns.length}</strong> 项待核实
              </div>
              <p className="section-note">
                通过表示对应程序规则满足，不代表营业、票务或来源事实已经认证。
              </p>
              {keyed(plan?.checks ?? []).map(({ value: item, key }) => (
                <div className="check-item" key={key}>
                  <span>{checkDescription(item)}</span>
                  <span className={`check-state ${item.status}`}>
                    {item.status === "pass"
                      ? "规则满足"
                      : item.status === "fail"
                        ? "存在冲突"
                        : "待核实"}
                  </span>
                </div>
              ))}
              {!!plan?.missing_fields.length && (
                <p className="section-note">
                  还有条件未确认。缺少日期、人数口径或具体费用时，结果继续保留为草案。
                </p>
              )}
            </div>
          )}
          <div className="result-footer">
            <div>
              <span>
                {result.is_demo
                  ? "演示费用 ¥0 · 无真实服务调用"
                  : result.service_cost.status === "uncertain"
                    ? "服务费用待核实"
                    : `服务费用估算 ¥${(result.service_cost.estimated_upper / 1000000).toFixed(6)}`}
              </span>
              {result.service_cost.reserved_unsettled > 0 && (
                <small>
                  尚未结算预留 ¥
                  {(result.service_cost.reserved_unsettled / 1000000).toFixed(
                    6,
                  )}
                </small>
              )}
              <small>
                本次上限 ¥{(result.service_cost.cap / 1000000).toFixed(2)} ·
                {result.is_demo ? "无实际供应商账单" : "实际账单未对账"}
              </small>
            </div>
            <button
              type="button"
              className="button secondary"
              onClick={download}
            >
              <Icon name="download" size={17} />
              导出 Markdown
            </button>
          </div>
          {!task?.output?.audit_saved && (
            <p className="section-note">
              最小运行审计未保存，当前结果仍可查看和导出。
            </p>
          )}
        </div>
      )}
      <div className="workspace-bottom">
        <Icon name="shield" size={14} />
        <span>每条建议都应该有依据，每个未知都值得被看见。</span>
      </div>
    </section>
  );
}
