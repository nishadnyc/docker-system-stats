#!/usr/bin/env python3
"""Server Panel - a tiny read-only live view of Docker containers + host.

Stdlib only. Talks to Docker through the docker CLI (image ships docker-cli)
and reads host metrics from /host/proc (host /proc bind-mounted read-only).
"""
import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8095"))
HERE = os.path.dirname(os.path.abspath(__file__))
SAFE_ID = re.compile(r"^[a-zA-Z0-9_.\-]+$")

# ---------------------------------------------------------------- host stats
_prev = {"cpu": None, "net": None, "t": 0.0}
_prev_lock = threading.Lock()


def _read(path):
    with open(path, "r", errors="replace") as f:
        return f.read()


def host_cpu_times():
    for line in _read("/host/proc/stat").splitlines():
        if line.startswith("cpu "):
            parts = [int(x) for x in line.split()[1:]]
            idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
            return sum(parts), idle
    return 0, 0


def host_net_bytes():
    rx = tx = 0
    for line in _read("/host/proc/net/dev").splitlines():
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        name = name.strip()
        if name == "lo":
            continue
        fields = rest.split()
        rx += int(fields[0])
        tx += int(fields[8])
    return rx, tx


def host_info():
    mem = {}
    for line in _read("/host/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        mem[k.strip()] = int(v.strip().split()[0]) * 1024  # kB -> bytes
    mem_total = mem.get("MemTotal", 0)
    mem_used = mem_total - mem.get("MemAvailable", 0)
    load = _read("/host/proc/loadavg").split()[:3]
    uptime_s = int(float(_read("/host/proc/uptime").split()[0]))
    try:
        st = os.statvfs("/hostfs")
        disk_total = st.f_blocks * st.f_frsize
        disk_used = disk_total - st.f_bavail * st.f_frsize
    except OSError:
        disk_total = disk_used = 0

    total, idle = host_cpu_times()
    rx, tx = host_net_bytes()
    now = time.time()
    cpu_pct = 0.0
    rx_rate = tx_rate = 0.0
    with _prev_lock:
        if _prev["cpu"] is not None and now > _prev["t"]:
            dt = now - _prev["t"]
            d_total = total - _prev["cpu"][0]
            d_idle = idle - _prev["cpu"][1]
            if d_total > 0:
                cpu_pct = max(0.0, min(100.0, 100.0 * (1 - d_idle / d_total)))
            d_rx = rx - _prev["net"][0]
            d_tx = tx - _prev["net"][1]
            if d_rx >= 0 and d_tx >= 0:
                rx_rate, tx_rate = d_rx / dt, d_tx / dt
        _prev["cpu"], _prev["net"], _prev["t"] = (total, idle), (rx, tx), now

    return {
        "cpu_pct": round(cpu_pct, 1),
        "mem_used": mem_used,
        "mem_total": mem_total,
        "disk_used": disk_used,
        "disk_total": disk_total,
        "net_rx_rate": rx_rate,
        "net_tx_rate": tx_rate,
        "net_rx_total": rx,
        "net_tx_total": tx,
        "load": [float(x) for x in load],
        "uptime_s": uptime_s,
        "cpus": os.cpu_count() or 0,
    }


# ---------------------------------------------------------------- docker
def docker_json_lines(args):
    out = subprocess.run(
        ["docker"] + args, capture_output=True, text=True, timeout=30
    )
    rows = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


UNITS = {
    "B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4,
    "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4,
    "K": 1024, "M": 1024**2, "G": 1024**3,
}


def parse_size(s):
    m = re.match(r"([\d.]+)\s*([a-zA-Z]+)", s.strip())
    if not m:
        return 0
    return int(float(m.group(1)) * UNITS.get(m.group(2).upper(), 1))


def parse_pair(s):
    parts = s.split("/")
    if len(parts) != 2:
        return 0, 0
    return parse_size(parts[0]), parse_size(parts[1])


def parse_pct(s):
    try:
        return float(s.strip().rstrip("%"))
    except (ValueError, AttributeError):
        return 0.0


def containers_overview():
    ps = docker_json_lines(
        ["ps", "-a", "--format", "{{json .}}"]
    )
    stats = {}
    for row in docker_json_lines(
        ["stats", "--no-stream", "--format", "{{json .}}"]
    ):
        stats[row.get("Name", "")] = row
    result = []
    for c in ps:
        name = c.get("Names", "")
        s = stats.get(name, {})
        mem_used, mem_limit = parse_pair(s.get("MemUsage", "0B / 0B"))
        net_rx, net_tx = parse_pair(s.get("NetIO", "0B / 0B"))
        blk_r, blk_w = parse_pair(s.get("BlockIO", "0B / 0B"))
        result.append({
            "id": c.get("ID", "")[:12],
            "name": name,
            "image": c.get("Image", ""),
            "state": c.get("State", ""),
            "status": c.get("Status", ""),
            "ports": c.get("Ports", ""),
            "cpu_pct": parse_pct(s.get("CPUPerc", "0%")),
            "mem_used": mem_used,
            "mem_limit": mem_limit,
            "mem_pct": parse_pct(s.get("MemPerc", "0%")),
            "net_rx": net_rx,
            "net_tx": net_tx,
            "blk_r": blk_r,
            "blk_w": blk_w,
            "pids": int(s.get("PIDs", "0") or 0),
        })
    result.sort(key=lambda x: (x["state"] != "running", x["name"]))
    return result


def container_detail(cid):
    out = subprocess.run(
        ["docker", "inspect", cid], capture_output=True, text=True, timeout=15
    )
    data = json.loads(out.stdout)
    if not data:
        return None
    c = data[0]
    st = c.get("State", {})
    ports = []
    for cport, binds in (c.get("NetworkSettings", {}).get("Ports") or {}).items():
        if binds:
            for b in binds:
                ports.append(f"{b.get('HostIp', '')}:{b.get('HostPort', '')} -> {cport}")
        else:
            ports.append(cport)
    srows = docker_json_lines(
        ["stats", "--no-stream", cid, "--format", "{{json .}}"]
    )
    s = srows[0] if srows else {}
    mem_used, mem_limit = parse_pair(s.get("MemUsage", "0B / 0B"))
    net_rx, net_tx = parse_pair(s.get("NetIO", "0B / 0B"))
    blk_r, blk_w = parse_pair(s.get("BlockIO", "0B / 0B"))
    return {
        "id": c.get("Id", "")[:12],
        "name": c.get("Name", "").lstrip("/"),
        "image": c.get("Config", {}).get("Image", ""),
        "created": c.get("Created", ""),
        "started": st.get("StartedAt", ""),
        "state": st.get("Status", ""),
        "restarts": c.get("RestartCount", 0),
        "ports": ports,
        "cpu_pct": parse_pct(s.get("CPUPerc", "0%")),
        "mem_used": mem_used,
        "mem_limit": mem_limit,
        "mem_pct": parse_pct(s.get("MemPerc", "0%")),
        "net_rx": net_rx,
        "net_tx": net_tx,
        "blk_r": blk_r,
        "blk_w": blk_w,
        "pids": int(s.get("PIDs", "0") or 0),
    }


# ---------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        try:
            if path == "/" or path == "/index.html":
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            elif path == "/api/overview":
                conts = containers_overview()
                sysinfo = host_info()
                sysinfo["containers_running"] = sum(
                    1 for c in conts if c["state"] == "running"
                )
                sysinfo["containers_total"] = len(conts)
                self._send(200, json.dumps({"system": sysinfo, "containers": conts}))
            elif path.startswith("/api/container/"):
                cid = path[len("/api/container/"):]
                if not SAFE_ID.match(cid):
                    return self._send(400, json.dumps({"error": "bad id"}))
                detail = container_detail(cid)
                if detail is None:
                    return self._send(404, json.dumps({"error": "not found"}))
                detail["system"] = host_info()
                self._send(200, json.dumps(detail))
            elif path.startswith("/api/logs/"):
                cid = path[len("/api/logs/"):]
                if not SAFE_ID.match(cid):
                    return self._send(400, "bad id", "text/plain")
                self.stream_logs(cid)
            else:
                self._send(404, json.dumps({"error": "not found"}))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # keep the panel alive on any single failure
            try:
                self._send(500, json.dumps({"error": str(e)}))
            except Exception:
                pass

    def stream_logs(self, cid):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        proc = subprocess.Popen(
            ["docker", "logs", "-f", "--tail", "300", "--timestamps", cid],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            bufsize=1,
        )
        try:
            for line in proc.stdout:
                payload = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", line.rstrip("\n"))
                self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.kill()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
