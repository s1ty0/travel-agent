import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { TaskHistory } from "../components/TaskHistory";
import { ResultPanel } from "../components/ResultPanel";
import { completed } from "./fixtures";
import type { Task } from "../lib/contracts";

afterEach(() => vi.unstubAllGlobals());

test("history reads owned versions and deleting requires a separate explicit click", async () => {
  let deleted = false;
  const onDeleted = vi.fn();
  const entry = {
    task_id: completed.task_id,
    root_id: completed.task_id,
    city: "南京",
    version: 1,
  };
  const fetcher = vi.fn(async (_url: string, options?: RequestInit) => {
    if (options?.method === "DELETE") {
      deleted = true;
      return Response.json({ deleted: true });
    }
    return Response.json({ tasks: deleted ? [] : [entry] });
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <TaskHistory
      currentId={completed.task_id}
      refresh={0}
      busy={false}
      onOpen={vi.fn()}
      onDeleted={onDeleted}
    />,
  );
  await screen.findByText("南京 · v1");
  expect(screen.getByRole("button", { name: "南京 · v1" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "删除这次旅行" }));
  expect(deleted).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "确认删除全部版本" }));
  await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce());
  expect(
    fetcher.mock.calls.filter(([, options]) => options?.method === "DELETE"),
  ).toHaveLength(1);
});

test("interrupted work explains that providers were not called again", () => {
  const task: Task = {
    task_id: completed.task_id,
    state: "interrupted",
    error: "worker_interrupted_no_retry",
    output: null,
  };
  render(<ResultPanel task={task} />);
  expect(screen.getByText("规划进程已中断")).toBeVisible();
  expect(screen.getByText(/调用费用可能尚未确认/)).toBeVisible();
});

test("stored version changes are shown beside the result", () => {
  const task = structuredClone(completed) as Task;
  task.version = 2;
  if (!task.output) throw new Error("missing fixture");
  task.output.change_summary = {
    added: 0,
    removed: 1,
    time_changed: 2,
    conditions_changed: true,
  };
  render(<ResultPanel task={task} />);
  expect(screen.getByText("本版修改 · v2")).toBeVisible();
  expect(screen.getByText(/移除1项/)).toBeVisible();
});
