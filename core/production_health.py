"""Bounded, privacy-preserving production health observation."""

import datetime
import json
import os
import stat
import threading
import urllib.error
import urllib.parse
import urllib.request


CONFIG_VERSION = 1
CONFIG_MAX_BYTES = 8 * 1024
ENDPOINT_MAX_BYTES = 2048
DEFAULT_INTERVAL_SECONDS = 60
MIN_INTERVAL_SECONDS = 30
MAX_INTERVAL_SECONDS = 3600
REQUEST_TIMEOUT_SECONDS = 5
FAILURE_THRESHOLD = 3
STALE_MULTIPLIER = 3


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(value):
    if not isinstance(value, datetime.datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc).isoformat()


def validate_endpoint(value, allow_loopback_http=False):
    """Return a canonical health endpoint or raise ValueError."""
    if (not isinstance(value, str) or not value or
            len(value.encode("utf-8")) > ENDPOINT_MAX_BYTES or
            any(character.isspace() or ord(character) < 32 or ord(character) == 127
                for character in value)):
        raise ValueError("production health endpoint must be a bounded URL")
    if not value.isascii():
        raise ValueError("production health endpoint must use an ASCII hostname")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("production health endpoint is invalid") from exc
    loopback_http = (
        allow_loopback_http and parsed.scheme == "http" and
        parsed.hostname in ("127.0.0.1", "localhost")
    )
    if parsed.scheme != "https" and not loopback_http:
        raise ValueError("production health endpoint must use HTTPS")
    if (not parsed.hostname or parsed.username is not None or parsed.password is not None or
            parsed.query or parsed.fragment):
        raise ValueError(
            "production health endpoint cannot contain credentials, a query, or a fragment")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("production health endpoint port is invalid")
    path = parsed.path or "/"
    return urllib.parse.urlunsplit((
        parsed.scheme, parsed.netloc.lower(), path, "", "",
    ))


def _read_private_config(path, allow_loopback_http=False):
    if not isinstance(path, str) or not os.path.isabs(path):
        raise ValueError("production monitor config path must be absolute")
    canonical = os.path.realpath(path)
    if canonical != os.path.abspath(path):
        raise ValueError("production monitor config cannot use symlinks")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(canonical, flags)
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_size > CONFIG_MAX_BYTES or
                stat.S_IMODE(metadata.st_mode) & 0o077):
            raise ValueError("production monitor config must be one bounded regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(CONFIG_MAX_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(raw) > CONFIG_MAX_BYTES:
        raise ValueError("production monitor config is too large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("production monitor config is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("production monitor config must be an object")
    unknown = sorted(set(value) - {"version", "endpoint", "intervalSeconds"})
    if unknown:
        raise ValueError("production monitor config has unsupported fields")
    if value.get("version") != CONFIG_VERSION:
        raise ValueError("production monitor config has an unsupported version")
    interval = value.get("intervalSeconds", DEFAULT_INTERVAL_SECONDS)
    if (not isinstance(interval, int) or isinstance(interval, bool) or
            not MIN_INTERVAL_SECONDS <= interval <= MAX_INTERVAL_SECONDS):
        raise ValueError("production monitor interval is outside the supported range")
    return {
        "version": CONFIG_VERSION,
        "endpoint": validate_endpoint(
            value.get("endpoint"), allow_loopback_http=allow_loopback_http),
        "intervalSeconds": interval,
    }


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        del request, file_pointer, code, message, headers, new_url
        return None


def _default_probe(endpoint):
    opener = urllib.request.build_opener(_NoRedirectHandler())
    request = urllib.request.Request(
        endpoint,
        headers={
            "Accept": "application/json",
            "User-Agent": "HFLedger-production-monitor/1",
        },
        method="GET",
    )
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status_code = response.getcode()
            response.read(1)
        return isinstance(status_code, int) and 200 <= status_code < 300
    except urllib.error.HTTPError as exc:
        exc.close()
        return False
    except (OSError, ValueError, urllib.error.URLError):
        return False


class ProductionHealthMonitor:
    """Observe one explicit endpoint without retaining its URL or response in output."""

    def __init__(self, config_path, now_fn=None, probe_fn=None, autostart=True,
                 allow_loopback_http=False):
        self.config = _read_private_config(
            config_path, allow_loopback_http=allow_loopback_http)
        self._now_fn = now_fn or _utc_now
        self._probe_fn = probe_fn or _default_probe
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._last_checked_at = None
        self._last_healthy_at = None
        self._consecutive_failures = 0
        if autostart:
            self.start()

    @property
    def interval_seconds(self):
        return self.config["intervalSeconds"]

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="hfledger-production-monitor",
            daemon=True,
        )
        self._thread.start()

    def close(self):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)

    def _run(self):
        while not self._stop.is_set():
            self.check_once()
            if self._stop.wait(self.interval_seconds):
                return

    def check_once(self):
        try:
            success = bool(self._probe_fn(self.config["endpoint"]))
        except Exception:  # pragma: no cover - final containment for the daemon thread
            success = False
        checked_at = self._now_fn()
        if not isinstance(checked_at, datetime.datetime):
            raise ValueError("production monitor clock must return a datetime")
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=datetime.timezone.utc)
        checked_at = checked_at.astimezone(datetime.timezone.utc)
        with self._lock:
            self._last_checked_at = checked_at
            if success:
                self._last_healthy_at = checked_at
                self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1
        return success

    def snapshot(self, now=None):
        now = now or self._now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)
        now = now.astimezone(datetime.timezone.utc)
        with self._lock:
            checked_at = self._last_checked_at
            healthy_at = self._last_healthy_at
            failures = self._consecutive_failures

        stale_after = datetime.timedelta(
            seconds=self.interval_seconds * STALE_MULTIPLIER)
        stale = checked_at is not None and now - checked_at > stale_after
        if stale:
            state = "degraded"
            summary = "Production monitoring has stopped updating."
            monitor_state = "stale"
        elif checked_at is None:
            state = "degraded"
            summary = "Production monitoring is starting."
            monitor_state = "starting"
        elif failures >= FAILURE_THRESHOLD:
            state = "degraded"
            summary = "The live service is not responding to its health check."
            monitor_state = "degraded"
        elif healthy_at is not None:
            state = "healthy"
            summary = "The live service is responding normally."
            monitor_state = "retrying" if failures else "active"
        else:
            state = "degraded"
            summary = "Production monitoring is retrying the live service."
            monitor_state = "retrying"

        return {
            "state": state,
            "summary": summary,
            "monitorState": monitor_state,
            "lastCheckedAt": _iso(checked_at),
            "lastHealthyAt": _iso(healthy_at),
        }
