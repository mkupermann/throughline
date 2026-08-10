import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createBrowserRouter } from "react-router-dom";

import { Shell } from "@/components/Shell";
import { ThemeProvider } from "@/lib/theme";
import { OverviewPage } from "@/features/overview/OverviewPage";
import { FindPage } from "@/features/find/FindPage";
import { TimelinePage } from "@/features/timeline/TimelinePage";
import { DETAIL_KINDS, DetailPage } from "@/features/detail/DetailPage";
import { CuratePage } from "@/features/curate/CuratePage";
import { OperatePage } from "@/features/operate/OperatePage";
import { ConsolePage } from "@/features/console/ConsolePage";
import { ToastProvider } from "@/components/Toaster";

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
      { path: "operate", element: <OperatePage /> },
      ...Object.entries(DETAIL_KINDS).map(([prefix, kind]) => ({
        path: `${prefix}/:id`,
        element: <DetailPage kind={kind} />,
      })),
      { path: "console", element: <ConsolePage /> },
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
