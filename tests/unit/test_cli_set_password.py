"""Tests for the host-access recovery CLI: python -m watermeter set-password."""

import getpass
import io
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from watermeter import auth

REPO_ROOT = Path(__file__).resolve().parents[2]
GOOD = "longenough1"


class FakeStdin(io.StringIO):
    """stdin stand-in with a controllable isatty()."""

    def __init__(self, text="", tty=False):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty


@pytest.fixture
def auth_path(tmp_path, monkeypatch):
    """Fresh auth.json location for the default-path variant."""
    path = tmp_path / "data" / "auth.json"
    monkeypatch.setenv("WATERMETER_AUTH_FILE", str(path))
    return path


def _stdin(monkeypatch, text, tty=False):
    monkeypatch.setattr(sys, "stdin", FakeStdin(text, tty))


def _getpass(monkeypatch, answers):
    prompts = []
    queue = list(answers)

    def fake(prompt="Password: ", stream=None):
        prompts.append(prompt)
        return queue.pop(0)

    monkeypatch.setattr(getpass, "getpass", fake)
    return prompts


# -- non-TTY stdin ---------------------------------------------------------------------------------------------------


def test_creates_auth_file_from_one_stdin_line(auth_path, monkeypatch, capsys):
    """No auth.json yet: the CLI creates it from one stdin line and says where it is."""
    _stdin(monkeypatch, GOOD + "\n")
    assert auth.cli_set_password(str(auth_path)) == 0
    record = auth.AuthStore(auth_path).record()
    assert auth.verify_password(GOOD, record.password_hash) is True
    out = capsys.readouterr().out
    assert str(auth_path) in out
    assert "restart" in out.lower()
    assert GOOD not in out


def test_uses_default_path_from_environment(auth_path, monkeypatch):
    """Without --auth-file the WATERMETER_AUTH_FILE location is used."""
    _stdin(monkeypatch, GOOD + "\n")
    assert auth.cli_set_password() == 0
    assert auth_path.is_file()


def test_existing_file_keeps_identity_and_bumps_epoch(auth_path, monkeypatch):
    """A reset logs everyone out but keeps the secret and the cookie name."""
    store = auth.AuthStore(auth_path)
    assert store.create(auth.hash_password("original-password")) is True
    before = store.record()
    old_token = auth.issue_token(before)

    _stdin(monkeypatch, "brand-new-password\n")
    assert auth.cli_set_password(str(auth_path)) == 0

    after = auth.AuthStore(auth_path).record()
    assert after.session_secret == before.session_secret
    assert after.instance_id == before.instance_id
    assert after.epoch == before.epoch + 1
    assert auth.verify_password("brand-new-password", after.password_hash) is True
    assert auth.verify_token(after, old_token) is None


def test_crlf_line_ending_is_stripped(auth_path, monkeypatch):
    """One trailing CRLF is removed."""
    _stdin(monkeypatch, "windows-line-ending\r\n")
    assert auth.cli_set_password(str(auth_path)) == 0
    assert auth.verify_password("windows-line-ending", auth.AuthStore(auth_path).record().password_hash) is True


def test_reads_exactly_one_line(auth_path, monkeypatch):
    """Only the first line counts."""
    _stdin(monkeypatch, "first-line-pass\nsecond-line\n")
    assert auth.cli_set_password(str(auth_path)) == 0
    assert auth.verify_password("first-line-pass", auth.AuthStore(auth_path).record().password_hash) is True


@pytest.mark.parametrize("text", ["short\n", "\n", "", "1234567\n"])
def test_invalid_input_exits_nonzero_and_creates_nothing(auth_path, monkeypatch, capsys, text):
    """Empty or under 8 characters: error on stderr, no file."""
    _stdin(monkeypatch, text)
    assert auth.cli_set_password(str(auth_path)) != 0
    assert not auth_path.exists()
    err = capsys.readouterr().err
    assert ("required" in err) or ("at least 8" in err)


@pytest.mark.parametrize("text", ["short\n", "\n", ""])
def test_invalid_input_leaves_existing_file_byte_identical(auth_path, monkeypatch, text):
    """A rejected reset does not touch the stored password."""
    auth.AuthStore(auth_path).create(auth.hash_password("original-password"))
    before = auth_path.read_bytes()
    _stdin(monkeypatch, text)
    assert auth.cli_set_password(str(auth_path)) != 0
    assert auth_path.read_bytes() == before


def test_unavailable_file_returns_two_and_is_untouched(auth_path, monkeypatch, capsys):
    """A damaged auth.json is reported, never overwritten."""
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text("garbage")
    _stdin(monkeypatch, GOOD + "\n")
    assert auth.cli_set_password(str(auth_path)) == 2
    assert auth_path.read_text() == "garbage"
    err = capsys.readouterr().err.lower()
    assert "fix or delete" in err


def test_password_is_never_printed(auth_path, monkeypatch, capsys):
    """Neither stream contains the password."""
    _stdin(monkeypatch, "do-not-print-me\n")
    auth.cli_set_password(str(auth_path))
    captured = capsys.readouterr()
    assert "do-not-print-me" not in captured.out + captured.err


# -- TTY -------------------------------------------------------------------------------------------------------------


