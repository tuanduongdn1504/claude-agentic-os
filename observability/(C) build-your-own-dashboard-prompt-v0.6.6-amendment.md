# v0.6.6 Amendment — Obsidian embed mode (`?embed=1` query param)

Apply on top of current `main` HEAD (post v0.6.5). UI/routing change
to the dashboard — first non-Telegram release since v0.6.0. Closes
the loop on this arc's original framing (Chase AI's Obsidian
command-centre video) without shipping an Obsidian companion plugin:
just strip dashboard chrome when loaded inside Obsidian's web-viewer
plugin iframe.

Frontend + 1 backend header. No schema, no new endpoints, no
breaking changes.

**Numbered `v0.6.6`** — patch over v0.6.5. Alternative `v0.7.0` is
defensible (first product surface expansion since v0.6.0 — embed
mode is a new use case, not a Telegram-bridge increment).
Recommendation: `v0.6.6` keeps the patch cadence; override at build
time if minor-bump preferred.

**Strategy**: iframe-via-web-viewer, not Obsidian companion plugin.
Chase AI's video shows a custom Obsidian plugin built by Claude
Code; this product's strategy stays standalone localhost web app +
embed-friendly URL. Operator installs the web-viewer plugin in
Obsidian, pastes `http://localhost:8765/?embed=1`, gets the
dashboard rendered without the AppShell chrome inside their vault.

**Verified against current `main` HEAD**:
- TanStack Router (file-based) already handles `/`, `/activity`,
  `/skills`, `/sessions` (v0.4.0), `/decisions` (v0.4.0). All
  candidate embed routes.
- `AppShell` wraps every route with nav + header + EmergencyStopBanner
  + CommandPalette.
- FastAPI serves UI as static from `ui/dist/`. Server-side headers
  controlled in `scripts/server.py` startup.
- No iframe-blocking headers currently set (verified — server.py
  has no `X-Frame-Options` middleware).
- No collision with v0.6.0–v0.6.5 surfaces (all Telegram-bridge or
  schema work).

---

## Why this release

The original Chase AI video that started this arc framed Obsidian
as the command-centre shell. This product picked the opposite
strategy — standalone web app at `127.0.0.1:8765` — for valid
reasons (no Obsidian dependency, simpler install, web-native UX).

But operators who run Obsidian as their daily knowledge base lose
the eye-level co-presence Chase AI demonstrated. They have to
context-switch between Obsidian vault and a separate browser tab.

`?embed=1` is the minimum-viable bridge between the two strategies:
- Dashboard stays a standalone web app (no Obsidian SDK, no plugin
  manifest, no Obsidian release cycle to track).
- Operators who want the eye-level co-presence install Obsidian's
  web-viewer plugin and paste the embed URL into a pane.
- All v0.4.0+ operator surfaces (SessionsExplorer, DecisionsQueue,
  TaskBoard, SkillLauncher) work identically inside Obsidian.

Cost: ~2-3h of frontend work + one FastAPI middleware line. No
ongoing Obsidian compatibility burden — Obsidian's web-viewer is a
standard iframe, not a custom integration point.

## Frontend delta — `AppShell.tsx`

Single primary file change. Add embed-mode detection + conditional
render.

### Embed detection

Read `?embed=1` from URL query string. Use TanStack Router's
`useSearch()` hook OR direct `window.location.search` parse — both
work. Recommendation: TanStack Router's `useSearch({from: '__root__'})`
to keep the embed flag reactive across route changes.

```tsx
const search = useSearch({ from: '__root__' });
const isEmbedded = search.embed === '1' || search.embed === 'true';
```

Declare the optional `embed` search param at the root route
(`__root__.tsx` or `router.tsx` config) so TanStack typing accepts
it.

### Conditional render

Wrap AppShell's existing JSX:

```tsx
return (
  <div className={isEmbedded ? "app-shell embedded" : "app-shell"}>
    {!isEmbedded && <Nav />}
    {!isEmbedded && <Header />}
    {!isEmbedded && <EmergencyStopBanner />}
    <main className={isEmbedded ? "main embedded" : "main"}>
      <Outlet />
    </main>
    {!isEmbedded && <CommandPalette />}
  </div>
);
```

**Hidden in embed mode:**
- `Nav` — Obsidian sidebar already provides navigation; the
  dashboard nav clutters.
- `Header` — title + branding redundant inside Obsidian frame.
- `CommandPalette` — Obsidian's own `⌘P` palette is at hand.
- `EmergencyStopBanner` — see "Critical-signal carve-out" below.

**Kept in embed mode:**
- `AttentionBar` (rendered inside the page, not AppShell) — surfaces
  stuck loops, failed tasks, dispatcher staleness. Operators need
  this regardless of frame.
