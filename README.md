<p align="center">
  <img src="assets/logo.svg" width="110" alt="System Stats logo">
</p>

<h1 align="center">System Stats</h1>

<p align="center">
  <b>A tiny, live monitor for your Docker host.</b><br>
  Every container at a glance, a real-time console inside each one, and the system vitals on the side.<br>
  No agents, no accounts, no database — one small container that uses ~20&nbsp;MB of RAM.
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.13-3776AB?logo=python&logoColor=white">
  <img alt="Docker" src="https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white">
  <img alt="Dependencies" src="https://img.shields.io/badge/dependencies-zero-3fb950">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-blue">
</p>

---

## Why?

Most container dashboards are either powerful-but-overwhelming or pretty-but-shallow. **System Stats** is the panel you open when you just want to *see your server*: what's running, what's hot, and what a container is saying right now. It reads like a status page, not an admin console.

## Features

- **Live dashboard** — every container as a card with status, CPU and memory bars, and live network rates, next to a system summary (CPU, memory, disk, network, load, uptime). Refreshes every **5 seconds**.
- **Real-time console** — click any container and its logs stream live into the page (Server-Sent Events, ANSI codes stripped, pause-scroll when you need to read a line).
- **Live detail sidebar** — per-container CPU, memory, network in/out with rates, disk I/O, PIDs, restart count, ports and start time, plus the host summary. Refreshes every **2 seconds**.
- **Featherweight** — a single Python file on the standard library only (plus the Docker CLI), ~20 MB RAM, ~0% CPU at rest.
- **Read-only by design** — it cannot start, stop or change anything. It only looks.

## Quick start

Requires a Linux host with Docker and Docker Compose.

```bash
git clone https://github.com/nishadnyc/docker-system-stats.git
cd docker-system-stats
docker compose up -d --build
```

Then open **http://localhost:8095**.

The default compose publishes the panel on `127.0.0.1` only. To reach it from your LAN or a VPN such as Tailscale, change the port mapping in `docker-compose.yml`, for example:

```yaml
ports:
  - "100.x.y.z:8095:8095"   # your Tailscale IP
```

### CasaOS / ReCasaOS

A ready-made CasaOS-format compose is included:

```bash
cp docker-compose.casaos.yml docker-compose.yml   # adjust the data paths inside first
```

It installs as a proper app with the title **System Stats**.

## How it works

```
Browser ──HTTP──▶ server.py ──docker CLI──▶ Docker daemon (via docker.sock)
   ▲                  │
   └── SSE log stream ┘         host metrics from /proc (bind-mounted read-only)
```

- `app/server.py` — a standard-library Python HTTP server. It shells out to `docker ps`, `docker stats` and `docker inspect` for container data, reads `/host/proc` for host CPU/memory/network, and proxies `docker logs -f` to the browser as an SSE stream.
- `app/index.html` — the entire UI: one file, vanilla JS, no build step, no framework.
- Host disk usage comes from a read-only bind of `/` at `/hostfs`.

## Configuration

| Setting | How | Default |
|---|---|---|
| Port inside the container | `PORT` environment variable | `8095` |
| Published address/port | `ports:` in `docker-compose.yml` | `127.0.0.1:8095` |
| Dashboard refresh | `setInterval` in `app/index.html` | 5 s |
| Detail refresh | `setInterval` in `app/index.html` | 2 s |

## Security notes

- The panel mounts the Docker socket to read stats and logs. That socket is powerful — **never expose this app directly to the public internet.** Bind it to localhost or a private network (LAN / Tailscale / WireGuard), or put it behind an authenticated reverse proxy.
- There is intentionally **no built-in authentication**; the bind address is the security boundary.
- All mounts except the Docker socket are read-only, and the app itself exposes no control operations.

## Project layout

```
.
├── app/
│   ├── server.py        # the whole backend (~300 lines, stdlib only)
│   └── index.html       # the whole frontend (one file, vanilla JS)
├── assets/logo.svg
├── Dockerfile
├── docker-compose.yml          # generic install
└── docker-compose.casaos.yml   # CasaOS / ReCasaOS install
```

## Roadmap

- Optional start / stop / restart controls (behind an explicit opt-in flag)
- History sparklines for CPU and memory
- Container grouping and search

## License

[MIT](LICENSE) — do whatever you like with it.
