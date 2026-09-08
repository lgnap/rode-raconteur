"""Fixtures every test in this suite gets, whether it asks or not.

The one below exists because the suite came within a monkeypatch of reading —
and, on a different code path, writing — the user's real ledger and recording
folders. Four tests in test_app.py patched `erase_card` but not `Ledger`, so
`_erase_one` built a real `Ledger(serial)` against the real `ledger_root()`,
opening the file holding the day's imports. It happened to be a read; nothing
in the code guaranteed that.

A test suite must not be able to touch the user's data, and the way to
guarantee it is not to be careful in each test but to move the root the whole
suite resolves. Everything user-owned here hangs off XDG_DATA_HOME.
"""

import pytest


@pytest.fixture(autouse=True)
def isolated_data_home(tmp_path, monkeypatch):
    """Point every user-data path at this test's own tmp_path."""
    home = tmp_path / "xdg"
    home.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(home))
    return home
