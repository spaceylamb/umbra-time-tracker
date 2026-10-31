import {
  Outlet,
  createRootRouteWithContext,
  createRoute,
  createHashHistory,
  createRouter,
  redirect,
} from "@tanstack/react-router";
import { Pending } from "./ui/Pending";
import type { QueryClient } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";
import { STATIC, dayQuery, metaQuery, setupQuery, weekQuery } from "./api/client";
import { DayPage } from "./day/DayPage";
import { FirstRun } from "./day/FirstRun";
import { WeekPage } from "./week/WeekPage";
import { SourcesPage } from "./sources/SourcesPage";
import { useTheme } from "./theme";
import { markNavigation } from "./lib/flip";
import { DemoStrip } from "./ui/DemoStrip";

function Index() {
  const meta = useQuery(metaQuery);
  const nav = useNavigate();
  useTheme(meta.data);
  const last = meta.data?.order.at(-1);
  useEffect(() => {
    if (last) void nav({ to: "/day/$date", params: { date: last }, replace: true });
  }, [last, nav]);
  if (!meta.data || last) return <Pending error={meta.error} />;
  return <FirstRun meta={meta.data} />;
}

const rootRoute = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: () => <><DemoStrip /><Outlet /></>,
});

// "/" means the newest day there is data for — not the calendar's today,
// which may have nothing yet.
const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  loader: async ({ context }) => {
    const meta = await context.queryClient.ensureQueryData(metaQuery);
    const last = meta.order.at(-1);
    if (last) throw redirect({ to: "/day/$date", params: { date: last } });
    return null;
  },
  component: Index,
});

export const dayRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/day/$date",
  // Load before navigating, so the view transition crossfades between two
  // drawn pages rather than into a blank one that fills in afterwards.
  loader: async ({ context, params }) => {
    const qc = context.queryClient;
    try {
      const [meta] = await Promise.all([qc.ensureQueryData(metaQuery), qc.ensureQueryData(dayQuery(params.date))]);
      // The strip's three weeks: start them now, so they arrive together
      // with (or just after) the page rather than one by one after it.
      const i = Math.max(meta.order.indexOf(params.date), 0);
      for (const d of meta.order.slice(Math.max(0, i - 20), i + 21)) void qc.prefetchQuery(dayQuery(d));
    } catch { /* the page shows the error */ }
    return null;
  },
  component: DayPage,
});

export const weekRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/week/$week",
  loader: ({ context, params }) => Promise.all([
    context.queryClient.ensureQueryData(metaQuery),
    context.queryClient.ensureQueryData(weekQuery(params.week)),
  ]).catch(() => null),
  component: WeekPage,
});

const sourcesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/sources",
  loader: ({ context }) => Promise.all([
    context.queryClient.ensureQueryData(metaQuery),
    context.queryClient.ensureQueryData(setupQuery),
  ]).catch(() => null),
  component: SourcesPage,
});

export const router = createRouter({
  // A static host has no fallback that turns /day/2026-09-08 into the app, so
  // the exported page routes in the hash; umbra serve does the fallback itself.
  ...(STATIC ? { history: createHashHistory() } : {}),
  routeTree: rootRoute.addChildren([indexRoute, dayRoute, weekRoute, sourcesRoute]),
  context: { queryClient: undefined! },
  // A new view opens at its top and Back returns to where you were. No
  // whole-page crossfade: it double-exposes two pages and reads as a flash;
  // the content under the topbar fades in instead (.page-in).
  scrollRestoration: true,
  defaultPendingMs: 400,
});

// Every navigation: the pinned headers follow the scroll reset without motion.
router.subscribe("onBeforeNavigate", markNavigation);
router.subscribe("onResolved", markNavigation);

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
