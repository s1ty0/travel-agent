"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { ResultPanel } from "./ResultPanel";
import { TaskHistory } from "./TaskHistory";
import { errorText, stageText, type Task } from "../lib/contracts";

const suggestions = [
  {
    city: "南京",
    days: 1,
    budget: "2000以内",
    text: "想去南京玩一天，预算2000元以内。喜欢博物馆，安排轻松一点。",
    label: "南京 · 博物馆慢游",
    icon: "book",
  },
  {
    city: "上海",
    days: 2,
    budget: "2000～3000",
    text: "上海两日游，总预算2000～3000元，想看看城市建筑和展览。",
    label: "上海 · 城市漫步",
    icon: "pin",
  },
  {
    city: "杭州",
    days: 2,
    budget: "3000以内",
    text: "想去杭州待两天，总预算3000元以内，喜欢自然和人文景点。",
    label: "杭州 · 山水之间",
    icon: "compass",
  },
  {
    city: "苏州",
    days: 1,
    budget: "1500以内",
    text: "想去苏州古城玩一天，预算1500以内，喜欢园林，安排午餐和晚餐。",
    label: "苏州 · 古城园林",
    icon: "compass",
  },
];

async function api(path: string, body?: unknown, deepSeekKey?: string) {
  const response = await fetch(`/api/${path}`, {
    method: body ? "POST" : "GET",
    cache: "no-store",
    ...(body
      ? {
          headers: {
            "Content-Type": "application/json",
            ...(deepSeekKey ? { "X-Travel-DeepSeek-Key": deepSeekKey } : {}),
          },
          body: JSON.stringify(body),
        }
      : {}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error ?? "backend_unavailable");
  return data;
}

export default function Planner() {
  const [message, setMessage] = useState("");
  const [asked, setAsked] = useState(false);
  const [city, setCity] = useState("");
  const [budget, setBudget] = useState("");
  const [days, setDays] = useState(1);
  const [adults, setAdults] = useState<number | "">("");
  const [startDate, setStartDate] = useState("");
  const [dayStart, setDayStart] = useState("09:00");
  const [dayEnd, setDayEnd] = useState("20:00");
  const [origin, setOrigin] = useState("");
  const [sights, setSights] = useState<number | "auto">("auto");
  const [walkMinutes, setWalkMinutes] = useState<number | "">("");
  const [allowRepair, setAllowRepair] = useState(true);
  const [researchMode, setResearchMode] = useState("single_agent");
  const [enabled, setEnabled] = useState(false);
  const [demo, setDemo] = useState(false);
  const [supportedCities, setSupportedCities] = useState<string[] | null>(null);
  const [trialPolicy, setTrialPolicy] = useState(false);
  const [deepSeekKey, setDeepSeekKey] = useState("");
  const [hasUserKey, setHasUserKey] = useState(false);
  const [showKeyForm, setShowKeyForm] = useState(false);
  const [savingKey, setSavingKey] = useState(false);
  const [legacy, setLegacy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [durableStorage, setDurableStorage] = useState(false);
  const [historyRefresh, setHistoryRefresh] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [task, setTask] = useState<Task | null>(null);
  const [previousTask, setPreviousTask] = useState<Task | null>(null);
  const [showPrevious, setShowPrevious] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [pollingPaused, setPollingPaused] = useState(false);
  const requestId = useRef<string | null>(null);
  const busyRef = useRef(false);

  const acceptConditions = useCallback((value: Task) => {
    const planning = value.output?.planning;
    if (!planning) return;
    const mode = value.output?.result.research_mode;
    if (
      mode &&
      ["single_agent", "parallel_tools", "multi_agent"].includes(mode)
    )
      setResearchMode(mode);
    setCity(planning.city === "待选择目的城市" ? "" : planning.city);
    setBudget(planning.trip.budget ?? "");
    setDays(planning.trip.days ?? 1);
    setSights(planning.constraints?.max_sightseeing_places ?? "auto");
    setWalkMinutes(planning.constraints?.max_walk_minutes ?? "");
    setAdults(planning.trip.adults ?? "");
    setOrigin(planning.trip.origin ?? "");
    setStartDate(planning.trip.start_date ?? "");
    if (planning.day_window) {
      const time = (value: number) =>
        `${Math.floor((value % 1440) / 60)
          .toString()
          .padStart(2, "0")}:${(value % 60).toString().padStart(2, "0")}`;
      setDayStart(time(planning.day_window.start_minute));
      setDayEnd(time(planning.day_window.end_minute));
    }
    setMessage("");
    setAsked(true);
  }, []);

  useEffect(() => {
    api("config")
      .then((data) => {
        setEnabled(data.planning_enabled ?? data.live_enabled);
        setDemo(Boolean(data.demo_mode));
        setSupportedCities(
          Array.isArray(data.supported_cities) ? data.supported_cities : null,
        );
        setTrialPolicy(Boolean(data.trial_policy));
        setHasUserKey(Boolean(data.has_user_key));
        setShowKeyForm(
          Boolean(
            data.trial_policy &&
            !data.has_user_key &&
            !sessionStorage.getItem("travel_key_prompt_seen"),
          ),
        );
        setLegacy(data.engine === "baseline");
        setConnected(true);
        setDurableStorage(data.storage === "private_session_7_days");
        const previous = sessionStorage.getItem("travel_task");
        if (previous) {
          busyRef.current = true;
          setTaskId(previous);
          setBusy(true);
        }
      })
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!taskId || pollingPaused) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next: Task = await api(`tasks/${taskId}`);
        if (!active) return;
        if (next.output && Boolean(next.output.result.is_demo) !== demo) {
          setTaskId(null);
          setBusy(false);
          busyRef.current = false;
          sessionStorage.removeItem("travel_task");
          return;
        }
        setTask(next);
        if (next.state === "running") {
          timer = setTimeout(poll, 1200);
        } else {
          acceptConditions(next);
          setBusy(false);
          busyRef.current = false;
          setHistoryRefresh((value) => value + 1);
        }
      } catch (err) {
        if (!active) return;
        const code = err instanceof Error ? err.message : "backend_unavailable";
        setError(code);
        setPollingPaused(true);
        if (code === "task_not_found") {
          setBusy(false);
          busyRef.current = false;
          sessionStorage.removeItem("travel_task");
        }
      }
    }
    poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [taskId, pollingPaused, acceptConditions, demo]);

  function choose(index: number) {
    const item = suggestions[index];
    setMessage(item.text);
    setCity(item.city);
    setDays(item.days);
    setBudget(item.budget);
    setAsked(true);
    requestId.current = null;
  }
  async function cancelCurrent() {
    if (!taskId) return;
    try {
      setTask(await api(`tasks/${taskId}/cancel`, {}));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "backend_unavailable");
    }
  }
  function openSaved(id: string) {
    if (busyRef.current || id === taskId) return;
    setPreviousTask(null);
    setShowPrevious(false);
    setPollingPaused(false);
    setError("");
    setTaskId(id);
    sessionStorage.setItem("travel_task", id);
    setBusy(true);
    busyRef.current = true;
  }
  function reset() {
    if (busy) return;
    setMessage("");
    setDeepSeekKey("");
    setAsked(false);
    setCity("");
    setBudget("");
    setTask(null);
    setPreviousTask(null);
    setShowPrevious(false);
    setTaskId(null);
    setError("");
    setDays(1);
    setAdults("");
    setOrigin("");
    setStartDate("");
    setDayStart("09:00");
    setDayEnd("20:00");
    setSights("auto");
    setWalkMinutes("");
    setAllowRepair(true);
    setResearchMode("single_agent");
    requestId.current = null;
    sessionStorage.removeItem("travel_task");
  }
  async function updateKey(remove = false) {
    setSavingKey(true);
    setError("");
    try {
      if (remove) {
        const response = await fetch("/api/credentials", {
          method: "DELETE",
          cache: "no-store",
        });
        if (!response.ok) throw new Error("trial_not_configured");
      } else {
        await api("credentials", {}, deepSeekKey.trim());
      }
      setHasUserKey(!remove);
      setDeepSeekKey("");
      setShowKeyForm(false);
      sessionStorage.setItem("travel_key_prompt_seen", "1");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "backend_unavailable");
    } finally {
      setSavingKey(false);
    }
  }
  async function generate(
    event: React.FormEvent<HTMLFormElement>,
    ideaOnly = false,
  ) {
    event.preventDefault();
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    setPollingPaused(false);
    const id = requestId.current ?? crypto.randomUUID();
    requestId.current = id;
    const baseTask = task?.output?.planning ? task : previousTask;
    const payload = {
      request_id: id,
      ...(baseTask?.output?.planning ? { base_task_id: baseTask.task_id } : {}),
      max_replans: allowRepair ? 1 : 0,
      research_mode: researchMode,
      ...(ideaOnly
        ? { idea: message.trim() }
        : {
            planning: {
              city: city.trim(),
              day_window: {
                start_minute:
                  Number(dayStart.slice(0, 2)) * 60 + Number(dayStart.slice(3)),
                end_minute:
                  Number(dayEnd.slice(0, 2)) * 60 + Number(dayEnd.slice(3)) ||
                  1440,
              },
              day_overrides: baseTask?.output?.planning?.day_overrides ?? [],
              trip: {
                ...(baseTask?.output?.planning?.trip ?? {}),
                raw_text: [
                  baseTask?.output?.planning?.trip.raw_text ?? "",
                  message.trim(),
                ]
                  .filter(Boolean)
                  .join("\n")
                  .slice(-4000),
                destination: city.trim(),
                budget: budget.trim() || null,
                days,
                adults: adults === "" ? null : adults,
                start_date: startDate || null,
                origin: origin.trim() || null,
                ...(budget.trim() !== baseTask?.output?.planning?.trip.budget
                  ? { budget_scope: null, cap_confirmed: false }
                  : {}),
              },
              constraints: {
                ...(baseTask?.output?.planning?.constraints ?? {}),
                max_sightseeing_places: sights === "auto" ? null : sights,
                max_walk_minutes: walkMinutes === "" ? null : walkMinutes,
              },
            },
          }),
    };
    try {
      const created: Task = await api("tasks", payload);
      if (task?.output?.result.plan) setPreviousTask(task);
      setShowPrevious(false);
      setTask(created);
      setAsked(true);
      setTaskId(created.task_id);
      setPollingPaused(false);
      setError("");
      sessionStorage.setItem("travel_task", created.task_id);
      requestId.current = null;
      if (created.state !== "running") {
        acceptConditions(created);
        setBusy(false);
        busyRef.current = false;
      }
    } catch (err) {
      const code = err instanceof Error ? err.message : "backend_unavailable";
      setError(code);
      if (code === "own_deepseek_key_required" || code === "task_key_expired")
        setShowKeyForm(true);
      setBusy(false);
      busyRef.current = false;
      // A transport failure may hide an accepted task; retain this id for a safe retry.
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a href="/" className="brand" aria-label="行迹首页">
          <span className="brand-mark">
            <Icon name="compass" size={28} />
          </span>
          <strong>
            行迹<span>TRAIL</span>
          </strong>
        </a>
        <span className="topbar-divider" />
        <span className="product-label">旅行规划工作台</span>
        <div className="topbar-right">
          <span className="preview-label">
            {connected && enabled
              ? demo
                ? "合成演示"
                : "真实规划"
              : "开发预览"}
          </span>
          <span className={`connection ${connected ? "online" : ""}`}>
            <span className="dot" />
            {connected ? "服务已连接" : "服务连接中"}
          </span>
        </div>
      </header>
      <main className="main-shell">
        <div className="intro">
          <div>
            <span className="eyebrow">
              <span className="tiny-line" /> A LITTLE PLAN, A BETTER JOURNEY
            </span>
            <h1>
              把想去的地方，
              <br className="mobile-break" />
              变成<span>期待的旅程。</span>
            </h1>
            <p>说说你的想法。让行程有安排，让每一步有依据。</p>
          </div>
          <div className="intro-note">
            <Icon name="shield" size={21} />
            <span>
              有据可查
              <br />
              <strong>放心做决定</strong>
            </span>
          </div>
        </div>
        <div className="planner-grid">
          <aside className="conversation">
            <div className="conversation-heading">
              <div className="section-title">
                <Icon name="chat" size={19} />
                <h2>聊聊你的旅行</h2>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label="新建旅行"
                disabled={busy}
                onClick={reset}
              >
                <Icon name="plus" size={21} />
              </button>
            </div>
            <div className="assistant-greeting">
              <div className="assistant-avatar">
                <Icon name="spark" size={18} />
              </div>
              <div>
                <strong>想去哪儿走走？</strong>
                <p>
                  目的地、预算、喜欢的体验……
                  <br />
                  从一个想法开始就好。
                </p>
              </div>
            </div>
            {trialPolicy && (
              <div className="key-panel">
                {showKeyForm ? (
                  <>
                    <label htmlFor="deepseek-session-key">
                      DeepSeek API Key（可选）
                    </label>
                    <input
                      id="deepseek-session-key"
                      type="password"
                      autoComplete="off"
                      maxLength={163}
                      value={deepSeekKey}
                      disabled={busy || savingKey}
                      onChange={(event) => setDeepSeekKey(event.target.value)}
                    />
                    <p className="small-note">
                      保存后本会话优先使用你的
                      Key。服务端加密保存，最长7天，可随时删除。跳过可试用最多3次，受网络和全站额度限制。
                    </p>
                    <div className="history-actions">
                      <button
                        type="button"
                        className="button secondary"
                        disabled={busy || savingKey || !deepSeekKey.trim()}
                        onClick={() => updateKey()}
                      >
                        保存 Key
                      </button>
                      <button
                        type="button"
                        className="button secondary"
                        disabled={savingKey}
                        onClick={() => {
                          setDeepSeekKey("");
                          setShowKeyForm(false);
                          sessionStorage.setItem("travel_key_prompt_seen", "1");
                        }}
                      >
                        {hasUserKey ? "取消" : "先试用"}
                      </button>
                    </div>
                  </>
                ) : (
                  <div className="history-actions">
                    <button
                      type="button"
                      className="button secondary"
                      disabled={busy || savingKey}
                      onClick={() => setShowKeyForm(true)}
                    >
                      {hasUserKey
                        ? "更换 DeepSeek Key"
                        : "填写自己的 DeepSeek Key"}
                    </button>
                    {hasUserKey && (
                      <button
                        type="button"
                        className="button secondary"
                        disabled={busy || savingKey}
                        onClick={() => updateKey(true)}
                      >
                        删除 Key
                      </button>
                    )}
                  </div>
                )}
              </div>
            )}
            <form
              className="message-form"
              onSubmit={(event) => generate(event, true)}
            >
              <label htmlFor="message" className="sr-only">
                描述你的旅行
              </label>
              <textarea
                id="message"
                name="message"
                required
                maxLength={2000}
                disabled={busy}
                placeholder="比如：想去南京玩一天，预算2000元以内，喜欢博物馆，别安排太赶。"
                value={message}
                onChange={(event) => {
                  setMessage(event.target.value);
                  requestId.current = null;
                }}
              />
              <div className="composer-bottom">
                <span>{message.length}/2000</span>
                <button
                  className="send-button"
                  type="submit"
                  disabled={busy || !enabled || !message.trim()}
                  aria-label="发送旅行想法"
                >
                  <Icon name="arrow" size={20} />
                </button>
              </div>
            </form>
            {!asked && (
              <div className="inspiration">
                <span>选一个想法，再发送即可生成初稿</span>
                {suggestions.map(
                  (item, index) =>
                    (!supportedCities ||
                      supportedCities.includes(item.city)) && (
                      <button
                        type="button"
                        key={item.city}
                        onClick={() => choose(index)}
                        disabled={busy}
                      >
                        <Icon name={item.icon} size={17} />
                        {item.label}
                        <Icon name="arrow" size={15} />
                      </button>
                    ),
                )}
              </div>
            )}
            {asked && (
              <div className="conditions">
                <div className="confirmation-title">
                  <span className="mini-check">✓</span>
                  <strong>补充条件，继续完善初稿</strong>
                </div>
                <p className="form-note">
                  这组条件决定规划范围。其他想法会送给模型，但尚不能保证逐项验证。
                </p>
                <form
                  onSubmit={generate}
                  onChange={() => {
                    requestId.current = null;
                  }}
                >
                  <div className="field-row">
                    <label>
                      目的城市
                      <input
                        name="city"
                        required
                        maxLength={40}
                        placeholder="如：南京"
                        value={city}
                        disabled={busy}
                        onChange={(event) => setCity(event.target.value)}
                      />
                    </label>
                    <label>
                      行程天数
                      <select
                        name="days"
                        value={days}
                        disabled={busy}
                        onChange={(event) =>
                          setDays(Number(event.target.value))
                        }
                      >
                        {[1, 2, 3].map((n) => (
                          <option key={n} value={n}>
                            {n} 天
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <label>
                    旅行总预算
                    <input
                      name="budget"
                      maxLength={80}
                      placeholder="如：2000以内 / 2000～3000"
                      value={budget}
                      disabled={busy}
                      onChange={(event) => setBudget(event.target.value)}
                    />
                  </label>
                  <div className="field-row">
                    <label>
                      出发日期 <small>可留空</small>
                      <input
                        name="date"
                        type="date"
                        value={startDate}
                        disabled={busy}
                        onChange={(event) => setStartDate(event.target.value)}
                      />
                    </label>
                    <label>
                      成人人数 <small>可留空</small>
                      <input
                        name="adults"
                        type="number"
                        min={1}
                        max={20}
                        value={adults}
                        disabled={busy}
                        onChange={(event) =>
                          setAdults(
                            event.target.value
                              ? Number(event.target.value)
                              : "",
                          )
                        }
                      />
                    </label>
                  </div>
                  <details>
                    <summary>
                      更多条件 <span>出发地、景点上限与步行</span>
                    </summary>
                    <div className="field-row">
                      <label>
                        每天开始时间
                        <input
                          type="time"
                          required
                          value={dayStart}
                          disabled={busy}
                          onChange={(event) => setDayStart(event.target.value)}
                        />
                      </label>
                      <label>
                        每天结束时间
                        <input
                          type="time"
                          required
                          value={dayEnd}
                          disabled={busy}
                          onChange={(event) => setDayEnd(event.target.value)}
                        />
                      </label>
                    </div>
                    <label>
                      研究方式
                      <select
                        value={researchMode}
                        disabled={busy}
                        onChange={(event) =>
                          setResearchMode(event.target.value)
                        }
                      >
                        <option value="single_agent">标准研究</option>
                        <option value="parallel_tools">并行查询对照</option>
                        <option value="multi_agent">专精并行（实验）</option>
                      </select>
                    </label>
                    <label>
                      每日步行上限（分钟） <small>留空暂按90分钟</small>
                      <input
                        type="number"
                        min="0"
                        max="480"
                        value={walkMinutes}
                        disabled={busy}
                        onChange={(event) =>
                          setWalkMinutes(
                            event.target.value === ""
                              ? ""
                              : Number(event.target.value),
                          )
                        }
                      />
                    </label>
                    <label>
                      出发地 <small>可留空</small>
                      <input
                        name="origin"
                        maxLength={40}
                        placeholder="如：上海"
                        value={origin}
                        disabled={busy}
                        onChange={(event) => setOrigin(event.target.value)}
                      />
                    </label>
                    <label>
                      全程最多几处景点
                      <select
                        name="sights"
                        value={sights}
                        disabled={busy}
                        onChange={(event) =>
                          setSights(
                            event.target.value === "auto"
                              ? "auto"
                              : Number(event.target.value),
                          )
                        }
                      >
                        <option value="auto">按天数与偏好安排</option>
                        {Array.from({ length: 9 }, (_, i) => i + 1).map((n) => (
                          <option key={n} value={n}>
                            {n} 处
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="checkbox-label" hidden={!legacy}>
                      <input
                        type="checkbox"
                        checked={allowRepair}
                        disabled={busy}
                        onChange={(event) =>
                          setAllowRepair(event.target.checked)
                        }
                      />
                      允许一次时间 / 数量冲突修正
                    </label>
                  </details>
                  <div className="scope-note">
                    <Icon name="info" size={15} />
                    <span>
                      {supportedCities
                        ? `当前覆盖${supportedCities.join("、")}的部分城区。`
                        : "当前支持已选城市。"}
                      可规划1–3天，市内交通为步行估算。实时票务、住宿库存和总费用待核实。
                    </span>
                  </div>
                  <button
                    type="submit"
                    className="button primary generate-button"
                    disabled={busy || !enabled || !city.trim()}
                  >
                    {busy ? (
                      <>
                        <span className="spinner" />
                        正在规划中
                      </>
                    ) : (
                      <>
                        <Icon name="spark" size={18} />
                        {task ? "按补充条件更新行程" : "按这些条件生成"}
                        <Icon name="arrow" size={18} />
                      </>
                    )}
                  </button>
                  <p className="cost-note">
                    {demo
                      ? "合成演示 · 不调用真实服务"
                      : "真实调用 · 每次服务上限 ¥5"}
                    <br />
                    此为模型与查询成本，与旅行预算分开。
                  </p>
                </form>
              </div>
            )}
            {error && (
              <div className="error-banner" role="alert">
                {errorText[error] ?? "请求未完成，请检查条件或服务连接。"}
                {pollingPaused && error !== "task_not_found" && (
                  <button
                    type="button"
                    onClick={() => {
                      setError("");
                      setPollingPaused(false);
                    }}
                  >
                    继续查看原任务
                  </button>
                )}
              </div>
            )}
            {connected && !enabled && (
              <p className="form-note connection-note">
                页面已连接；真实规划尚未开启。启动服务时加 -Live 后即可生成。
              </p>
            )}
            <div className="conversation-footnote">
              <Icon name="shield" size={15} />
              <span>不自动预约，不代下单，不执行付款。</span>
            </div>
            {durableStorage && (
              <TaskHistory
                demo={demo}
                currentId={taskId}
                refresh={historyRefresh}
                busy={busy}
                onOpen={openSaved}
                onDeleted={reset}
              />
            )}
          </aside>
          <div>
            {busy && taskId && (
              <div className="history-actions">
                <span>{stageText(task?.stage)}</span>
                <button
                  type="button"
                  className="button secondary"
                  onClick={cancelCurrent}
                  disabled={task?.stage === "cancelling"}
                >
                  取消后续规划
                </button>
              </div>
            )}
            {!busy && previousTask?.output && (
              <button
                type="button"
                className="button secondary"
                onClick={() => setShowPrevious((value) => !value)}
              >
                {showPrevious ? "查看本次结果" : "查看上一版行程"}
              </button>
            )}
            {busy && previousTask?.output && (
              <div className="notice">
                <Icon name="clock" />
                <p>正在生成更新版，下方先保留上一版供你查看。</p>
              </div>
            )}
            <ResultPanel
              task={
                (busy || showPrevious) && previousTask?.output
                  ? previousTask
                  : task
              }
            />
          </div>
        </div>
        <footer className="page-footer">
          <span>
            行迹 TRAIL <i>·</i> 让旅途回归期待
          </span>
          <span>私有会话保存7天 · 支持导出与删除</span>
        </footer>
      </main>
    </div>
  );
}
