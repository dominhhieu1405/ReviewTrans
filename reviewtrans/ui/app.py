from __future__ import annotations

import sys

from PyQt6 import QtCore, QtGui, QtWidgets

from .. import APP_NAME
from ..core.config import SettingsStore
from ..core.paths import fonts_dir, register_dll_dirs, resource_path
from .main_window import MainWindow
from .state import AppState
from .theme import apply_theme


def load_user_fonts() -> None:
    folder = fonts_dir()
    for path in folder.iterdir():
        if path.suffix.lower() in (".ttf", ".otf", ".ttc"):
            QtGui.QFontDatabase.addApplicationFont(str(path))


def main() -> int:
    register_dll_dirs()
    QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
        QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("ReviewTrans")
    icon_path = resource_path("icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))
    load_user_fonts()
    apply_theme(app)
    state = AppState(SettingsStore())
    window = MainWindow(state)
    window.show()
    return app.exec()
