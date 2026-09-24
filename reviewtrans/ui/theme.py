from __future__ import annotations

from PyQt6 import QtGui, QtWidgets

BG = "#1b1c1f"
PANEL = "#232428"
PANEL_2 = "#2b2d31"
BORDER = "#3a3c42"
TEXT = "#e3e5e8"
TEXT_DIM = "#9aa0a8"
ACCENT = "#4c8dff"
ACCENT_DIM = "#2d4f8f"
DANGER = "#ef5b5b"
SUCCESS = "#3fb67a"
WARNING = "#e6a23c"

# màu track trên timeline
TRACK_COLORS = {
    "image": "#7c6cf0",
    "text": "#b36ae2",
    "blur": "#4f9fd8",
    "subtitle": "#d9a441",
    "dub": "#3fb67a",
    "dub_stale": "#8a6d3b",
    "original": "#5f6670",
    "bgm": "#4e7f6f",
    "video": "#50555e",
}

STATE_COLORS = {
    "done": SUCCESS,
    "stale": WARNING,
    "error": DANGER,
    "running": ACCENT,
    "none": TEXT_DIM,
}

QSS = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-size: 13px; }}
QMainWindow::separator {{ background: {BORDER}; width: 1px; height: 1px; }}
QToolTip {{ background: {PANEL_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QFrame#Panel, QWidget#Panel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; }}
QFrame#NavRail {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
QLabel#Title {{ font-size: 18px; font-weight: 600; background: transparent; }}
QLabel#Subtitle {{ color: {TEXT_DIM}; background: transparent; }}
QLabel {{ background: transparent; }}
QPushButton, QToolButton {{
    background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 5px; padding: 5px 10px;
}}
QPushButton:hover, QToolButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {ACCENT_DIM}; }}
QPushButton:disabled, QToolButton:disabled {{ color: #666; border-color: #333; }}
QPushButton#Primary {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: #5d9aff; }}
QPushButton#Danger {{ border-color: {DANGER}; color: {DANGER}; }}
QToolButton:checked {{ background: {ACCENT_DIM}; border-color: {ACCENT}; }}
QToolButton#NavButton {{
    background: transparent; border: none; border-radius: 8px; padding: 6px; color: {TEXT_DIM};
}}
QToolButton#NavButton:hover {{ background: {PANEL_2}; color: {TEXT}; }}
QToolButton#NavButton:checked {{ background: {ACCENT_DIM}; color: white; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 4px; padding: 3px 6px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {ACCENT};
}}
QComboBox QAbstractItemView {{ background: {PANEL_2}; border: 1px solid {BORDER}; selection-background-color: {ACCENT}; }}
QTableView, QTreeView, QListView, QListWidget, QTableWidget {{
    background: {PANEL}; alternate-background-color: #26272b; border: 1px solid {BORDER};
    gridline-color: {BORDER}; selection-background-color: {ACCENT_DIM}; selection-color: white;
}}
QHeaderView::section {{ background: {PANEL_2}; border: none; border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER}; padding: 4px 6px; color: {TEXT_DIM}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 4px; top: -1px; background: {PANEL}; }}
QTabBar::tab {{ background: transparent; padding: 6px 12px; border: none; color: {TEXT_DIM}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 6px; margin-top: 14px; padding-top: 8px; background: {PANEL}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {TEXT_DIM}; background: transparent; }}
QCheckBox, QRadioButton {{ background: transparent; spacing: 6px; }}
QProgressBar {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 4px; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 12px; margin: -5px 0; border-radius: 6px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: #4a4d55; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: #4a4d55; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QSplitter::handle {{ background: {BG}; }}
QSplitter::handle:hover {{ background: {ACCENT_DIM}; }}
QMenu {{ background: {PANEL_2}; border: 1px solid {BORDER}; }}
QMenu::item:selected {{ background: {ACCENT_DIM}; }}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; }}
QScrollArea {{ border: none; }}
"""


def apply_theme(app: QtWidgets.QApplication) -> None:
    app.setStyle("Fusion")
    palette = QtGui.QPalette()
    for role, color in (
        (QtGui.QPalette.ColorRole.Window, BG),
        (QtGui.QPalette.ColorRole.WindowText, TEXT),
        (QtGui.QPalette.ColorRole.Base, PANEL),
        (QtGui.QPalette.ColorRole.AlternateBase, PANEL_2),
        (QtGui.QPalette.ColorRole.Text, TEXT),
        (QtGui.QPalette.ColorRole.Button, PANEL_2),
        (QtGui.QPalette.ColorRole.ButtonText, TEXT),
        (QtGui.QPalette.ColorRole.Highlight, ACCENT),
        (QtGui.QPalette.ColorRole.HighlightedText, "#ffffff"),
        (QtGui.QPalette.ColorRole.ToolTipBase, PANEL_2),
        (QtGui.QPalette.ColorRole.ToolTipText, TEXT),
        (QtGui.QPalette.ColorRole.PlaceholderText, TEXT_DIM),
    ):
        palette.setColor(role, QtGui.QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(QSS)
