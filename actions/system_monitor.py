"""
System Monitor — background metric checks with voice alert support.
Works on Windows, macOS, and Linux without external APIs.
"""
import platform
import subprocess
import time

import psutil

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

DEFAULT_THRESHOLDS = {
    "cpu":  90.0,   # %  — fires after _CPU_STREAK consecutive readings
    "ram":  90.0,   # %
    "temp": 85.0,   # °C
    "gpu":  95.0,   # %
}

_COOLDOWN   = 300   # seconds between same-type alerts (5 min)
_CPU_STREAK = 3     # consecutive high readings required before CPU alert


def _get_gpu_usage() -> float:
    # NVIDIA
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            vals = [float(v.strip()) for v in r.stdout.strip().split("\n") if v.strip()]
            return sum(vals) / len(vals) if vals else -1.0
    except Exception:
        pass

    # AMD (Linux)
    if _OS == "Linux":
        try:
            r = subprocess.run(
                ["rocm-smi", "--showuse", "--csv"],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode == 0:
                for line in r.stdout.strip().split("\n"):
                    parts = line.split(",")
                    if len(parts) >= 2:
                        try:
                            return float(parts[1].strip().replace("%", ""))
                        except ValueError:
                            pass
        except Exception:
            pass

    return -1.0


def _get_cpu_temp() -> float:
    # psutil sensors (Linux + some Windows drivers)
    try:
        temps = psutil.sensors_temperatures()
        for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                     "cpu-thermal", "zenpower", "it8688"]:
            if name in temps and temps[name]:
                return temps[name][0].current
        for entries in temps.values():
            if entries:
                return entries[0].current
    except Exception:
        pass

    # Windows — WMI thermal zone
    if _OS == "Windows":
        try:
            r = subprocess.run(
                ["powershell", "-Command",
                 "(Get-WmiObject MSAcpi_ThermalZoneTemperature "
                 "-Namespace root/wmi).CurrentTemperature"],
                capture_output=True, text=True, timeout=3,
            )
            if r.returncode == 0 and r.stdout.strip():
                raw = float(r.stdout.strip().split("\n")[0])
                return (raw / 10.0) - 273.15
        except Exception:
            pass

    # macOS — osx-cpu-temp (optional CLI tool)
    if _OS == "Darwin":
        try:
            r = subprocess.run(
                ["osx-cpu-temp"], capture_output=True, text=True, timeout=2,
            )
            if r.returncode == 0:
                import re
                m = re.search(r"([\d.]+)", r.stdout)
                if m:
                    return float(m.group(1))
        except Exception:
            pass

    return -1.0


def get_system_status() -> dict:
    """
    Snapshot of current system metrics.
    Used by the system_status tool so the user can ask verbally.
    """
    cpu  = psutil.cpu_percent(interval=0.2)
    ram  = psutil.virtual_memory()
    temp = _get_cpu_temp()
    gpu  = _get_gpu_usage()

    boot_time   = psutil.boot_time()
    uptime_secs = time.time() - boot_time
    uptime_h    = int(uptime_secs // 3600)
    uptime_m    = int((uptime_secs % 3600) // 60)

    return {
        "cpu_percent":   round(cpu, 1),
        "ram_percent":   round(ram.percent, 1),
        "ram_used_gb":   round(ram.used   / 1024 ** 3, 1),
        "ram_total_gb":  round(ram.total  / 1024 ** 3, 1),
        "cpu_temp_c":    round(temp, 1) if temp > 0 else None,
        "gpu_percent":   round(gpu,  1) if gpu  >= 0 else None,
        "uptime":        f"{uptime_h}h {uptime_m}m",
        "process_count": len(psutil.pids()),
    }


class SystemMonitor:
    """
    Stateful monitor — cooldown state persists across session reconnections.
    Call check() periodically; it returns a [SYSTEM_ALERT] string when a
    threshold is exceeded, or None when everything is fine.
    """

    def __init__(self, thresholds: dict | None = None):
        self.thresholds   = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._last_alert: dict[str, float] = {}
        self._cpu_streak  = 0

    def _can_alert(self, key: str) -> bool:
        return (time.monotonic() - self._last_alert.get(key, 0)) > _COOLDOWN

    def _record(self, key: str):
        self._last_alert[key] = time.monotonic()

    def check(self) -> str | None:
        """
        Reads current metrics synchronously.
        Returns an instruction string for Jarvis to speak, or None.
        """
        try:
            cpu  = psutil.cpu_percent(interval=None)
            ram  = psutil.virtual_memory().percent
            temp = _get_cpu_temp()
            gpu  = _get_gpu_usage()
        except Exception:
            return None

        alerts: list[str] = []

        # CPU — require consecutive readings to avoid transient spikes
        if cpu >= self.thresholds["cpu"]:
            self._cpu_streak += 1
            if self._cpu_streak >= _CPU_STREAK and self._can_alert("cpu"):
                alerts.append(
                    f"[SYSTEM_ALERT] CPU usage has been critically high ({cpu:.0f}%) "
                    "for several seconds. Warn the user in their language and suggest "
                    "closing heavy applications."
                )
                self._record("cpu")
                self._cpu_streak = 0
        else:
            self._cpu_streak = 0

        if ram >= self.thresholds["ram"] and self._can_alert("ram"):
            alerts.append(
                f"[SYSTEM_ALERT] RAM is at {ram:.0f}% — nearly exhausted. "
                "Warn the user in their language and suggest freeing memory."
            )
            self._record("ram")

        if temp > 0 and temp >= self.thresholds["temp"] and self._can_alert("temp"):
            alerts.append(
                f"[SYSTEM_ALERT] CPU temperature is {temp:.0f}°C — above the safe limit. "
                "Warn the user in their language and advise reducing system load "
                "or checking cooling."
            )
            self._record("temp")

        if gpu >= 0 and gpu >= self.thresholds["gpu"] and self._can_alert("gpu"):
            alerts.append(
                f"[SYSTEM_ALERT] GPU load is at {gpu:.0f}%. "
                "Briefly inform the user in their language."
            )
            self._record("gpu")

        return " ".join(alerts) if alerts else None
