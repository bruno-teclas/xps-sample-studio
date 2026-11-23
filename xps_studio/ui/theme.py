"""Tema escuro moderno."""

ACCENT = "#FF6A3D"
ACCENT_2 = "#FFB547"
BG = "#121318"
PANEL = "#1A1C23"
CARD = "#20232C"
CARD_2 = "#272B36"
BORDER = "#2E3240"
TEXT = "#E9EBF1"
MUTED = "#8C92A4"
OK = "#3DDC97"
WARN = "#FFB547"

PART_COLORS = ["#FF6A3D", "#FFB547", "#3DDC97", "#3DB4FF", "#9B7BFF", "#FF5C9A",
               "#47E0D0", "#C6E04A", "#FF8F5C", "#7FA7FF", "#E07BFF", "#5CFFB0",
               "#FFD15C", "#5CC8FF", "#FF7B7B", "#A0FF5C"]

QSS = f"""
* {{ font-size: 13px; color: {TEXT}; }}
QMainWindow, QWidget#root, QWidget#page, QStackedWidget {{ background: {BG}; }}
QWidget#sidebar {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
QLabel#brand {{ font-size: 18px; font-weight: 700; }}
QLabel#brandSub {{ color: {MUTED}; font-size: 11px; letter-spacing: 1px; }}
QLabel#h1 {{ font-size: 22px; font-weight: 700; }}
QLabel#h2 {{ font-size: 15px; font-weight: 600; }}
QLabel#muted, QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel#stat {{ font-size: 20px; font-weight: 700; }}
QLabel#statLabel {{ color: {MUTED}; font-size: 11px; }}
QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#drop {{ background: {CARD}; border: 2px dashed {BORDER}; border-radius: 18px; }}
QFrame#drop[hover="true"] {{ border-color: {ACCENT}; background: #2A221F; }}

QPushButton#nav {{ text-align: left; padding: 11px 14px; border: none; border-radius: 10px;
                   background: transparent; color: {MUTED}; font-weight: 600; }}
QPushButton#nav:hover {{ background: {CARD}; color: {TEXT}; }}
QPushButton#nav:checked {{ background: {CARD_2}; color: {TEXT}; border-left: 3px solid {ACCENT}; }}
QPushButton#nav:disabled {{ color: #4A4F5E; }}

QPushButton {{ background: {CARD_2}; border: 1px solid {BORDER}; border-radius: 9px; padding: 8px 14px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #5A5F6E; border-color: {BORDER}; }}
QPushButton#primary {{ background: {ACCENT}; border: none; color: #16110F; font-weight: 700; padding: 10px 20px; }}
QPushButton#primary:hover {{ background: #FF8058; }}
QPushButton#primary:disabled {{ background: #5A3A30; color: #8E7A73; }}
QPushButton#seg {{ border-radius: 0; padding: 7px 12px; }}
QPushButton#seg:checked {{ background: {ACCENT}; color: #16110F; border-color: {ACCENT}; font-weight: 700; }}
QPushButton#icon {{ padding: 6px 10px; border-radius: 16px; font-size: 14px; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {BG}; border: 1px solid {BORDER};
    border-radius: 8px; padding: 6px 8px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {CARD}; border: 1px solid {BORDER}; selection-background-color: {ACCENT}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; }}

QSlider::groove:horizontal {{ height: 6px; background: {BORDER}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {BORDER}; background: {BG}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QListWidget, QTableWidget {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px;
    alternate-background-color: #1D2028; gridline-color: transparent; outline: none; }}
QListWidget::item {{ padding: 8px 10px; border-radius: 8px; }}
QListWidget::item:selected, QTableWidget::item:selected {{ background: #3A2A24; color: {TEXT}; }}
QHeaderView::section {{ background: {CARD}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 8px; font-weight: 600; }}
QTableCornerButton::section {{ background: {CARD}; border: none; }}

QProgressBar {{ background: {BORDER}; border: none; border-radius: 4px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 4px; min-width: 30px; }}
QToolTip {{ background: {CARD_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; border-radius: 6px; }}
QScrollArea {{ border: none; background: transparent; }}
"""
