import {
  type Activity,
  type Result,
  type DaySchedule,
  minute,
} from "../lib/contracts";
import { Icon } from "./Icon";

function Visit({ activity }: { activity: Activity }) {
  return (
    <div className="activity-card">
      <div className="activity-label">
        <span>
          {activity.kind === "meal"
            ? "餐饮"
            : activity.kind === "rest"
              ? "休息"
              : "游览"}
        </span>
        <span>待核实</span>
      </div>
      <h4>{activity.place_name}</h4>
      <p>
        <Icon name="pin" size={14} />
        {activity.address || "地址待核实"}
      </p>
      {activity.citations?.slice(0, 1).map((citation) => (
        <blockquote key={citation.source_id}>
          “{citation.quote}”
          <small>来源片段 · 仅证明文字可定位，事实仍待核实</small>
        </blockquote>
      ))}
    </div>
  );
}

export function DailyItinerary({
  plan,
  dates,
}: {
  plan: NonNullable<Result["plan"]>;
  dates?: Record<string, string>;
}) {
  const frames: DaySchedule[] =
    plan.day_schedule ??
    [...new Set(plan.activities.map((item) => item.day))].map((day) => ({
      day,
      missing_day: false,
      missing_meals: [],
      has_open_time: false,
      blocks: plan.activities.flatMap((activity, activity_index) =>
        activity.day === day
          ? [
              {
                kind: "activity",
                activity_index,
                start_minute: activity.start_minute,
                end_minute: activity.end_minute,
              },
            ]
          : [],
      ),
    }));
  if (!frames.length)
    return (
      <p className="muted">
        这次还没有形成活动安排。可先查看来源，再补充条件生成。
      </p>
    );
  return (
    <>
      {plan.day_window && (
        <p className="section-note">
          默认展示范围 {minute(plan.day_window.start_minute)}–
          {minute(plan.day_window.end_minute)}{" "}
          ，单日调整以各天标注为准。餐饮与留白为建议时段，营业和费用仍待核实。
        </p>
      )}
      {frames.map((frame) => (
        <div key={frame.day} className="day-block">
          <div className="day-title">
            <span>DAY {String(frame.day).padStart(2, "0")}</span>
            <h3>
              第 {frame.day} 天{" "}
              {dates?.[String(frame.day)] && (
                <small>{dates[String(frame.day)]} · </small>
              )}
              {plan.day_windows?.[String(frame.day)] && (
                <small>
                  {minute(plan.day_windows[String(frame.day)].start_minute)}–
                  {minute(plan.day_windows[String(frame.day)].end_minute)}{" "}
                  ·{" "}
                </small>
              )}
              <small>
                {frame.missing_day
                  ? "缺少本日安排"
                  : frame.has_open_time
                    ? "含待补充时段"
                    : "建议安排"}
              </small>
            </h3>
          </div>
          {!!frame.missing_meals.length && (
            <p className="section-note">
              {frame.missing_meals.join("、")}尚未留足时间，需要调整活动。
            </p>
          )}
          <div className="timeline">
            {frame.blocks.map((block) => (
              <article
                key={`${block.kind}-${block.start_minute}-${block.end_minute}-${block.activity_index ?? "gap"}`}
                className="activity"
              >
                <div className="time">
                  <strong>{minute(block.start_minute)}</strong>
                  <span>{minute(block.end_minute)}</span>
                </div>
                <div className="activity-line">
                  <i />
                </div>
                {block.kind === "activity" &&
                block.activity_index !== undefined &&
                plan.activities[block.activity_index] ? (
                  <Visit activity={plan.activities[block.activity_index]} />
                ) : (
                  <div
                    className={`activity-card schedule-suggestion ${block.kind === "unplanned_day" ? "missing-day" : ""}`}
                  >
                    <div className="activity-label">
                      <span>
                        {block.kind === "unplanned_day"
                          ? "尚未安排"
                          : "建议时段"}
                      </span>
                      <span>具体内容待确认</span>
                    </div>
                    <h4>{block.title}</h4>
                    <p>{block.note}</p>
                  </div>
                )}
              </article>
            ))}
          </div>
        </div>
      ))}
    </>
  );
}
