#!/usr/bin/env python3
"""Write this host's CPU, memory, disk, temperature, GPU and Docker counts to a JSON file.

Designed for a container with no network: only numbers and the GPU model leave it.
Settings come from environment variables (defaults suit the provided docker-compose.yml):

  STATS_OUT       output file                      /shared/host.json
  STATS_PROC      host /proc mount                 /host/proc
  STATS_DISK      path whose filesystem is measured /host/root
  STATS_INTERVAL  seconds between samples          20
  DOCKER_SOCKET   Docker API socket                /var/run/docker.sock

Run with --once to print one sample to stdout and exit.
"""
import glob
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import time

OUT = os.environ.get("STATS_OUT", "/shared/host.json")
PROC = os.environ.get("STATS_PROC", "/host/proc")
DISK = os.environ.get("STATS_DISK", "/host/root")
INTERVAL = int(os.environ.get("STATS_INTERVAL", "20"))
DOCKER_SOCKET = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
CPU_SENSORS = ("coretemp", "k10temp", "zenpower", "cpu_thermal")


class DockerSocket(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect(DOCKER_SOCKET)


def docker_state():
    """Container counts only, or None values when Docker is not reachable."""
    try:
        connection = DockerSocket("docker")
        connection.request("GET", "/containers/json?all=1")
        containers = json.load(connection.getresponse())
        connection.close()
    except OSError:
        return {"running": None, "stopped": None, "failing": None}
    states = [(c["State"], c.get("Status", "")) for c in containers]
    return {
        "running": sum(1 for state, _ in states if state == "running"),
        "stopped": sum(1 for state, _ in states if state == "exited"),
        "failing": sum(1 for state, status in states if state == "restarting" or "(unhealthy)" in status),
    }


def cpu_times():
    with open(f"{PROC}/stat") as f:
        values = [int(v) for v in f.readline().split()[1:]]
    return sum(values), values[3] + values[4]


def memory():
    info = {}
    with open(f"{PROC}/meminfo") as f:
        for line in f:
            key, value = line.split(":", 1)
            info[key] = int(value.split()[0])
    total, available = info["MemTotal"], info["MemAvailable"]
    return {"mem_percent": round((total - available) / total * 100, 1),
            "mem_used_gb": round((total - available) / 1048576, 1),
            "mem_total_gb": round(total / 1048576, 1)}


def disk():
    stat = os.statvfs(DISK)
    used, free = (stat.f_blocks - stat.f_bfree) * stat.f_frsize, stat.f_bavail * stat.f_frsize
    return {"disk_percent": round(used / max(used + free, 1) * 100, 1),
            "disk_used_gb": round(used / 1e9), "disk_total_gb": round((used + free) / 1e9)}


def temperature():
    readings = {}
    for path in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            with open(f"{path}/name") as f:
                name = f.read().strip()
            with open(f"{path}/temp1_input") as f:
                readings[name] = int(f.read()) / 1000
        except (OSError, ValueError):
            continue
    for name in CPU_SENSORS:
        if name in readings:
            return round(readings[name])
    return round(max(readings.values())) if readings else None


def gpu():
    """NVIDIA stats through nvidia-smi, or None on hosts without it."""
    if not shutil.which("nvidia-smi"):
        return None
    query = "name,utilization.gpu,memory.used,memory.total,temperature.gpu"
    try:
        result = subprocess.run(["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, timeout=10, check=True)
        name, busy, used, total, temp = (part.strip() for part in result.stdout.splitlines()[0].split(","))
        return {"name": name, "util": float(busy), "mem_used_mb": int(used), "mem_total_mb": int(total),
                "temp": int(temp)}
    except (OSError, ValueError, IndexError, subprocess.SubprocessError) as error:
        print(f"gpu read failed: {error}", file=sys.stderr, flush=True)
        return None


def uptime():
    with open(f"{PROC}/uptime") as f:
        return int(float(f.read().split()[0]))


def sample(cpu):
    return {"ts": time.time(), "cpu": cpu, "temp": temperature(), "uptime_s": uptime(), "gpu": gpu(),
            **memory(), **disk(), **docker_state()}


def cpu_percent(previous, current):
    (total0, idle0), (total1, idle1) = previous, current
    return round((1 - (idle1 - idle0) / max(total1 - total0, 1)) * 100, 1)


def main():
    if "--once" in sys.argv:
        first = cpu_times()
        time.sleep(1)
        print(json.dumps(sample(cpu_percent(first, cpu_times())), indent=1))
        return
    previous = cpu_times()
    while True:
        time.sleep(INTERVAL)
        current = cpu_times()
        try:
            state = sample(cpu_percent(previous, current))
            with open(f"{OUT}.tmp", "w") as f:
                json.dump(state, f)
            os.replace(f"{OUT}.tmp", OUT)
        except Exception as error:
            print(f"collect failed: {error}", file=sys.stderr, flush=True)
        previous = current


if __name__ == "__main__":
    main()
