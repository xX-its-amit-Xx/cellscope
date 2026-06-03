# No nginx — the frontend is served by FastAPI

CellScope ships as a **single image on a single port (8000)**. There is no nginx
(or any other reverse proxy) inside the container.

Per `docs/CONTRACT.md` section 1 ("Serving model: single image, single port"),
the built React/TypeScript/deck.gl frontend is compiled to static assets at build
time (the `frontend` stage in `docker/Dockerfile`) and then served **directly by
the FastAPI backend** using Starlette's `StaticFiles`:

- The compiled assets are copied into `/app/static` (the value of
  `CELLSCOPE_STATIC_DIR`).
- `backend/app/main.py` mounts that directory at `/` with an SPA fallback so a
  hard refresh on any client-side route returns `index.html`.
- The REST API lives under the `/api` prefix and the job WebSocket at
  `/api/ws/jobs`; everything else is the static frontend.

Because the API and the assets are same-origin on one port, the client always
uses relative URLs (`/api/...`), so **no proxy is required** in production. In
development, Vite's dev server (port 5173) proxies `/api` to the backend at
`http://localhost:8000` — see `frontend/vite.config.ts` — which is purely a
dev-time convenience and likewise does not involve nginx.

## Why not nginx?

- **Simplicity / self-hosting.** One process, one port, one container to run with
  `docker compose up`. Nothing to configure.
- **No extra moving parts.** `StaticFiles` is more than fast enough for serving a
  handful of hashed bundle assets; the performance-critical path (cell
  coordinates, expression, selection codes) is the binary protocol described in
  CONTRACT section 4/9, not static file throughput.

## If you really want a proxy

Putting CellScope behind nginx, Caddy, Traefik, etc. is entirely optional and
lives **outside** this image. Terminate TLS and forward both HTTP and WebSocket
traffic to the container's port 8000, for example:

```nginx
location / {
    proxy_pass http://cellscope:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;       # WebSocket (/api/ws/jobs)
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 3600s;                     # long-running recompute jobs
}
```

That configuration is informational only — CellScope does not need it to run.
