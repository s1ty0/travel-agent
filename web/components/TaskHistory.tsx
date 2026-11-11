"use client";

import { useCallback, useEffect, useState } from "react";
import { errorText } from "../lib/contracts";

type Entry = {
  task_id: string;
  root_id: string;
  version: number;
  city: string;
  state: string;
};
export function TaskHistory({
  currentId,
  refresh,
  busy,
  onOpen,
  onDeleted,
}: {
  currentId?: string | null;
  refresh: number;
  busy: boolean;
  onOpen: (id: string) => void;
  onDeleted: () => void;
}) {
  const [trips, setTrips] = useState<Entry[]>([]);
  const [versions, setVersions] = useState<Entry[]>([]);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState("");
  const reload = useCallback(async () => {
    try {
      const response = await fetch("/api/tasks", { cache: "no-store" });
      if (!response.ok) throw new Error("backend_unavailable");
      const data = await response.json();
      setTrips(data.tasks ?? []);
      setError("");
    } catch {
      setError("历史记录暂不可用，当前行程仍可查看。");
    }
  }, []);
  useEffect(() => {
    void refresh;
    reload();
  }, [refresh, reload]);
  useEffect(() => {
    setConfirmDelete(false);
    if (!currentId) {
      setVersions([]);
      return;
    }
    let active = true;
    fetch(`/api/tasks/${currentId}/versions`, { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : { tasks: [] }))
      .then((data) => {
        if (active) setVersions(data.tasks ?? []);
      })
      .catch(() => {
        if (active) setVersions([]);
      });
    return () => {
      active = false;
    };
  }, [currentId]);
  async function remove() {
    if (!currentId || busy) return;
    try {
      const response = await fetch(`/api/tasks/${currentId}`, {
        method: "DELETE",
        cache: "no-store",
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error ?? "backend_unavailable");
      onDeleted();
      setConfirmDelete(false);
      await reload();
    } catch (cause) {
      setError(
        errorText[cause instanceof Error ? cause.message : ""] ??
          "删除未完成，请稍后再试。",
      );
    }
  }
  return (
    <section className="task-history" aria-label="私有旅行记录">
      <h3>
        我的旅行 <small>当前私有会话</small>
      </h3>
      <p className="small-note">
        保存7天，可随时删除。清理浏览器会话后将无法找回。
      </p>
      {error && <p role="alert">{error}</p>}
      {!trips.length && (
        <p className="small-note">生成第一份行程后，会保存在这里。</p>
      )}
      <div className="history-actions">
        {trips.map((entry) => (
          <button
            type="button"
            className="button secondary"
            key={entry.task_id}
            disabled={busy || currentId === entry.task_id}
            onClick={() => onOpen(entry.task_id)}
          >
            {entry.city} · v{entry.version}
          </button>
        ))}
      </div>
      {versions.length > 0 && (
        <details>
          <summary>历史版本（{versions.length}）</summary>
          <div className="history-actions">
            {versions.map((entry) => (
              <button
                type="button"
                className="button secondary"
                key={entry.task_id}
                disabled={busy || currentId === entry.task_id}
                onClick={() => onOpen(entry.task_id)}
              >
                查看第{entry.version}版
              </button>
            ))}
          </div>
        </details>
      )}
      {currentId && !busy && (
        <div className="history-actions">
          {confirmDelete ? (
            <>
              <span>删除本次旅行及全部版本？</span>
              <button
                type="button"
                className="button secondary"
                onClick={remove}
              >
                确认删除全部版本
              </button>
              <button
                type="button"
                className="button secondary"
                onClick={() => setConfirmDelete(false)}
              >
                保留
              </button>
            </>
          ) : (
            <button
              type="button"
              className="button secondary"
              onClick={() => setConfirmDelete(true)}
            >
              删除这次旅行
            </button>
          )}
        </div>
      )}
    </section>
  );
}
