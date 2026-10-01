"""Measure the connection: latency, download and upload.

Written in this project's plugin format so it is a drop-in tool like any other,
and deliberately dependency-free beyond `requests` — a speed test that needs a
package nobody has installed is a speed test that fails on a fresh machine.

Method: Cloudflare's public speed endpoints, which need no key and are the ones
`speed.cloudflare.com` itself uses. Latency is the round trip of a tiny request,
download is a timed 3 MB fetch, upload is a timed 1 MB post. Sizes are chosen for
a spoken answer rather than a benchmark chart: big enough to be meaningful on a
normal line, small enough that a slow connection still finishes inside the
timeout instead of being reported as an error.
"""

import time

PLUGIN = {
    "name": "internet_speed_test",
    "description": (
        "Measures internet speed: latency in milliseconds, download and upload in "
        "megabits per second. Trigger phrases: 'test my internet speed', 'how fast "
        "is my internet', 'run a speed test', 'check my download speed', 'wifi "
        "speed'. Use this whenever the user asks how fast their connection is or "
        "whether their internet is working properly. Do NOT use it to check "
        "whether a specific website is reachable — use web_search for that."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "include_upload": {
                "type": "BOOLEAN",
                "description": (
                    "Measure upload as well as download. Defaults to true; set "
                    "false when the user only cares about download speed or is on "
                    "a slow line and wants a quicker answer."
                ),
            },
        },
        "required": [],
    },
}

_DOWN_BYTES = 3_000_000
_UP_BYTES = 1_000_000
_PING_URL = "https://speed.cloudflare.com/__down?bytes=1000"
_DOWN_URL = "https://speed.cloudflare.com/__down?bytes={n}"
_UP_URL = "https://speed.cloudflare.com/__up"


def _mbps(byte_count: int, seconds: float) -> float:
    if seconds <= 0:
        return 0.0
    return round((byte_count * 8) / (seconds * 1_000_000), 1)


def run(parameters: dict, player=None, session_memory=None) -> str:
    parameters = parameters if isinstance(parameters, dict) else {}
    want_upload = parameters.get("include_upload")
    want_upload = True if want_upload is None else bool(want_upload)

    try:
        import requests
    except Exception as e:
        return f"I cannot measure the connection because requests is missing: {e}"

    session = requests.Session()
    session.headers.update({"User-Agent": "ICE-JARVIS/1.0 speed test"})

    # The probe answers two questions at once: how long a round trip takes, and —
    # more importantly — whether the speed test servers are reachable at all. If
    # they are not, there is nothing to measure, so this is the one place that
    # reports an outright failure rather than a partial reading.
    ping = 0.0
    try:
        started = time.time()
        probe = session.get(_PING_URL, timeout=8)
        if probe.status_code >= 400:
            return (f"I could not reach the speed test servers — they answered "
                    f"{probe.status_code} — so the connection may be down or "
                    f"blocked here.")
        ping = round((time.time() - started) * 1000)
    except Exception as e:
        return (f"I could not reach the speed test servers, so the connection may "
                f"be down: {e}")

    download = None
    down_error = ""
    try:
        started = time.time()
        response = session.get(_DOWN_URL.format(n=_DOWN_BYTES), timeout=25)
        elapsed = time.time() - started
        if response.status_code < 400 and response.content:
            download = _mbps(len(response.content), elapsed)
        else:
            down_error = f"the server answered {response.status_code}"
    except Exception as e:
        down_error = str(e)

    upload = None
    up_error = ""
    if want_upload:
        try:
            payload = b"0" * _UP_BYTES
            started = time.time()
            response = session.post(_UP_URL, data=payload, timeout=25)
            elapsed = time.time() - started
            if response.status_code < 400 and elapsed > 0:
                upload = _mbps(_UP_BYTES, elapsed)
            else:
                up_error = f"the server answered {response.status_code}"
        except Exception as e:
            up_error = str(e)

    if download is None:
        # "latency", not "answered in", so the one measurement keeps one name
        # wherever it appears.
        return (f"The latency is {int(ping)} milliseconds, but the download test "
                f"itself did not finish: {down_error}.")

    parts = [f"Your download speed is {download:g} megabits per second"]
    if upload is not None:
        parts.append(f"upload is {upload:g} megabits per second")
    # Always reported when the probe answered, even as "0 milliseconds": a
    # latency that rounds away is still a fact about the connection, and dropping
    # it would make the answer look like the measurement had failed.
    parts.append(f"latency is {int(ping)} milliseconds")
    spoken = ", ".join(parts) + "."

    quality = _quality(download, ping)
    spoken += f" {quality}"
    if up_error and want_upload:
        spoken += f" (The upload test failed: {up_error}.)"

    if player:
        try:
            player.write_log(f"JARVIS: {spoken}")
        except Exception:
            pass
    return spoken


def _quality(download: float, ping) -> str:
    """One plain sentence about whether that number is good, because the numbers
    alone mean nothing to most people."""
    try:
        ping = float(ping) if ping is not None else 0.0
    except Exception:
        ping = 0.0
    if download >= 100:
        speed = "That is a fast connection"
    elif download >= 25:
        speed = "That is plenty for streaming and video calls"
    elif download >= 5:
        speed = "That is workable, but high-definition video will struggle"
    else:
        speed = "That is slow"
    if ping and ping > 150:
        speed += ", though the latency is high enough to feel sluggish"
    return speed + "."
