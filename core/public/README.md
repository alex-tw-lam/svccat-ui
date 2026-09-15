# core/public/ — first-party assets + vendored-library slots

The app serves everything in this directory itself via Django `FileResponse` routes in `service_manager/urls.py` — no
static-files machinery, no build step. URLs are flat (wired in `core/templates/core/layout.html`):

| URL served           | File in this directory           | Shipped in repo?                                 |
| -------------------- | -------------------------------- | ------------------------------------------------ |
| `/app.js` `/app.css` | `app.js` `app.css`               | yes (first-party: Alpine logic + overlay styles) |
| `/htmx.min.js`       | `htmx.min.js`                    | **no — drop in**                                 |
| `/alpine.min.js`     | `alpine.min.js`                  | **no — drop in**                                 |
| `/daisyui.css`       | `daisyui.css` (+ optional `.gz`) | **no — drop in**                                 |

## Files the operator must drop in (exact names; `.gitignore` excludes them)

1. **`htmx.min.js`** — htmx **2.0.4** (the version this app was built and tested against).
2. **`alpine.min.js`** — Alpine.js **3.14.9** core build (`alpinejs/dist/cdn.min.js`; rename).
3. **`daisyui.css`** — DaisyUI **4.12.24** prebuilt **full** css (`daisyui/dist/full.css`; rename). Do NOT
   substitute a tailwind-built subset — the templates use components across the whole prebuilt file.
4. _(optional)_ **`daisyui.css.gz`** — gzip of the file above; if the client sends `Accept-Encoding: gzip`,
   the view serves it with `Content-Encoding: gzip` (gunicorn doesn't compress; 2.9 MB → ~174 KB).

Quick copy-paste:

```sh
cd core/public && curl -fsSLo htmx.min.js https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js && curl -fsSLo alpine.min.js https://unpkg.com/alpinejs@3.14.9/dist/cdn.min.js && curl -fsSLo daisyui.css https://unpkg.com/daisyui@4.12.24/dist/full.css && gzip -9 -n -c daisyui.css > daisyui.css.gz
```
