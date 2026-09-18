"""Offline guard rails for the root-level ``demo`` runner script.

Nothing here starts Docker. The tests either read the script as text or run the
two code paths that are documented as Docker-free (``--help`` and an unknown
command), so they are safe in CI.

They exist to pin down the properties that are easy to break by accident and
expensive to discover on a judge's laptop: that the script stays executable,
that it never regenerates or force-replaces the seed data, that volume deletion
is confined to ``reset``, that ``.env`` is only ever created from the template,
and that readiness is bounded polling rather than a fixed sleep.
"""

import os
import re
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_SCRIPT = REPO_ROOT / "demo"

DOCUMENTED_COMMANDS = ("up", "status", "logs", "test", "down", "reset", "help")


@pytest.fixture(scope="module")
def script_text() -> str:
    return DEMO_SCRIPT.read_text(encoding="utf-8")


def bash_function_body(text: str, name: str) -> str:
    """Return the body of a ``name() {`` ... ``}`` bash function definition.

    The script declares every function at column zero and closes it with a
    lone ``}``, so this stays simple on purpose rather than trying to parse
    shell grammar.
    """
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if re.match(rf"^{re.escape(name)}\(\)\s*\{{\s*$", line):
            start = index + 1
            break
    assert start is not None, f"no '{name}()' function found in the demo script"
    for offset, line in enumerate(lines[start:]):
        if line == "}":
            return "\n".join(lines[start : start + offset])
    raise AssertionError(f"'{name}()' is never closed")


