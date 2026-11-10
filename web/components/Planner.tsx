"use client";

import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { ResultPanel } from "./ResultPanel";
import { errorText, type Task } from "../lib/contracts";

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
];

async function api(path: string, body?: unknown) {
  const response = await fetch(`/api/${path}`, {
    method: body ? "POST" : "GET",
    cache: "no-store",
    ...(body
      ? {
          headers: { "Content-Type": "application/json" },
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
  const [adults, setAdults] = useState(1);
  const [startDate, setStartDate] = useState("");
  const [origin, setOrigin] = useState("");
  const [sights, setSights] = useState(2);
  const [allowRepair, setAllowRepair] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [task, setTask] = useState<Task | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [pollingPaused, setPollingPaused] = useState(false);
  const requestId = useRef<string | null>(null);
  const busyRef = useRef(false);

  useEffect(() => {
    api("config")
      .then((data) => {
        setEnabled(data.live_enabled);
        setConnected(true);
      })
      .catch((err) => setError(err.message));
    const previous = sessionStorage.getItem("travel_task");
    if (previous) {
      busyRef.current = true;
      setTaskId(previous);
      setBusy(true);
    }
  }, []);

  useEffect(() => {
    if (!taskId || pollingPaused) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next: Task = await api(`tasks/${taskId}`);
        if (!active) return;
        setTask(next);
        if (next.state === "running") {
          timer = setTimeout(poll, 1200);
        } else {
          setBusy(false);
          busyRef.current = false;
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
  }, [taskId, pollingPaused]);

  function choose(index: number) {
    const item = suggestions[index];
    setMessage(item.text);
    setCity(item.city);
    setDays(item.days);
    setBudget(item.budget);
    setAsked(true);
    requestId.current = null;
  }
  function reset() {
    if (busy) return;
    setMessage("");
    setAsked(false);
    setCity("");
    setBudget("");
    setTask(null);
    setTaskId(null);
    setError("");
    setDays(1);
    setAdults(1);
    setOrigin("");
    setStartDate("");
    setSights(2);
    setAllowRepair(false);
    requestId.current = null;
    sessionStorage.removeItem("travel_task");
  }
  async function generate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    setPollingPaused(false);
    const id = requestId.current ?? crypto.randomUUID();
    requestId.current = id;
    const payload = {
      request_id: id,
      max_replans: allowRepair ? 1 : 0,
      planning: {
        city: city.trim(),
        trip: {
          raw_text: message.trim(),
          destination: city.trim(),
          budget: budget.trim(),
          days,
          adults,
          budget_scope: "total",
          ...(startDate ? { start_date: startDate } : {}),
          ...(origin.trim() ? { origin: origin.trim() } : {}),
        },
        constraints: { max_sightseeing_places: sights },
      },
    };
    try {
      const created: Task = await api("tasks", payload);
      setTask(created);
      setTaskId(created.task_id);
      sessionStorage.setItem("travel_task", created.task_id);
      requestId.current = null;
      if (created.state !== "running") {
        setBusy(false);
        busyRef.current = false;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "backend_unavailable");
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
          <span className="preview-label">开发预览</span>
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
            <form
              className="message-form"
              onSubmit={(event) => {
                event.preventDefault();
                setAsked(true);
                requestId.current = null;
              }}
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
                  disabled={busy || !message.trim()}
                  aria-label="发送旅行想法"
                >
                  <Icon name="arrow" size={20} />
                </button>
              </div>
            </form>
            {!asked && (
              <div className="inspiration">
                <span>还没想好？试着这样开始</span>
                {suggestions.map((item, index) => (
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
                ))}
              </div>
            )}
            {asked && (
              <div className="conditions">
                <div className="confirmation-title">
                  <span className="mini-check">✓</span>
                  <strong>想法已记录，再确认几个条件</strong>
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
                      required
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
                      成人人数
                      <input
                        name="adults"
                        required
                        type="number"
                        min={1}
                        max={20}
                        value={adults}
                        disabled={busy}
                        onChange={(event) =>
                          setAdults(Number(event.target.value))
                        }
                      />
                    </label>
                  </div>
                  <details>
                    <summary>
                      更多条件 <span>出发地、景点上限与修正</span>
                    </summary>
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
                          setSights(Number(event.target.value))
                        }
                      >
                        {Array.from({ length: 9 }, (_, i) => i + 1).map((n) => (
                          <option key={n} value={n}>
                            {n} 处
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="checkbox-label">
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
                      当前支持已选城市 1–3
                      天草案，市内交通为步行估算。周边筛选、实时票务和完整费用尚未接入。
                    </span>
                  </div>
                  <button
                    type="submit"
                    className="button primary generate-button"
                    disabled={busy || !enabled || !message.trim()}
                  >
                    {busy ? (
                      <>
                        <span className="spinner" />
                        正在规划中
                      </>
                    ) : (
                      <>
                        <Icon name="spark" size={18} />
                        {task ? "按新条件重新生成" : "确认条件并生成"}
                        <Icon name="arrow" size={18} />
                      </>
                    )}
                  </button>
                  <p className="cost-note">
                    真实调用 · 每次服务上限 ¥2
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
          </aside>
          <ResultPanel task={task} />
        </div>
        <footer className="page-footer">
          <span>
            行迹 TRAIL <i>·</i> 让旅途回归期待
          </span>
          <span>当前为开发预览 · 任务结果临时保留 30 分钟，请及时导出。</span>
        </footer>
      </main>
    </div>
  );
}
