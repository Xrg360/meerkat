import unittest
from dataclasses import dataclass
from unittest.mock import patch

from monitors.actions import ActionService
from monitors.alerts import AlertManager
from monitors.autofix import AutoHealMonitor
from monitors.network import InterfaceStatus


class FakeState:
    def __init__(self) -> None:
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        return True


class FakeHistory:
    def __init__(self) -> None:
        self.alerts = []

    def record(self, alert):
        self.alerts.append(alert)


class FakeNotifier:
    def __init__(self) -> None:
        self.alerts = []

    def send_alert(self, alert, force=False):
        self.alerts.append((alert, force))


class FakeAutoHealAlerts:
    def __init__(self) -> None:
        self.events = []

    def event(self, **kwargs):
        self.events.append(kwargs)


class FakeAutoHealActions:
    def __init__(self) -> None:
        self.blocked_containers = {"meerkat"}
        self.started = []
        self.restarted_interfaces = []

    def start_container(self, name):
        self.started.append(name)
        return {"ok": True, "message": f"started {name}"}

    def restart_network_interface(self, interface):
        self.restarted_interfaces.append(interface)
        return {"ok": True, "message": f"restarted {interface}"}


@dataclass
class FakeContainer:
    name: str
    status: str


class AlertManagerTests(unittest.TestCase):
    def make_manager(self):
        state = FakeState()
        history = FakeHistory()
        notifier = FakeNotifier()
        manager = AlertManager({"alerting": {"duration": 0, "cooldown": 900}}, state, notifier, history)
        return manager, state, notifier, history

    def test_condition_alert_and_recovery(self):
        manager, _state, notifier, history = self.make_manager()

        manager.condition("cpu.high", "cpu", True, "warning", "CPU high", "bad", "ok")
        manager.condition("cpu.high", "cpu", False, "warning", "CPU high", "bad", "ok")

        self.assertEqual([entry[0].status for entry in notifier.alerts], ["active", "recovered"])
        self.assertEqual([alert.status for alert in history.alerts], ["active", "recovered"])

    def test_event_dedupe(self):
        manager, _state, notifier, history = self.make_manager()

        manager.event("docker.foo.start", "docker", "info", "started", "body", dedupe_id="same")
        manager.event("docker.foo.start", "docker", "info", "started", "body", dedupe_id="same")

        self.assertEqual(len(notifier.alerts), 1)
        self.assertEqual(len(history.alerts), 1)

    def test_force_event_reaches_notifier(self):
        manager, _state, notifier, _history = self.make_manager()

        manager.event("meerkat.boot", "meerkat", "info", "boot", "body", force=True)

        self.assertTrue(notifier.alerts[0][1])


class ActionServiceTests(unittest.TestCase):
    def test_blocks_meerkat_restart_by_default(self):
        service = ActionService({"actions": {"enabled": True}}, FakeState(), None)

        result = service.restart_container("meerkat")

        self.assertFalse(result["ok"])
        self.assertIn("blocked", result["error"])


class AutoHealMonitorTests(unittest.TestCase):
    def make_monitor(self):
        state = FakeState()
        alerts = FakeAutoHealAlerts()
        actions = FakeAutoHealActions()
        config = {
            "network": {"ethernet": "eth0", "wifi": "wlan0"},
            "auto_heal": {"enabled": True, "interval": 300, "repair_cooldown": 0},
        }
        monitor = AutoHealMonitor(config, state, alerts, actions)
        return monitor, state, alerts, actions

    def test_tracks_running_containers_before_restarting_down_ones(self):
        monitor, state, alerts, actions = self.make_monitor()

        with patch("monitors.autofix.docker.from_env") as docker_from_env:
            docker_from_env.return_value.containers.list.return_value = [
                FakeContainer("web", "running"),
                FakeContainer("db", "exited"),
            ]
            monitor._heal_containers()
            docker_from_env.return_value.containers.list.return_value = [
                FakeContainer("web", "exited"),
                FakeContainer("db", "exited"),
            ]
            monitor._heal_containers()

        self.assertEqual(actions.started, ["web"])
        self.assertEqual(state.get("auto_heal.containers.active"), ["web"])
        self.assertEqual(alerts.events[0]["alert_id"], "auto_heal.container.web")

    def test_does_not_restart_blocked_container(self):
        monitor, state, _alerts, actions = self.make_monitor()
        state.set("auto_heal.containers.active", ["meerkat"])

        with patch("monitors.autofix.docker.from_env") as docker_from_env:
            docker_from_env.return_value.containers.list.return_value = [FakeContainer("meerkat", "exited")]
            monitor._heal_containers()

        self.assertEqual(actions.started, [])

    def test_restarts_previously_active_network_interface(self):
        monitor, state, _alerts, actions = self.make_monitor()

        with patch("monitors.autofix.get_interface_status") as interface_status:
            interface_status.side_effect = [
                InterfaceStatus("eth0", "up", True, True, True),
                InterfaceStatus("wlan0", "missing", False, False, False),
                InterfaceStatus("eth0", "down", False, False, False),
                InterfaceStatus("wlan0", "missing", False, False, False),
            ]
            monitor._heal_network()
            monitor._heal_network()

        self.assertEqual(actions.restarted_interfaces, ["eth0"])
        self.assertEqual(state.get("auto_heal.network.active"), ["eth0"])


if __name__ == "__main__":
    unittest.main()
