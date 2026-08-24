"""Windows process-level regression tests for the frozen-app update handoff."""

import base64
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process semantics")
def test_apply_helper_survives_the_frozen_app_process_tree(tmp_path):
    """Exiting the app must not recursively kill its PowerShell swap helper.

    The extra wrapper stands in for PyInstaller's onefile bootloader. This is
    important for the regression: the former ``taskkill /T`` implementation
    targets that parent and therefore kills both the app child and the updater
    grandchild, while leaving the pytest process itself outside the test tree.
    """
    repo = Path(__file__).resolve().parents[1]
    target = tmp_path / "install" / "DocCipherProbe.exe"
    staged = tmp_path / "data" / "DocCipherProbe.new"
    target.parent.mkdir()
    staged.parent.mkdir()
    target.write_bytes(b"old verified build")
    staged.write_bytes(b"new verified build")

    isolated_temp = tmp_path / "temp"
    isolated_local = tmp_path / "local"
    isolated_temp.mkdir()
    isolated_local.mkdir()

    app_source = r"""
import os
import sys
import time
from pathlib import Path

from backend import main, updater

# Exercise the frozen-only shutdown path without making the fixture itself a
# PyInstaller bundle. running_exe() is replaced so only our isolated file is
# ever passed to the helper.
sys.frozen = True
updater.running_exe = lambda: Path(os.environ["DOCCIPHER_TEST_TARGET"])
updater.state.update(
    staged_path=os.environ["DOCCIPHER_TEST_STAGED"],
    ready=True,
)
main.api_update_apply()

# quit_soon() must terminate us first. This guard prevents a hung regression
# from leaving the wrapper alive indefinitely.
time.sleep(12)
raise SystemExit("update shutdown did not terminate the app process")
"""
    app_encoded = base64.b64encode(app_source.encode()).decode("ascii")
    wrapper_source = rf"""
import base64
import subprocess
import sys

app_source = base64.b64decode({app_encoded!r})
child = subprocess.run(
    [sys.executable, "-c", app_source],
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    timeout=15,
)
raise SystemExit(child.returncode)
"""

    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(repo),
            "TEMP": str(isolated_temp),
            "TMP": str(isolated_temp),
            "LOCALAPPDATA": str(isolated_local),
            "DOCCIPHER_TEST_TARGET": str(target),
            "DOCCIPHER_TEST_STAGED": str(staged),
        }
    )
    wrapper = subprocess.run(
        [sys.executable, "-c", wrapper_source],
        cwd=repo,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=20,
    )
    assert wrapper.returncode == 0

    backup = Path(f"{target}.old")
    log = isolated_local / "DocCipherBreaker" / "update.log"
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if (
            target.read_bytes() == b"new verified build"
            and backup.is_file()
            and not staged.exists()
        ):
            break
        time.sleep(0.1)

    assert target.read_bytes() == b"new verified build"
    assert backup.read_bytes() == b"old verified build"
    assert not staged.exists()

    # "swap complete" is written just after the moves, so the files can be in
    # place a moment before the line reaches the log. Wait for it rather than
    # racing the script's own logging.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if "swap complete" in log.read_text(encoding="utf-8-sig"):
            break
        time.sleep(0.1)
    assert "swap complete" in log.read_text(encoding="utf-8-sig")
