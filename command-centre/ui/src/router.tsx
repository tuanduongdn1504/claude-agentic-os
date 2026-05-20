import {
  createRootRoute, createRoute, createRouter, Outlet,
} from '@tanstack/react-router';
import { AppShell } from '@/components/layout/AppShell';
import CommandPage from '@/pages/CommandPage';
import ActivityPage from '@/pages/ActivityPage';
import SkillsPage from '@/pages/SkillsPage';
import SessionsPage from '@/pages/SessionsPage';
import DecisionsPage from '@/pages/DecisionsPage';

// v0.6.6: optional `?embed=1` flag, read by AppShell to strip chrome
// when the dashboard is rendered inside Obsidian's web-viewer iframe.
type RootSearch = { embed?: string };

const rootRoute = createRootRoute({
  component: () => (
    <AppShell />
  ),
  validateSearch: (search: Record<string, unknown>): RootSearch => {
    const embed = search.embed;
    return embed === undefined || embed === null ? {} : { embed: String(embed) };
  },
  notFoundComponent: () => (
    <div className="text-center py-24 text-text-dim">
      <div className="text-4xl font-mono mb-2 text-text">404</div>
      <div>no route here</div>
    </div>
  ),
});

// AppShell renders <Outlet /> — child routes fill it.
const commandRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: CommandPage,
});

const activityRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/activity',
  component: ActivityPage,
});

const skillsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/skills',
  component: SkillsPage,
});

const sessionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/sessions',
  component: SessionsPage,
});

const decisionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/decisions',
  component: DecisionsPage,
});

const routeTree = rootRoute.addChildren([commandRoute, activityRoute, skillsRoute, sessionsRoute, decisionsRoute]);

export const router = createRouter({
  routeTree,
  defaultPreload: 'intent',
});

declare module '@tanstack/react-router' {
  interface Register { router: typeof router }
}

// Dev-only ping — keeps `Outlet` typed/imported for AppShell indirection.
export { Outlet };