- All panels rendered by `<Outlet />` — the actual data view.

### Critical-signal carve-out — EmergencyStopBanner

The EmergencyStopBanner sits at the page top with a red "Emergency
Stop" button. In embed mode, hiding it removes a critical-signal
surface from the operator's view inside Obsidian.

**Decision**: hide in embed mode by default; render a compact
inline banner (one line, red dot + "Emergency stop available" text +
button) only when `emergency_stop` state is `'1'` OR a dispatcher
issue requires attention. The compact banner injects above the
`<Outlet />`, controlled by `?embed=1` plus a fetch of
`GET /api/system/state`.

Defer the inline compact banner to v0.7+ if scope feels tight in
this release. For MVP: hide EmergencyStopBanner entirely in embed
mode; operator handles emergencies by opening the dashboard outside
Obsidian. Document this trade-off in README.

### Embed-mode CSS

Add to existing global CSS or AppShell's stylesheet:

```css
.app-shell.embedded {
  /* Tighter framing for ~400-600px Obsidian panel widths */
  padding: 0;
}

.app-shell.embedded .main {
  padding: 16px;       /* was 24-32px */
  max-width: 100%;
  margin: 0;
}

.app-shell.embedded .panel,
.app-shell.embedded .card {
  margin-bottom: 12px; /* tighter than full-page 16-20px */
}

/* Optional: smaller font in embed mode if Obsidian panel narrow */
@media (max-width: 600px) {
  .app-shell.embedded {
    font-size: 13px; /* was 14-15px */
  }
}
```

Match the existing Tailwind / CSS variable convention. Build agent
should consult the current AppShell + theme tokens before adding
new vars.

### Internal link preservation

TanStack Router's `Link` component supports preserving search
params across navigation:

```tsx
<Link to="/skills" search={(prev) => prev}>...</Link>
```

For embed mode, internal links should preserve `?embed=1` so the
operator stays in embed mode as they navigate. Wrap the Nav links
(when embed is OFF, Nav is hidden anyway; this matters only if a
panel inside a page links to another page).

Audit existing internal links:
- `LiveSessionDetail` may link to `/sessions/{id}` — preserve
- `SkillsRegistry` may link from skill row to skill detail — preserve
- `ProjectBreakdown` may link to filtered sessions view — preserve

For MVP: add `search={(prev) => prev}` to every internal `<Link>`.

## Backend delta — `scripts/server.py`

Single change: ensure no `X-Frame-Options: DENY` is sent.

