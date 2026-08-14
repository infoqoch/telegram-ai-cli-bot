"""Tests for the opt-in macOS power manager shell boundary."""

from __future__ import annotations

import os
import plistlib
import stat
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POWER_MANAGER = PROJECT_ROOT / "scripts" / "macos" / "power_manager.sh"
INSTALLER = PROJECT_ROOT / "scripts" / "macos" / "install_power_manager.sh"
UNINSTALLER = PROJECT_ROOT / "scripts" / "macos" / "uninstall_power_manager.sh"
RUN_SCRIPT = PROJECT_ROOT / "run.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _power_env(tmp_path: Path, power: str = "ac") -> tuple[dict[str, str], dict[str, Path]]:
    state_dir = tmp_path / "state"
    data_dir = tmp_path / "data"
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    power_file = runtime_dir / "power"
    power_file.write_text(power, encoding="utf-8")
    actions_file = runtime_dir / "actions"
    notifications_file = runtime_dir / "notifications"
    running_file = runtime_dir / "running"
    worker_file = runtime_dir / "worker"
    degraded_file = runtime_dir / "degraded"

    pmset = tmp_path / "pmset"
    _write_executable(
        pmset,
        f"""#!/bin/bash
power=$(sed -n '1p' {power_file!s})
if [ "$1 $2" = "-g batt" ]; then
  case "$power" in
    ac) echo "Now drawing from 'AC Power'" ;;
    battery) echo "Now drawing from 'Battery Power'" ;;
    *) echo "Power source unknown" ;;
  esac
  exit 0
fi
if [ "$1 $2" = "-g pslog" ]; then
  echo "Now drawing from 'AC Power'"
  exit 0
fi
exit 1
""",
    )

    osascript = tmp_path / "osascript"
    _write_executable(
        osascript,
        f"""#!/bin/bash
printf '%s\\n' "$*" >> {notifications_file!s}
""",
    )

    run_script = tmp_path / "run.sh"
    _write_executable(
        run_script,
        f"""#!/bin/bash
case "$1" in
  _power-is-running) [ -f {running_file!s} ] ;;
  _power-runtime-state)
    if [ -f {running_file!s} ]; then
      echo running
    elif [ -f {degraded_file!s} ]; then
      echo degraded
    else
      echo stopped
    fi
    ;;
  _power-has-processes) [ -f {running_file!s} ] || [ -f {worker_file!s} ] ;;
  start)
    echo start >> {actions_file!s}
    touch {running_file!s}
    ;;
  stop-soft)
    echo stop-soft >> {actions_file!s}
    rm -f {running_file!s}
    ;;
  stop-hard)
    echo stop-hard >> {actions_file!s}
    rm -f {running_file!s} {worker_file!s}
    ;;
  *) exit 2 ;;
esac
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "BOT_POWER_PLATFORM": "Darwin",
            "BOT_POWER_PMSET_BIN": str(pmset),
            "BOT_POWER_OSASCRIPT_BIN": str(osascript),
            "BOT_POWER_STATE_DIR": str(state_dir),
            "BOT_DATA_DIR": str(data_dir),
            "BOT_LOG_DIR": str(data_dir / "logs"),
            "BOT_RUN_SCRIPT": str(run_script),
        }
    )
    paths = {
        "state": state_dir,
        "power": power_file,
        "actions": actions_file,
        "notifications": notifications_file,
        "running": running_file,
        "worker": worker_file,
        "degraded": degraded_file,
        "run_script": run_script,
    }
    return env, paths


def _run_power(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(POWER_MANAGER), *args],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _enable(env: dict[str, str], paths: dict[str, Path], desired: str) -> None:
    assert _run_power(env, "set-desired", desired).returncode == 0
    paths["state"].mkdir(parents=True, exist_ok=True)
    (paths["state"] / "enabled").touch()


def test_non_macos_power_manager_refuses_operation(tmp_path):
    env, _ = _power_env(tmp_path)
    env["BOT_POWER_PLATFORM"] = "Linux"

    result = _run_power(env, "status")

    assert result.returncode != 0
    assert "Darwin" in result.stderr


def test_ac_power_starts_once_when_desired_on(tmp_path):
    env, paths = _power_env(tmp_path, power="ac")
    _enable(env, paths, "on")

    assert _run_power(env, "reconcile").returncode == 0
    assert paths["running"].exists()
    assert paths["actions"].read_text(encoding="utf-8").splitlines() == ["start"]
    assert "전원이 연결되어" in paths["notifications"].read_text(encoding="utf-8")

    assert _run_power(env, "reconcile").returncode == 0
    assert paths["actions"].read_text(encoding="utf-8").splitlines() == ["start"]


def test_battery_hard_stops_all_processes_but_preserves_desired_on(tmp_path):
    env, paths = _power_env(tmp_path, power="battery")
    _enable(env, paths, "on")
    paths["running"].touch()
    paths["worker"].touch()

    assert _run_power(env, "reconcile").returncode == 0

    assert not paths["running"].exists()
    assert not paths["worker"].exists()
    assert paths["actions"].read_text(encoding="utf-8").splitlines() == ["stop-hard"]
    assert (paths["state"] / "desired_state").read_text(encoding="utf-8").strip() == "on"
    assert "배터리 사용으로" in paths["notifications"].read_text(encoding="utf-8")


def test_desired_off_soft_stops_main_and_preserves_detached_worker(tmp_path):
    env, paths = _power_env(tmp_path, power="ac")
    _enable(env, paths, "off")
    paths["running"].touch()
    paths["worker"].touch()

    assert _run_power(env, "reconcile").returncode == 0

    assert not paths["running"].exists()
    assert paths["worker"].exists()
    assert paths["actions"].read_text(encoding="utf-8").splitlines() == ["stop-soft"]
    assert not paths["notifications"].exists()


def test_unknown_power_does_not_change_process_state(tmp_path):
    env, paths = _power_env(tmp_path, power="unknown")
    _enable(env, paths, "on")
    paths["running"].touch()

    assert _run_power(env, "reconcile").returncode == 0

    assert paths["running"].exists()
    assert not paths["actions"].exists()


def test_monitor_reconciles_battery_then_ac_stream_events(tmp_path):
    env, paths = _power_env(tmp_path, power="ac")
    _enable(env, paths, "on")
    paths["running"].touch()

    event_pmset = tmp_path / "event-pmset"
    _write_executable(
        event_pmset,
        f"""#!/bin/bash
if [ "$1 $2" = "-g batt" ]; then
  power=$(sed -n '1p' {paths['power']!s})
  case "$power" in
    ac) echo "Now drawing from 'AC Power'" ;;
    battery) echo "Now drawing from 'Battery Power'" ;;
  esac
  exit 0
