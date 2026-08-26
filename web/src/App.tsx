import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createBrowserRouter } from "react-router-dom";

import { Shell } from "@/components/Shell";
import { ThemeProvider } from "@/lib/theme";
import { OverviewPage } from "@/features/overview/OverviewPage";
import { FindPage } from "@/features/find/FindPage";
import { TimelinePage } from "@/features/timeline/TimelinePage";
import { DETAIL_KINDS, DetailPage } from "@/features/detail/DetailPage";
import { CuratePage } from "@/features/curate/CuratePage";
import { ProjectPage } from "@/features/projects/ProjectPage";
import { OperatePage } from "@/features/operate/OperatePage";
import { ConsolePage } from "@/features/console/ConsolePage";
import { ToastProvider } from "@/components/Toaster";
import { DashboardPage } from "@/features/pm/DashboardPage";
import { CockpitPage } from "@/features/pm/CockpitPage";
import { TaskPage } from "@/features/pm/TaskPage";
import { RolesPage } from "@/features/pm/RolesPage";
import { MembersPage } from "@/features/pm/MembersPage";
import { TeamsPage } from "@/features/pm/TeamsPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Local database — refetching on every window focus is noise, but
      // stale-after-30s keeps a long-lived tab honest.
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        // Do not hammer a server that is down or a query that 4xx'd.
        const status = (error as { status?: number }).status ?? 0;
        if (status === 0 || (status >= 400 && status < 500)) return false;
        return failureCount < 2;
      },
    },
  },
});

const router = createBrowserRouter([
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "find", element: <FindPage /> },
      { path: "timeline", element: <TimelinePage /> },
      { path: "curate", element: <CuratePage /> },
      // `*` so a project name with a slash-free but otherwise awkward shape
      // ("The FireScore Website") survives the round trip.
      { path: "project/:name", element: <ProjectPage /> },
      { path: "operate", element: <OperatePage /> },
      ...Object.entries(DETAIL_KINDS).map(([prefix, kind]) => ({
        path: `${prefix}/:id`,
        element: <DetailPage kind={kind} />,
      })),
      { path: "console", element: <ConsolePage /> },
      { path: "pm", element: <DashboardPage /> },
      { path: "pm/projects/:id", element: <CockpitPage /> },
      { path: "pm/tasks/:id", element: <TaskPage /> },
      { path: "pm/roles", element: <RolesPage /> },
      { path: "pm/members", element: <MembersPage /> },
      { path: "pm/teams", element: <TeamsPage /> },
    ],
  },
]);

export function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <RouterProvider router={router} />
        </ToastProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
