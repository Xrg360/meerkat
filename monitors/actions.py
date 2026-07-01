import platform
import subprocess
from urllib.parse import urlparse
from typing import Any


class ActionService:
    def __init__(self, config: dict[str, Any], state: Any, history: Any) -> None:
        self.config = config
        self.state = state
        self.history = history
        action_config = config.get("actions", {}) or {}
        self.enabled = bool(action_config.get("enabled", True))
        self.blocked_containers = set(action_config.get("blocked_containers") or ["meerkat"])

    def clear_ram_cache(self) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "actions are disabled"}
        if platform.system().lower() != "linux":
            return {"ok": False, "error": "clear RAM cache is only supported on Linux"}

        try:
            subprocess.run(["sync"], check=True, timeout=10)
            subprocess.run(
                ["sh", "-c", "echo 3 > /proc/sys/vm/drop_caches"],
                check=True,
                timeout=10,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            return {"ok": False, "error": (exc.stderr or str(exc)).strip()}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "error": str(exc)}

        return {"ok": True, "message": "Linux page cache, dentries, and inodes were dropped"}

    def restart_container(self, name: str) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "actions are disabled"}
        name = name.strip()
        if not name:
            return {"ok": False, "error": "container name is required"}
        if name in self.blocked_containers:
            return {"ok": False, "error": f"container restart is blocked: {name}"}

        try:
            import docker

            client = docker.from_env()
            container = client.containers.get(name)
            container.restart(timeout=10)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        return {"ok": True, "message": f"Container restarted: {name}"}

    def add_site(self, site: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "actions are disabled"}

        name = str(site.get("name") or "").strip()
        url = str(site.get("url") or "").strip()
        parsed = urlparse(url)
        if not name:
            return {"ok": False, "error": "site name is required"}
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return {"ok": False, "error": "site URL must start with http:// or https://"}

        runtime_sites = list(self.state.get("sites.custom", []))
        normalized = {
            "name": name,
            "url": url,
            "expected_status": site.get("expected_status") or [200],
            "timeout": float(site.get("timeout", 10)),
            "follow_redirects": bool(site.get("follow_redirects", True)),
            "severity": site.get("severity", "critical"),
            "duration": site.get("duration", "30s"),
            "cooldown": site.get("cooldown", "15m"),
        }
        keyword = str(site.get("keyword") or "").strip()
        if keyword:
            normalized["keyword"] = keyword

        runtime_sites = [existing for existing in runtime_sites if str(existing.get("name", "")).lower() != name.lower()]
        runtime_sites.append(normalized)
        self.state.set("sites.custom", runtime_sites)
        return {"ok": True, "message": f"Site monitor saved: {name}"}

    def remove_site(self, name: str) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "actions are disabled"}
        name = name.strip()
        if not name:
            return {"ok": False, "error": "site name is required"}

        runtime_sites = list(self.state.get("sites.custom", []))
        remaining = [site for site in runtime_sites if str(site.get("name", "")).lower() != name.lower()]
        if len(remaining) == len(runtime_sites):
            return {"ok": False, "error": f"runtime site not found: {name}"}
        self.state.set("sites.custom", remaining)
        return {"ok": True, "message": f"Site monitor removed: {name}"}

    def clear_events(self) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "actions are disabled"}
        self.history.clear()
        return {"ok": True, "message": "Recent events cleared"}
