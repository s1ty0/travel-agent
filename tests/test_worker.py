from types import SimpleNamespace
from unittest.mock import Mock

from travel_agent import worker


def test_once_worker_honors_live_flag_without_touching_providers(monkeypatch):
    execute, shutdown, maintain = Mock(return_value=True), Mock(), Mock()
    manager = SimpleNamespace(
        execute=execute,
        pool=SimpleNamespace(shutdown=shutdown),
        store=SimpleNamespace(maintenance=maintain),
    )
    monkeypatch.setattr(worker, "tasks", manager)
    monkeypatch.setattr(worker.signal, "signal", lambda *args: None)
    monkeypatch.setattr("sys.argv", ["worker", "--once"])
    monkeypatch.setattr(worker, "live_enabled", lambda: False)
    worker.main()
    execute.assert_not_called()
    maintain.assert_called_once()
    shutdown.assert_called_once_with(wait=True)
    monkeypatch.setattr(worker, "live_enabled", lambda: True)
    worker.main()
    execute.assert_called_once_with()
