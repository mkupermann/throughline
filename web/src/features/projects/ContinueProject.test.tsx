import {render,screen} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {QueryClient,QueryClientProvider} from "@tanstack/react-query";
import {MemoryRouter} from "react-router-dom";
import {it,expect,vi} from "vitest";
import {ContinueProject} from "./ContinueProject";
const mock=vi.hoisted(()=>({request:vi.fn()}));
vi.mock("@/lib/api",()=>mock);
it("builds only on request and keeps source content as plain text",async()=>{
 mock.request.mockResolvedValue({project:"alpha",markdown:"# Evidence\n<script>bad()</script>",sources:[{kind:"messages",id:3,href:"/c/2"}],truncated:true,empty:false});
 render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><MemoryRouter><ContinueProject project="alpha"/></MemoryRouter></QueryClientProvider>);
 expect(mock.request).not.toHaveBeenCalled();
 await userEvent.click(screen.getByRole("button",{name:"Continue this project"}));
 const text=await screen.findByRole("textbox",{name:"Continuation brief"});
 expect((text as HTMLTextAreaElement).value).toContain("<script>bad()</script>");
 expect(mock.request).toHaveBeenCalledWith("/projects/alpha/continue");
 expect(document.querySelector("script")).toBeNull();
 expect(screen.getByText(/Bounded preview/)).toBeTruthy();
});