fi
if [ "$1 $2" = "-g pslog" ]; then
  echo battery > {paths['power']!s}
  echo "Now drawing from 'Battery Power'"
  sleep 1
  echo ac > {paths['power']!s}
  echo "Now drawing from 'AC Power'"
  sleep 1
  exit 0
fi
exit 1
""",
    )
    env["BOT_POWER_PMSET_BIN"] = str(event_pmset)

    result = _run_power(env, "monitor")

    assert result.returncode == 0
    assert paths["actions"].read_text(encoding="utf-8").splitlines() == ["stop-hard", "start"]
    assert paths["running"].exists()
    assert (paths["state"] / "desired_state").read_text(encoding="utf-8").strip() == "on"
    notifications = paths["notifications"].read_text(encoding="utf-8")
    assert "배터리 사용으로" in notifications
    assert "전원이 연결되어" in notifications


def test_power_status_reports_supervisor_only_runtime_as_degraded(tmp_path):
    env, paths = _power_env(tmp_path, power="ac")
    _enable(env, paths, "on")
    paths["degraded"].touch()

    result = _run_power(env, "status")

    assert result.returncode == 0
    assert "bot      : degraded" in result.stdout


def test_run_sh_defers_start_on_battery_only_when_power_management_enabled(tmp_path):
    calls = tmp_path / "calls"
    manager = tmp_path / "manager.sh"
    _write_executable(
        manager,
        f"""#!/bin/bash
echo "$*" >> {calls!s}
case "$1" in
  is-enabled|set-desired|reconcile) exit 0 ;;
  current-power) echo battery; exit 0 ;;
  *) exit 1 ;;
esac
""",
    )
    env = os.environ.copy()
    env.update(
        {
            "BOT_POWER_PLATFORM": "Darwin",
            "BOT_POWER_MANAGER_SCRIPT": str(manager),
            "BOT_DATA_DIR": str(tmp_path / "data"),
        }
    )

    result = subprocess.run(
        [str(RUN_SCRIPT), "start"],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "배터리 사용 중" in result.stdout
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "is-enabled",
        "set-desired on",
        "current-power",
        "reconcile",
    ]


def test_run_sh_does_not_consult_power_manager_outside_macos(tmp_path):
    calls = tmp_path / "calls"
    manager = tmp_path / "manager.sh"
    _write_executable(
        manager,
        f"""#!/bin/bash
