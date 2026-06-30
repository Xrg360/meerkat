import unittest

from monitors.alerts import AlertManager


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


if __name__ == "__main__":
    unittest.main()