FastAPI doesn't set `X-Frame-Options` by default. Verify by
inspecting any middleware chain — if a security middleware adds it,
either:
- Remove the middleware (if it's only added for the embed concern)
- Add a conditional middleware that omits `X-Frame-Options` when
  the request path starts with `/` and query has `embed=1`

Most likely no change is needed. Verify with:

```bash
curl -I http://localhost:8765/?embed=1 | grep -i x-frame
```

If header absent → no change needed.

### Content-Security-Policy (optional hardening)

If hardening desired, add a CSP middleware that sets
`Content-Security-Policy: frame-ancestors 'self'`. Allows same-
origin iframes but blocks cross-origin embedding. Obsidian's web-
viewer plugin loads localhost URLs as `app://obsidian.md/...` —
verify CSP doesn't block this.

Defer CSP to v0.7+ if it requires real Obsidian iframe testing.
MVP: just verify no `DENY` is sent.

## Documentation delta

### README.md

Add a new subsection under Install or "Usage":

```markdown
## Embed in Obsidian (optional)

The dashboard can be embedded inside Obsidian's web-viewer plugin
so it lives next to your notes.

1. Install the Web Viewer plugin in Obsidian (Community Plugins →
   search "Web Viewer").
2. Restart Obsidian, enable the plugin.
3. Open the command palette (`⌘P`) → "Web Viewer: Open URL".
4. Paste: `http://localhost:8765/?embed=1`
5. Pin the pane to your preferred location (right sidebar works
   well).

The embed strips the dashboard's own nav and header — Obsidian's
sidebar provides navigation; the dashboard's panels do the work.

**Emergency stop in embed mode**: hidden by design. To trigger
emergency stop, open `http://localhost:8765/` in a regular browser
(without `?embed=1`).
```

### `.env.example`

No new env vars.

## Stop conditions

1. **Embed mode hides chrome.** `GET /?embed=1` renders the
   Command page without Nav, Header, CommandPalette, or
   EmergencyStopBanner. Same with `/activity?embed=1`,
   `/skills?embed=1`, `/sessions?embed=1`, `/decisions?embed=1`.
2. **Non-embed unchanged.** `GET /` (no query) renders identically
   to current `main`. No visual diff. No new render artifacts.
3. **Internal navigation preserves flag.** From `/?embed=1`, click
   a link to `/sessions` via any panel-internal link → URL becomes
   `/sessions?embed=1`, embed mode persists.
4. **AttentionBar still visible in embed.** Trigger a dispatcher
   stall (mock or wait for natural). AttentionBar renders the
   issue inside the embedded view.
5. **Critical-signal carve-out documented.** README explicitly
   states EmergencyStopBanner is hidden in embed; operator opens
   dashboard in browser for emergencies.
6. **No iframe-blocking header.** `curl -I /?embed=1` shows no
   `X-Frame-Options: DENY` or `X-Frame-Options: SAMEORIGIN` that
   would prevent Obsidian iframe loading. If a CSP exists, its
   `frame-ancestors` allows `'self'` and `app://obsidian.md`.
7. **Obsidian smoke.** Install Obsidian's web-viewer plugin
   manually, paste embed URL, verify the embed renders + interacts
   (click Launch button on a SkillLauncher card → task fires). One-
   time manual verification; document the procedure in README.
8. **Mobile-width responsiveness.** Embed page at 400px width
   (Obsidian narrow panel) renders without horizontal scroll. Use
   browser dev tools mobile view to verify.
9. **Backward compat.** All existing routes work identically when
   `embed` query param absent. Playwright tests pass unchanged.

## Order of operations

1. **TanStack Router root-search type.** Declare optional `embed`
   param in the route config so TypeScript accepts `?embed=1`.
2. **AppShell conditional render.** Detect `embed=1`, hide
   chrome conditionally.
3. **Embed-mode CSS.** Tighter padding, optional smaller font at
   narrow widths.
4. **Internal `<Link>` preservation.** Audit all internal links;
   add `search={(prev) => prev}` to each.
5. **Backend `X-Frame-Options` audit.** Verify no DENY header;
   adjust middleware if needed.
6. **README section.** Document install + usage + the
   EmergencyStopBanner caveat.
7. **Smoke tests** — all 9 stop conditions. Stop 7 (Obsidian smoke)
   is operator-side manual; document the procedure.
8. **Playwright spec** — add `tests/e2e/v0.6.6.spec.ts` covering
   stop 1, 2, 3, 4 (`embed=1` chrome hidden, no embed renders full
   chrome, navigation preserves flag, AttentionBar visible).
9. **CHANGELOG v0.6.6 entry** flipped from DRAFT to shipped. Match
   v0.6.4 / v0.6.5 style.

## Not in this release (deferred)

- **Compact inline EmergencyStopBanner** for embed mode (v0.7+).
  MVP hides entirely; operator opens dashboard outside Obsidian for
  emergencies. Trade-off documented in README.
- **CSP `frame-ancestors` hardening** with explicit Obsidian
  app://obsidian.md origin allowlist. Verify Obsidian iframe origin
  in real testing first.
- **`?embed=compact` or `?embed=tab` modes** for different
  Obsidian pane sizes. MVP: single mode. Add modes when usage shows
  operators consistently using narrow panels.
- **Theme query param `?theme=light|dark`** to match Obsidian's
  active theme. MVP: dashboard dark theme stays (matches Obsidian
  dark default; covers ~80% of operators per Chase AI video framing).
- **Custom Obsidian companion plugin** with deeper integration
  (Obsidian commands, vault file linking). Out of scope; the
  iframe path keeps the product standalone.

## Estimate

~2-3h:
- AppShell conditional render: ~30 min
- Embed-mode CSS: ~30 min
- Internal Link preservation audit: ~20-30 min
- Backend X-Frame audit: ~15 min
- README section: ~15 min
- Playwright spec (4 cases): ~30 min
- Manual Obsidian smoke (stop 7): ~15 min
- CHANGELOG flip: ~10 min

Larger than v0.6.5 (~1h) because UI surface + manual smoke +
Playwright; smaller than v0.6.0 / v0.6.3 (no schema, no new
endpoints, no operator config).

## Compat note with v0.6.0 – v0.6.5

No collisions:
- All v0.6.x Telegram-bridge work (v0.6.1 / v0.6.2 / v0.6.4 /
  v0.6.5) untouched — embed mode is dashboard UI only.
- v0.6.0 SkillLauncher renders identically in embed mode (no
  custom embed handling needed).
- v0.6.3 multi-account AccountBreakdownCard renders identically.
- v0.5.0-mvp2 / v0.6.4 chained-task UI unchanged.

The embed flag is invisible to every existing feature — it's a
chrome toggle, not a data path.

---

End of amendment. Apply against current `main` HEAD (post-v0.6.5).
Quality bar matches v0.6.3 — minimal additive surface, zero-config
default (operators who don't use Obsidian see no change). Estimate
~2-3h.