echo "$*" >> {calls!s}
exit 0
""",
    )
    env = os.environ.copy()
    env.update(
        {
            "BOT_POWER_PLATFORM": "Linux",
            "BOT_POWER_MANAGER_SCRIPT": str(manager),
            "BOT_DATA_DIR": str(tmp_path / "data"),
        }
    )

    result = subprocess.run(
        [str(RUN_SCRIPT), "status"],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert not calls.exists()


def test_run_sh_process_discovery_is_scoped_to_project_cwd(tmp_path):
    command = [sys.executable, "-c", "import time; time.sleep(30)", "src.worker_job"]
    project_worker = subprocess.Popen(command, cwd=PROJECT_ROOT)
    foreign_worker = subprocess.Popen(command, cwd=tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "BOT_DATA_DIR": str(tmp_path / "data"),
            "BOT_POWER_MANAGER_INTERNAL": "1",
        }
    )
    try:
        result = subprocess.run(
            [str(RUN_SCRIPT), "_power-worker-pids"],
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        project_worker.terminate()
        foreign_worker.terminate()
        project_worker.wait(timeout=5)
        foreign_worker.wait(timeout=5)

    discovered = {int(pid) for pid in result.stdout.split()}
    assert project_worker.pid in discovered
    assert foreign_worker.pid not in discovered


def test_installer_and_uninstaller_use_isolated_launchagent_paths(tmp_path):
    env, paths = _power_env(tmp_path, power="ac")
    launch_agents = tmp_path / "LaunchAgents"
    launchctl_calls = tmp_path / "launchctl-calls"
    launchctl = tmp_path / "launchctl"
    _write_executable(
        launchctl,
        f"""#!/bin/bash
echo "$*" >> {launchctl_calls!s}
exit 0
""",
    )
    env.update(
        {
            "BOT_POWER_LAUNCH_AGENTS_DIR": str(launch_agents),
            "BOT_POWER_LAUNCHCTL_BIN": str(launchctl),
            "BOT_POWER_PLUTIL_BIN": "/usr/bin/plutil",
            "BOT_POWER_USER_ID": "501",
            "BOT_RUN_SCRIPT": str(paths["run_script"]),
            "BOT_POWER_RUNTIME_PATH": (
                "/opt/example-stable-bin:"
                "/var/folders/example/T/cmux-cli-shims/session:"
                f"{Path.home() / '.codex/tmp/arg0/session'}:/var/run/example/bin:/usr/bin:/bin"
            ),
        }
    )

    install = subprocess.run(
        [str(INSTALLER)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    target = launch_agents / "com.telegram-ai-cli-bot.power-manager.plist"
    assert install.returncode == 0, install.stderr
    assert target.exists()
    assert "__PROJECT_ROOT__" not in target.read_text(encoding="utf-8")
    plist = plistlib.loads(target.read_bytes())
    assert plist["AbandonProcessGroup"] is True
    runtime_path = plist["EnvironmentVariables"]["PATH"]
    assert "/opt/example-stable-bin" in runtime_path.split(":")
    assert "/usr/bin" in runtime_path.split(":")
    assert "/var/folders/example/T/cmux-cli-shims/session" not in runtime_path
    assert str(Path.home() / ".codex/tmp/arg0/session") not in runtime_path
    assert "/var/run/example/bin" not in runtime_path
    assert (paths["state"] / "enabled").exists()
    assert "bootstrap gui/501" in launchctl_calls.read_text(encoding="utf-8")

    (paths["state"] / "desired_state").write_text("on\n", encoding="utf-8")
    reinstall = subprocess.run(
        [str(INSTALLER)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert reinstall.returncode == 0
    assert (paths["state"] / "desired_state").read_text(encoding="utf-8").strip() == "on"

    uninstall = subprocess.run(
        [str(UNINSTALLER)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert uninstall.returncode == 0
    assert not target.exists()
    assert not (paths["state"] / "enabled").exists()


def test_installer_retries_transient_launchctl_bootstrap_failure(tmp_path):
    env, paths = _power_env(tmp_path, power="ac")
    launch_agents = tmp_path / "LaunchAgents"
    launchctl_attempts = tmp_path / "launchctl-attempts"
    launchctl = tmp_path / "launchctl"
    _write_executable(
        launchctl,
        f"""#!/bin/bash
if [ "$1" = "bootstrap" ]; then
  attempt=0
  [ -f {launchctl_attempts!s} ] && attempt=$(cat {launchctl_attempts!s})
  attempt=$((attempt + 1))
  echo "$attempt" > {launchctl_attempts!s}
  [ "$attempt" -ge 2 ]
  exit $?
fi
exit 0
""",
    )
    env.update(
        {
            "BOT_POWER_LAUNCH_AGENTS_DIR": str(launch_agents),
            "BOT_POWER_LAUNCHCTL_BIN": str(launchctl),
            "BOT_POWER_PLUTIL_BIN": "/usr/bin/plutil",
            "BOT_POWER_USER_ID": "501",
            "BOT_RUN_SCRIPT": str(paths["run_script"]),
        }
    )

    result = subprocess.run(
        [str(INSTALLER)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert launchctl_attempts.read_text(encoding="utf-8").strip() == "2"
    assert (paths["state"] / "enabled").exists()
