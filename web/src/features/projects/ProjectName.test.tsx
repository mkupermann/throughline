import {render, screen} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {QueryClient,QueryClientProvider} from "@tanstack/react-query";
import {it,expect,vi} from "vitest";
import {ProjectName,projectLabel} from "./ProjectName";
const rename=vi.hoisted(()=>vi.fn());
vi.mock("@/lib/api",()=>({projectsApi:{rename}}));
it("marks future folder imports unconfirmed instead of inventing a project name",()=>{
 expect(projectLabel({project:"si"})).toBe("Name pending — no readable source excerpt");
 expect(projectLabel({project:"er",display_name:"Nebula journey"})).toBe("Nebula journey");
});
it("saves the display name against the original stable key",async()=>{
 rename.mockResolvedValue({display_name:"Nebula journey"});
 render(<QueryClientProvider client={new QueryClient()}><ProjectName project="er" folders={1}/></QueryClientProvider>);
 expect(screen.getByText(/not a confirmed project assignment/)).toBeTruthy();
 await userEvent.click(screen.getByRole("button",{name:"Name this project"}));
 await userEvent.type(screen.getByRole("textbox",{name:"Project name"}),"Nebula journey");
 await userEvent.click(screen.getByRole("button",{name:"Save name"}));
 expect(rename).toHaveBeenCalledWith("er","Nebula journey");
});