def test_tty_prompts_for_password_then_confirmation(auth_path, monkeypatch):
    """Two prompts, in that order; equal valid answers write the file."""
    _stdin(monkeypatch, "", tty=True)
    prompts = _getpass(monkeypatch, [GOOD, GOOD])
    assert auth.cli_set_password(str(auth_path)) == 0
    assert prompts == ["New admin password: ", "Repeat it: "]
    assert auth.verify_password(GOOD, auth.AuthStore(auth_path).record().password_hash) is True


def test_tty_mismatch_exits_nonzero_without_writing(auth_path, monkeypatch, capsys):
    """Different answers are refused."""
    _stdin(monkeypatch, "", tty=True)
    _getpass(monkeypatch, [GOOD, GOOD + "x"])
    assert auth.cli_set_password(str(auth_path)) != 0
    assert not auth_path.exists()
    assert "do not match" in capsys.readouterr().err


def test_tty_mismatch_leaves_existing_file_alone(auth_path, monkeypatch):
    """A mismatch on a reset keeps the old password."""
    auth.AuthStore(auth_path).create(auth.hash_password("original-password"))
    before = auth_path.read_bytes()
    _stdin(monkeypatch, "", tty=True)
    _getpass(monkeypatch, [GOOD, "other-answer-1"])
    assert auth.cli_set_password(str(auth_path)) != 0
    assert auth_path.read_bytes() == before


# -- argparse wiring (in-process) ------------------------------------------------------------------------------------


def _run_cli(monkeypatch, *argv):
    from watermeter import __main__ as entry

    monkeypatch.setattr(sys, "argv", ["watermeter", *argv])
    with pytest.raises(SystemExit) as raised:
        entry.cli()
    return raised.value.code


def test_cli_dispatches_set_password(auth_path, monkeypatch):
    """The set-password subcommand reaches cli_set_password with the --auth-file value."""
    _stdin(monkeypatch, GOOD + "\n")
    assert _run_cli(monkeypatch, "set-password", "--auth-file", str(auth_path)) == 0
    assert auth_path.is_file()


def test_cli_one_shot_still_parses_as_before(monkeypatch):
    """--one-shot --config <cfg> calls the one-shot entry point with that config and its exit code."""
    calls = []
    fake = ModuleType("watermeter.oneshot")
    fake.main = lambda config: calls.append(config) or 7
    monkeypatch.setitem(sys.modules, "watermeter.oneshot", fake)
    assert _run_cli(monkeypatch, "--one-shot", "--config", "some.yaml") == 7
    assert calls == ["some.yaml"]


def test_cli_without_arguments_starts_the_server(monkeypatch):
    """No subcommand and no flag still runs the web app entry point."""
    from watermeter import __main__ as entry

    calls = []
    fake = ModuleType("watermeter.app")
    fake.main = lambda: calls.append("server")
    monkeypatch.setitem(sys.modules, "watermeter.app", fake)
    monkeypatch.setattr(sys, "argv", ["watermeter"])
    entry.cli()
    assert calls == ["server"]


# -- subprocess ------------------------------------------------------------------------------------------------------


def _python(*args, stdin="", **kwargs):
    return subprocess.run(
        [sys.executable, "-m", "watermeter", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=REPO_ROOT,
        **kwargs,
    )


def test_subprocess_help_lists_old_flags_and_new_subcommand():
    """--one-shot and --config are still documented, and set-password is listed."""
    result = _python("--help")
    assert result.returncode == 0
    for text in ("--one-shot", "--config", "set-password"):
        assert text in result.stdout


def test_subprocess_set_password_help():
    """The subcommand has its own help with --auth-file."""
    result = _python("set-password", "--help")
    assert result.returncode == 0
    assert "--auth-file" in result.stdout


def test_subprocess_set_password_creates_file(tmp_path):
    """End to end through python -m watermeter with one stdin line."""
    target = tmp_path / "data" / "auth.json"
    result = _python("set-password", "--auth-file", str(target), stdin=GOOD + "\n")
    assert result.returncode == 0, result.stderr
    data = json.loads(target.read_text())
    assert data["epoch"] == 1 and data["password_hash"].startswith("$scrypt$")


def test_subprocess_set_password_rejects_short_password(tmp_path):
    """A short password exits non-zero with the reason on stderr."""
    target = tmp_path / "auth.json"
    result = _python("set-password", "--auth-file", str(target), stdin="short\n")
    assert result.returncode != 0
    assert "at least 8" in result.stderr
    assert not target.exists()


def test_set_password_does_not_import_the_app(tmp_path):
    """The recovery path must not drag in the web app, cv2 or openvino."""
    code = (
        "import sys\n"
        "from watermeter import __main__ as m\n"
        "sys.argv = ['watermeter', 'set-password', '--auth-file', sys.argv[1]]\n"
        "try:\n    m.cli()\nexcept SystemExit as exc:\n    code = exc.code\n"
        "bad = [n for n in ('watermeter.app', 'cv2', 'openvino', 'fastapi', 'starlette') if n in sys.modules]\n"
        "print('IMPORTED', bad, 'EXIT', code)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "auth.json")],
        input=GOOD + "\n",
        capture_output=True,
        text=True,
        timeout=30,
        cwd=REPO_ROOT,
    )
    assert "IMPORTED [] EXIT 0" in result.stdout, result.stdout + result.stderr
