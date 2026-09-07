import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { ConversationsPage } from "./ConversationsPage";
const mocks = vi.hoisted(() => ({all:vi.fn(),history:vi.fn(),session:vi.fn()}));
vi.mock("@/lib/api",()=>({projectsApi:{all:mocks.all},storyApi:{history:mocks.history,session:mocks.session}}));
function mount(url="/conversations") { render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><MemoryRouter initialEntries={[url]}><ConversationsPage /></MemoryRouter></QueryClientProvider>); }
beforeEach(()=>{
 vi.clearAllMocks();
 vi.spyOn(window,"scrollTo").mockImplementation(()=>{});
 mocks.all.mockResolvedValue({projects:[{project:"Atlas",sessions:2},{project:"Orion",sessions:1}]});
 mocks.history.mockImplementation(async (p:string) => ({sessions:[{id:p==="Atlas"?1:3,title:p+" first conversation",opening:"Original prompt",answer:"Recorded answer",source_tool:"codex",message_count:2,started_at:"2026-09-01T12:00:00Z",prompt_at:"2026-09-01T12:00:00Z",answer_at:"2026-09-01T12:01:15Z"},...(p==="Atlas"?[{id:2,title:"Second conversation",message_count:1,started_at:null}]:[])],total:2,has_more:false,paths:[],hidden_generated:0}));
 mocks.session.mockImplementation(async (_p:string,id:number)=>({messages:[{id:id*10,role:"assistant",content:`Original transcript ${id}`,created_at:"2026-09-01T12:01:15Z"}],total:1,has_more:false}));
});
it("starts with project choice and reads only the selected conversation",async()=>{
 mount();expect(screen.getByText("Start with a project")).toBeTruthy();expect(mocks.history).not.toHaveBeenCalled();
 await screen.findByRole("option",{name:"Folder group: Atlas (2)"});
 await userEvent.selectOptions(screen.getByRole("combobox",{name:"Project"}),"Atlas");
 expect(await screen.findByText("Original transcript 1")).toBeTruthy();
 expect(screen.queryByText("Original transcript 2")).toBeNull();
 const reader=screen.getByRole("region",{name:"Selected conversation"});
 expect(within(reader).getByText("Recorded answer")).toBeTruthy();
 expect(reader.querySelector('time[datetime="2026-09-01T12:01:15Z"]')).toBeTruthy();
 await userEvent.click(screen.getByRole("button",{name:/Second conversation/}));
 expect(await screen.findByText("Original transcript 2")).toBeTruthy();expect(screen.queryByText("Original transcript 1")).toBeNull();
 await userEvent.selectOptions(screen.getByRole("combobox",{name:"Project"}),"Orion");
 expect(await screen.findByText("Original transcript 3")).toBeTruthy();expect(screen.queryByText("Original transcript 2")).toBeNull();
});
it("honours a conversation URL and keeps provider scope on requests",async()=>{
 mount("/conversations?project=Atlas&conversation=2&provider=codex");
 expect(await screen.findByText("Original transcript 2")).toBeTruthy();
 await waitFor(()=>expect(mocks.session).toHaveBeenCalled());
 expect(mocks.session.mock.calls[0][2].getAll("provider")).toEqual(["codex"]);
 expect(mocks.session.mock.calls.some(c=>c[1]===1)).toBe(false);
});
it("keeps malformed timestamps visible without crashing or emitting an invalid datetime",async()=>{
 const original=mocks.history.getMockImplementation()!;
 mocks.history.mockImplementation(async (...args)=>{ const data=await original(...args);data.sessions[0].started_at="not-a-date";return data; });
 mount("/conversations?project=Atlas");
 expect(await screen.findByText("Original transcript 1")).toBeTruthy();
 expect(screen.getByText("Invalid recorded time: not-a-date")).toBeTruthy();
 expect(document.querySelector('time[datetime="not-a-date"]')).toBeNull();
});
