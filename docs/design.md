# Design reference

Descriptive, not binding — binding constraints live in REQUIREMENTS §9; the templates are the source of truth. The DaisyUI 4 prebuilt + `app.css` overlay is
permanent: the overlay supplies what the prebuilt cannot (utilities, the AA tint badges); DaisyUI 5 dropped prebuilt css; Tailwind needs the forbidden node toolchain.

## Archetypes — every screen is one of six

1. **Sign-in** — one card: Keycloak button; admin u/p form only when the password env is set.
2. **Catalog** — `.grid` of DaisyUI `.card.card-bordered.card-compact` tiles; a tile goes straight to that offering's provision form.
3. **Form** (provision/edit/bind) — 680px form card: service/plan selects, plan fields, name, advanced drawer (raw JSON, live validation).
4. **Table** (instances/bindings) — title + action; `.table-wrap` zebra rows (name, badge, buttons); 3 s poll while in-flight.
5. **Detail** (instance/binding) — spinner/badge title + actions, `dl.detail`, parameters `<pre>`, sub-table / credentials.
6. **Error** — `card card-bordered border-error`: 4xx/5xx status + message + Back.

Chrome: dark topbar; light sidebar (Service Catalog group: **Catalog** / **Instances**); DaisyUI `.breadcrumbs` drill into the instance
list. Vocabulary: Kubernetes / Service Catalog / OSB, services only (no application-domain features) — cluster
(`CLUSTER_NAME`, default `in-cluster`) → namespace (a `<select>`); events timeline on the instance detail page.

## Tokens (measured; app.css is the source)

- Chrome: topbar `#1f2328`; sidebar 208px `#eef0f3`, active `#1d4ed8` on `#dbeafe` (5.49:1) + 3px left accent; container max 1100px, padding 24/16 (14 on phone).
- Grid: tiles — white cards, 1px `#dfe3e8` border, name 16/600, description 13px muted, hover `#93c5fd`; content `repeat(auto-fill, minmax(280px, 1fr))` gap 16 → 3 columns (345.33px) at 1280.
- Type (system-ui, line-height 1.5): h1 22/600 · h3 20/600 · h2 17 · body 15 · table/buttons/badges 14 · secondary 13 · code 12.
- Color: DaisyUI indigo primary, `neutral` navbar; badges (AA): ok `#15803d`/`#dcfce7` · err `#b91c1c`/`#fee2e2` · warn `#92400e`/`#fef3c7` · info `#1d4ed8`/`#dbeafe`; muted `#57606f` (6.35:1) · error `#dc2626` · JSON pre `#0f172a`/`#e2e8f0` · soft chip `oklch(96% 0.01 260)`.
- Rhythm: gaps 16 cards / 14 form rows / 12,16 table cells / 8,16 dl / 5 label-to-control; radii 16/10/8 cards·table-wrap·drawer, 8 buttons, 4 code chips.
- One breakpoint, `max-width: 720px`: sidebar → horizontal strip, grids → one column, wide tables scroll in `.table-wrap` (min-width 620px), dl labels 110px; 390px phone pass.

## Inventory, states, a11y

- `btn` (+primary/outline/error/ghost/sm), one primary per page-head · `alert warning/error/info` · `badge` + tint via `badge_cls`.
- `card card-body` (compact/stats) · `table table-zebra` in `.table-wrap` · `input/select/textarea input-bordered` (locked plan values disabled).
- `.adv-drawer`/`.adv-head`/`.drawer-body` raw-JSON gate · DaisyUI `modal` + `schemaOpen` · `loading loading-spinner xs/sm`.
- `dl.detail` (grid 160/1fr) · `.kv` + `<code>` chips (plan resources, free-input hints).
- **States**: disabled = opacity 0.6; submit disabled while busy or invalid (reason inline in the drawer head); polling stops on terminal states.
- **A11y**: badge/muted pairs ≥4.5:1; semantic tables, `role="alert"`, `<dl>`, tied labels; never color alone; a new hex needs a measured ratio. Out of scope: dark mode, icons, non-spinner animation, themes, Tailwind.
