import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Transcript } from "./Transcript";

describe("Transcript", () => {
  it("renders adapter tool_calls when content_blocks are absent", () => {
    render(
      <Transcript
        messages={[
          {
            id: 42,
            role: "assistant",
            content: null,
            content_blocks: null,
            tool_calls: [
              {
                tool_name: "execute_command",
                input: '{"command":"npm test"}',
              },
            ],
            tool_name: "execute_command",
            model: "cursor-model",
            created_at: "2026-01-01T09:00:00Z",
          },
        ]}
      />,
    );

    expect(screen.getByText("execute_command")).toBeTruthy();
    expect(screen.getByText("npm test")).toBeTruthy();
    expect(screen.queryByText(/no recorded content/i)).toBeNull();
  });
});

 it("keeps output references and exact times visible without opening a disclosure", () => {
   render(<Transcript messages={[{id: 99, role: "assistant", content: "Done", created_at: "2026-08-01T10:02:34Z", content_blocks: [{type: "output_file", path: "report.md"}]}]} />);
   expect(screen.getByText("Answer")).toBeTruthy();
   expect(screen.getByText("Result / file")).toBeTruthy();
   expect(screen.getByText("report.md").closest("details")).toBeNull();
   expect(screen.getByText(/:02:34/)).toBeTruthy();
 });

it("retains structured tool output instead of silently discarding objects", () => {
  render(<Transcript messages={[{id:999,role:"tool_result",content:null,content_blocks:[{type:"tool_result",content:{status:"passed",checks:12}}]}]} />);
  expect(screen.getByText(/"status": "passed"/)).toBeTruthy();
  expect(screen.getByText(/"checks": 12/)).toBeTruthy();
});
it("renders imported HTML as text and makes invalid source times explicit", () => {
  const source='<img src=x onerror="alert(1)">';
  render(<Transcript messages={[{id:1000,role:"assistant",content:source,created_at:"invalid-time"}]} />);
  expect(screen.getByText(source)).toBeTruthy();
  expect(document.querySelector('img[src="x"]')).toBeNull();
  expect(screen.getByText("Invalid recorded time: invalid-time")).toBeTruthy();
  expect(document.querySelector('time[datetime="invalid-time"]')).toBeNull();
});
