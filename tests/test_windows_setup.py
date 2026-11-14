"""Static checks of the Windows batch files (no Windows needed): CRLF line endings, plain
ASCII, every goto target exists, and setup-windows.bat does its four steps in order."""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BATS = sorted(ROOT.glob("*.bat"))


def test_bat_files_found():
    names = {p.name for p in BATS}
    assert {"setup-windows.bat", "start-scada.bat", "make-desktop-shortcuts.bat"} <= names


@pytest.mark.parametrize("path", BATS, ids=lambda p: p.name)
def test_bat_crlf_ascii_and_labels(path):
    raw = path.read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")      # CRLF only
    text = raw.decode("ascii")                                             # no UTF-8 surprises in cmd
    labels = {m.lower() for m in re.findall(r"(?m)^:(\w+)", text)}
    targets = {t.lower() for t in re.findall(r"(?i)\bgoto\s+:?(\w+)", text)}
    targets.discard("eof")
    assert targets <= labels, f"goto without label: {targets - labels}"


def test_setup_windows_steps():
    text = (ROOT / "setup-windows.bat").read_text(encoding="ascii")
    steps = [
        'cd /d "%~dp0"',
        'if not exist "requirements.txt" goto :notunzipped',               # run from inside the ZIP
        "sys.version_info[:2] >= (3, 12)",                                 # pins need 3.12+
        "sys.maxsize.bit_length() == 63",                                  # and 64-bit
        "python -m venv .venv",
        '".venv\\Scripts\\python.exe" -m pip install -r requirements.txt',
        'call "make-desktop-shortcuts.bat" /nopause',
        "Done: double-click PV SCADA on your Desktop",
    ]
    pos = [text.find(s) for s in steps]
    assert all(p >= 0 for p in pos), [s for s, p in zip(steps, pos) if p < 0]
    assert pos == sorted(pos)
    assert "https://www.python.org/downloads/windows/" in text
    assert '"Add python.exe to PATH"' in text
    assert not re.search(r"(?mi)^\s*git\s", text)                          # never calls git


def test_start_scada_runs_without_git_in_a_zip_copy():
    text = (ROOT / "start-scada.bat").read_text(encoding="ascii")
    nogit = text.find('if not exist ".git" goto :nogit')
    assert 0 <= nogit < text.find("where git")                             # checked before git is needed
    assert "setup-windows.bat" in text


def test_shortcut_script_can_run_without_pause():
    text = (ROOT / "make-desktop-shortcuts.bat").read_text(encoding="ascii")
    assert '"%~1"=="/nopause"' in text and "exit /b %RC%" in text
    assert "$ErrorActionPreference='Stop'" in text                         # failure -> exit code 1
    assert "git checkout" not in text and "twin-sim" not in text          # never switches branches
