import { useState } from "react";
import { Icon } from "./Icon";
import { JourneyArt } from "./JourneyArt";
import {
  errorText,
  minute,
  ruleText,
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
    link.download = "旅行规划草案.md";
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
              ? `${city || "你的旅行"} · 行程草案`
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
            正在查询资料、整理安排并检查约束。
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
            {task?.state === "failed"
              ? "这次没能完成规划"
              : "把期待告诉我们，剩下的一起安排。"}
          </h3>
          <p>
            {task?.error
              ? (errorText[task.error] ?? "服务暂不可用，请稍后检查。")
              : "从左边说说你的想法。确认条件后，行程、依据和待核实事项会一起出现在这里。"}
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
          <div
            className={`notice ${result.status !== "draft" ? "warning" : ""}`}
          >
            <Icon name="info" />
            <div>
              <strong>
                {result.status === "draft"
                  ? "已生成草案，出发前还需要核实"
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
              {plan?.activities.length ? (
                [...new Set(plan.activities.map((item) => item.day))].map(
                  (day) => (
                    <div key={day} className="day-block">
                      <div className="day-title">
                        <span>DAY {String(day).padStart(2, "0")}</span>
                        <h3>
                          第 {day} 天 <small>建议安排</small>
                        </h3>
                      </div>
                      <div className="timeline">
                        {keyed(
                          plan.activities.filter((item) => item.day === day),
                        ).map(({ value: item, key }) => (
                          <article key={key} className="activity">
                            <div className="time">
                              <strong>{minute(item.start_minute)}</strong>
                              <span>{minute(item.end_minute)}</span>
                            </div>
                            <div className="activity-line">
                              <i />
                            </div>
                            <div className="activity-card">
                              <div className="activity-label">
                                <span>
                                  {item.kind === "meal"
                                    ? "餐饮"
                                    : item.kind === "rest"
                                      ? "休息"
                                      : "游览"}
                                </span>
                                <span>待核实</span>
                              </div>
                              <h4>{item.place_name}</h4>
                              <p>
                                <Icon name="pin" size={14} />
                                {item.address || "地址待核实"}
                              </p>
                              {item.citations?.slice(0, 1).map((citation) => (
                                <blockquote key={citation.source_id}>
                                  “{citation.quote}”
                                  <small>
                                    来源片段 · 仅证明文字可定位，事实仍待核实
                                  </small>
                                </blockquote>
                              ))}
                            </div>
                          </article>
                        ))}
                      </div>
                    </div>
                  ),
                )
              ) : (
                <p className="muted">
                  没有可展示的活动，已有失败信息保留在检查面板。
                </p>
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
                  {safeLink(item.url) ? (
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
                    {item.provider === "amap" ? "高德" : "DeepSeek 搜索"}
                    {item.retrieved_at &&
                      ` · 获取于 ${new Date(item.retrieved_at).toLocaleString("zh-CN")}`}
                  </small>
                </article>
              ))}
            </div>
          )}
          {tab === "checks" && (
            <div className="check-list">
              <div className="check-summary">
                <strong>{failures.length}</strong> 项确定冲突 <b>·</b>
                <strong>{unknowns.length}</strong> 项待核实
              </div>
              <p className="section-note">
                通过表示对应程序规则满足，不代表营业、票务或来源事实已经认证。
              </p>
              {keyed(plan?.checks ?? []).map(({ value: item, key }) => (
                <div className="check-item" key={key}>
                  <span>{ruleText[item.rule] || item.rule}</span>
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
                {result.service_cost.status === "uncertain"
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
                实际账单未对账
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
