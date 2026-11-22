"""Behaviour that differs in the single-file (PyInstaller) build, where sys.executable is the app itself."""

import sys

import pytest

from omos.errors import PermissionDenied
from omos.plugins import manager


def test_plugin_interpreter_from_source(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert manager.python_interpreter() == sys.executable


def test_plugin_interpreter_when_frozen_uses_system_python(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(manager.shutil, "which", lambda n: "/usr/bin/python3" if n == "python3" else None)
    assert manager.python_interpreter() == "/usr/bin/python3"


def test_plugin_interpreter_when_frozen_without_python_refuses_clearly(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(manager.shutil, "which", lambda n: None)
    with pytest.raises(PermissionDenied, match="need Python 3 installed"):
        manager.python_interpreter()