def run_demo(*args: str, extra_path: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if extra_path:
        env["PATH"] = f"{extra_path}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(
        ["bash", str(DEMO_SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO_ROOT,
        env=env,
    )


# --- presence and shape ------------------------------------------------------


def test_demo_script_exists():
    assert DEMO_SCRIPT.is_file(), "the repository root must contain a 'demo' script"


def test_demo_script_is_executable():
    mode = DEMO_SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "'demo' must be executable so './demo up' works"
    assert mode & (stat.S_IXGRP | stat.S_IXOTH), "'demo' should be executable for all"


def test_demo_script_is_bash_with_strict_mode(script_text):
    first_line = script_text.splitlines()[0]
    assert first_line == "#!/usr/bin/env bash"
    assert "set -euo pipefail" in script_text


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not available")
def test_bash_syntax_is_valid():
    result = subprocess.run(
        ["bash", "-n", str(DEMO_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck is not installed")
def test_shellcheck_is_clean():
    result = subprocess.run(
        ["shellcheck", "--severity=warning", str(DEMO_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- the Docker-free entry points --------------------------------------------


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not available")
@pytest.mark.parametrize("flag", ["help", "--help", "-h"])
def test_help_exits_zero_and_lists_every_command(flag):
    result = run_demo(flag)
    assert result.returncode == 0, result.stderr
    for command in DOCUMENTED_COMMANDS:
        assert re.search(rf"^\s+{command}\b", result.stdout, re.MULTILINE), (
            f"'{command}' is missing from the help output"
        )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not available")
def test_help_does_not_touch_docker(tmp_path):
    """Help must work on a machine without Docker, so it must not shell out to it.

    A stub earlier on PATH records any invocation; the marker file staying
    absent is the assertion.
    """
    marker = tmp_path / "docker-was-called"
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "docker"
    stub.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            echo "$@" >> {marker}
            exit 1
            """
        ),
        encoding="utf-8",
    )
    stub.chmod(0o755)

    for args in (("help",), ()):
        result = run_demo(*args, extra_path=str(stub_dir))
        assert result.returncode == 0, result.stderr

    assert not marker.exists(), f"help invoked docker: {marker.read_text()}"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not available")
def test_no_arguments_shows_help():
    result = run_demo()
    assert result.returncode == 0
    assert "Usage: ./demo" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not available")
def test_unknown_command_exits_two_with_usage_on_stderr():
    result = run_demo("definitely-not-a-command")
    assert result.returncode == 2
    assert "unknown command" in result.stderr
    assert "Usage: ./demo" in result.stderr


# --- seed safety -------------------------------------------------------------


def test_runner_never_regenerates_seed_data(script_text):
    assert "generate_synthetic" not in script_text, (
        "the runner must never invoke the synthetic seed generator — it would "
        "overwrite the hand-tuned fixtures in data/seed"
    )


def test_runner_never_force_replaces_the_seeded_network(script_text):
    assert "--force" not in script_text, (
        "the seeder must be invoked with no flags so an existing demo network "
        "is left alone and re-runs stay idempotent"
    )


# --- destructive-path containment --------------------------------------------


def test_volume_deletion_appears_only_in_reset(script_text):
    reset_body = bash_function_body(script_text, "cmd_reset")
    deleting_lines = [
        line
        for line in script_text.splitlines()
        if re.search(r"\bdown\b[^#\n]*\s-v\b", line) or "down -v" in line
    ]
    assert deleting_lines, "reset is supposed to remove volumes with 'down -v'"
    for line in deleting_lines:
        assert line in reset_body, f"volume deletion outside of reset: {line.strip()!r}"


def test_down_preserves_volumes(script_text):
    down_body = bash_function_body(script_text, "cmd_down")
    assert "down --remove-orphans" in down_body
    assert "-v" not in down_body, "'./demo down' must not delete the data volumes"


def test_reset_requires_confirmation(script_text):
    reset_body = bash_function_body(script_text, "cmd_reset")
    assert "--yes" in reset_body and "-y" in reset_body
    assert "-t 0" in reset_body, "reset should prompt when stdin is a TTY"
    assert "read -r -p" in reset_body


# --- .env handling -----------------------------------------------------------


def test_env_file_is_only_ever_copied_from_the_template(script_text):
    env_body = bash_function_body(script_text, "ensure_env")
    copies = [line.strip() for line in env_body.splitlines() if re.match(r"^\s*cp\b", line)]
    assert len(copies) == 1, f"expected exactly one copy of the env template, got {copies}"
    assert copies[0] == 'cp "$ENV_TEMPLATE" "$ENV_FILE"'

    guard_index = next(
        index
        for index, line in enumerate(env_body.splitlines())
        if '! -f "$ENV_FILE"' in line
    )
    copy_index = next(
        index for index, line in enumerate(env_body.splitlines()) if re.match(r"^\s*cp\b", line)
    )
    assert guard_index < copy_index, "the copy must be guarded by an absence check"

    # No other command in the script may write to .env.
    writes = re.findall(r'>\s*"?\$ENV_FILE"?', script_text)
    assert not writes, "the runner must never write into an existing .env"


def test_generated_env_file_stays_git_ignored():
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in [line.strip() for line in gitignore]


# --- readiness ---------------------------------------------------------------


def test_readiness_uses_bounded_polling(script_text):
    assert "poll_until()" in script_text, "readiness must go through a bounded poll helper"
    poll_body = bash_function_body(script_text, "poll_until")
    assert "SECONDS" in poll_body and "deadline" in poll_body, (
        "poll_until must enforce a wall-clock deadline"
    )
    # Called for datastores, migrator, seeder, backend, published port, frontend.
    assert script_text.count("poll_until ") >= 6


def test_no_long_fixed_sleeps(script_text):
    for match in re.finditer(r"\bsleep\s+(\S+)", script_text):
        argument = match.group(1)
        if argument == '"$POLL_INTERVAL"':
            continue
        value = float(argument.strip('"'))
        assert value < 1, f"fixed sleep of {argument} is not a readiness check"


def test_one_shot_job_polling_passes_the_service_name(script_text):
    """poll_until calls its predicate with no implicit context.

    job_exited() needs the service name as an argument; omitting it makes the
    predicate fail forever and every migration/seed step time out.
    """
    body = bash_function_body(script_text, "run_job")
    assert re.search(r'poll_until\s+"\$budget"[^\n]*job_exited\s+"\$svc"', body), (
        "run_job must pass the service name through to job_exited"
    )


def test_timeout_budgets_are_environment_overridable(script_text):
    budgets = {
        "DEMO_BUILD_TIMEOUT": "900",
        "DEMO_DATASTORE_TIMEOUT": "240",
        "DEMO_MIGRATE_TIMEOUT": "180",
        "DEMO_SEED_TIMEOUT": "300",
        "DEMO_BACKEND_TIMEOUT": "120",
        "DEMO_FRONTEND_TIMEOUT": "180",
    }
    for name, default in budgets.items():
        assert f'{name}="${{{name}:-{default}}}"' in script_text, (
            f"{name} must be overridable with a {default}s default"
        )


def test_backend_readiness_checks_every_dependency(script_text):
    body = bash_function_body(script_text, "backend_health")
    assert "/health" in body
    assert 'payload.get("status") == "ok"' in body
    assert "all(" in body, "a partially degraded /health payload must not count as ready"


# --- compose wiring ---------------------------------------------------------


def test_no_compose_call_can_omit_the_demo_profile(script_text):
    """The frontend only exists inside the 'demo' profile.

    A lifecycle call that forgets the flag would silently skip it, so every
    literal ``docker compose`` invocation must carry the profile. The only
    exemption is the preflight capability probe, which starts nothing.
    """
    wrapper = bash_function_body(script_text, "compose")
    assert 'docker compose --profile demo "$@"' in wrapper

    invocations = [
        line.strip() for line in script_text.splitlines() if line.strip().startswith("docker compose")
    ]
    assert invocations, "expected at least the wrapper's own compose invocation"
    for invocation in invocations:
        if invocation.startswith("docker compose version"):
            continue
        assert "--profile demo" in invocation, (
            f"compose call omits the demo profile: {invocation!r}"
        )

    assert "if ! docker compose version" in script_text


def test_bounded_compose_helper_cannot_shell_out_to_a_function(script_text):
    """`timeout` execs a binary, so it must not be handed the compose function."""
    body = bash_function_body(script_text, "compose_bounded")
    assert 'timeout "$budget" docker compose --profile demo "$@"' in body
    assert re.search(r"timeout\s+\"\$budget\"\s+compose\b", script_text) is None


def test_all_documented_commands_are_dispatched(script_text):
    for command in DOCUMENTED_COMMANDS:
        assert re.search(rf"^\s+{command}\)|^\s+{command} \|", script_text, re.MULTILINE), (
            f"'{command}' is not wired into the dispatch table"
        )


def test_one_shot_jobs_are_resolved_by_compose_ps(script_text):
    """Health and exit-code lookups must not depend on hardcoded container names."""
    assert "compose ps -aq" in script_text
    assert "ripple-postgres" not in script_text
    assert "ripple-backend" not in script_text


def test_demo_test_runs_the_scenario_in_the_backend_container(script_text):
    body = bash_function_body(script_text, "cmd_test")
    assert "compose exec -T backend python /data/scripts/demo_scenario.py" in body
    assert "e2e_test.py" not in script_text, (
        "e2e_test.py stays a host-side developer tool; it is not mounted or run here"
    )


# --- compose file ------------------------------------------------------------


def test_frontend_service_is_profile_gated():
    compose_text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "frontend:" in compose_text
    frontend_block = compose_text.split("  frontend:", 1)[1].split("\nvolumes:", 1)[0]
    assert "- demo" in frontend_block, (
        "the frontend must stay behind the 'demo' profile so that a plain "
        "'docker compose up --build' keeps port 5173 free for npm run dev"
    )
    assert "5173:5173" in frontend_block
    assert "VITE_API_TARGET: http://backend:8000" in frontend_block


def test_vite_proxy_target_defaults_to_the_manual_workflow():
    vite_config = (REPO_ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    assert 'process.env.VITE_API_TARGET ?? "http://localhost:8000"' in vite_config
    assert 'apiTarget.replace(/^http/, "ws")' in vite_config
    assert "target: apiTarget" in vite_config
    assert "target: wsTarget" in vite_config
