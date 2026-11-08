"""For tests (the apps' and appkit's own): stands in for PyObjC, so the Mac
window code (macwindow.py) can be imported and its Python parts checked
on any computer. What it draws can only be checked on a Mac (each app's
*-installers.yml does, in its window's self-test).

    with fake_pyobjc() as fake:
        from station_studio import macwindow
        ...
        fake.AppHelper.callAfter.assert_called()
"""
from __future__ import annotations

import contextlib
import sys
import types
from unittest import mock


class _NSObject:
    """Enough of NSObject: alloc().init() and a subclass's own init."""

    @classmethod
    def alloc(cls):
        return cls.__new__(cls)

    def init(self):
        return self


@contextlib.contextmanager
def fake_pyobjc():
    """AppKit, Foundation, WebKit, objc and PyObjCTools as stand-ins for
    as long as the `with` lasts. Modules imported meanwhile are forgotten
    afterwards (sys.modules is put back as it was)."""
    objc = types.ModuleType("objc")
    objc.python_method = lambda f: f
    objc.super = super
    objc.autorelease_pool = contextlib.nullcontext
    appkit = mock.MagicMock(name="AppKit")
    appkit.NSObject = _NSObject
    helper = mock.MagicMock(name="AppHelper")
    tools = types.ModuleType("PyObjCTools")
    tools.AppHelper = helper
    fakes = {"objc": objc, "AppKit": appkit, "Foundation": mock.MagicMock(name="Foundation"),
             "WebKit": mock.MagicMock(name="WebKit"), "PyObjCTools": tools, "PyObjCTools.AppHelper": helper}
    with mock.patch.dict(sys.modules, fakes):
        yield types.SimpleNamespace(objc=objc, AppKit=appkit, Foundation=fakes["Foundation"],
                                    WebKit=fakes["WebKit"], AppHelper=helper)
