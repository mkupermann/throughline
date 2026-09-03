import { createElement } from "react";
import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { JobConsole, parseJobCompletion } from "./JobConsole";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly listeners = new Map<string, (event: MessageEvent) => void>();
  onerror: ((event: Event) => void) | null = null;
  close = vi.fn();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(name: string, listener: (event: MessageEvent) => void) {
    this.listeners.set(name, listener);
  }

  emit(name: string, data: string) {
    this.listeners.get(name)?.(new MessageEvent(name, { data }));
  }
}

vi.stubGlobal("EventSource", FakeEventSource);

beforeEach(() => {
  FakeEventSource.instances = [];
});

describe("parseJobCompletion", () => {
  it("recognises a successful job", () => {
    expect(parseJobCompletion("exit=0 duration=4.2s")).toEqual({
      ok: true,
      returncode: 0,
      summary: "exit=0 duration=4.2s",
    });
  });

  it("treats a non-zero or missing exit code as a failure", () => {
    expect(parseJobCompletion("exit=3 duration=1.0s").ok).toBe(false);
    expect(parseJobCompletion("exit=None duration=0.0s error=missing binary")).toMatchObject({
      ok: false,
      returncode: null,
    });
  });
});

describe("JobConsole", () => {
  it("lets EventSource reconnect after a transient error and still reports completion", async () => {
    const onFinished = vi.fn();
    render(createElement(JobConsole, { jobId: "abc123", onFinished }));
    const source = FakeEventSource.instances[0];

    act(() => source.onerror?.(new Event("error")));
    expect(source.close).not.toHaveBeenCalled();

    act(() => source.emit("done", "exit=0 duration=1.0s"));
    await waitFor(() => expect(onFinished).toHaveBeenCalledOnce());
    expect(source.close).toHaveBeenCalledOnce();
  });
});
