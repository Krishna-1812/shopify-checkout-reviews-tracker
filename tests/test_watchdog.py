from datetime import datetime, timezone

from tracker import watchdog

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


def run(at, simulate=None):
    return {"started_at": at, "status": "ok", "simulate": simulate}


def test_recent_run_is_fine():
    stale, msg = watchdog.check([run("2026-11-21T03:20:00+00:00")], NOW, 36)
    assert not stale and "fine" in msg


def test_late_but_not_missed_is_fine():
    # yesterday's run started 9 h late: still within the window, no false alarm
    stale, _ = watchdog.check([run("2026-11-20T12:30:00+00:00")], NOW, 36)
    assert not stale


def test_missed_day_alerts():
    stale, msg = watchdog.check([run("2026-11-19T03:17:00+00:00")], NOW, 36)
    assert stale and "56.7 hours ago" in msg


def test_simulated_runs_do_not_count_as_alive():
    runs = [run("2026-11-18T03:17:00+00:00"), run("2026-11-21T10:00:00+00:00", simulate="empty_result")]
    assert watchdog.check(runs, NOW, 36)[0]


def test_no_runs_alerts():
    assert watchdog.check([], NOW, 36)[0]


def test_main_writes_output_and_alert(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setattr(watchdog.store, "load_runs", lambda: [run("2026-01-01T00:00:00+00:00")])
    monkeypatch.setattr(watchdog.config, "ROOT", tmp_path)
    assert watchdog.main([]) == 0
    assert "stale=true" in out.read_text()
    assert "has not run" in (tmp_path / "alert.md").read_text()


def test_forced_test_run_says_so():
    assert "Test of the watchdog alert" in watchdog.alert_markdown("x", 0)
    assert "has not run" in watchdog.alert_markdown("x", 36)
