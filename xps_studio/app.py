"""Ponto de entrada do XPS Sample Studio."""
import os
import sys


def main():
    from PySide6.QtGui import QFont, QFontDatabase, QIcon
    from PySide6.QtWidgets import QApplication

    from .ui.main_window import APP_NAME, MainWindow
    from .ui.theme import QSS

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("XPSSampleStudio")
    app.setStyle("Fusion")
    fonts = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")
    if os.path.isdir(fonts):
        for fn in os.listdir(fonts):
            if fn.endswith(".ttf"):
                QFontDatabase.addApplicationFont(os.path.join(fonts, fn))
    f = QFont()
    f.setFamilies(["Inter", "SF Pro Text", "Segoe UI", "Helvetica Neue", "Arial"])
    f.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(f)
    app.setStyleSheet(QSS)
    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    w = MainWindow()
    w.show()
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        w.open_path(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
