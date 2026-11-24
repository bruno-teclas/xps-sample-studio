"""Tema escuro moderno (inspirado nos samplers e DAWs atuais)."""

ACCENT = "#FF6B3D"
ACCENT_HOVER = "#FF845C"
ACCENT_SOFT = "#3A2219"
ACCENT_2 = "#FFB547"
BG = "#0E0F13"
PANEL = "#14161B"
CARD = "#181B21"
CARD_2 = "#20242C"
RAISED = "#262B34"
BORDER = "#2A2F39"
BORDER_SOFT = "#20242C"
TEXT = "#ECEEF3"
MUTED = "#8B91A0"
FAINT = "#5B6170"
OK = "#3DDC97"
WARN = "#FFB547"
DANGER = "#FF5C5C"

PART_COLORS = ["#FF6B3D", "#FFB547", "#3DDC97", "#3DB4FF", "#9B7BFF", "#FF5C9A",
               "#47E0D0", "#C6E04A", "#FF8F5C", "#7FA7FF", "#E07BFF", "#5CFFB0",
               "#FFD15C", "#5CC8FF", "#FF7B7B", "#A0FF5C"]

GROUP_COLORS = ["#FF6B3D", "#3DB4FF", "#3DDC97", "#9B7BFF", "#FFB547", "#FF5C9A", "#47E0D0", "#C6E04A"]

QSS = f"""
* {{ font-family: "Inter"; font-size: 13px; color: {TEXT}; }}
QMainWindow, QWidget#root, QWidget#page, QStackedWidget {{ background: {BG}; }}
QWidget#sidebar {{ background: {PANEL}; border-right: 1px solid {BORDER_SOFT}; }}
QWidget#topbar {{ background: {BG}; border-bottom: 1px solid {BORDER_SOFT}; }}
QLabel#brand {{ font-size: 16px; font-weight: 700; letter-spacing: -0.2px; }}
QLabel#brandSub, QLabel#section {{ color: {FAINT}; font-size: 10.5px; font-weight: 600; letter-spacing: 1.2px; }}
QLabel#h1 {{ font-size: 22px; font-weight: 700; letter-spacing: -0.4px; }}
QLabel#h2 {{ font-size: 14px; font-weight: 600; }}
QLabel#h3 {{ font-size: 12px; font-weight: 600; color: {MUTED}; }}
QLabel#muted, QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel#stat {{ font-size: 22px; font-weight: 700; letter-spacing: -0.4px; }}
QLabel#statLabel {{ color: {MUTED}; font-size: 11px; }}
QLabel#badge {{ background: {ACCENT_SOFT}; color: {ACCENT}; border-radius: 9px; padding: 2px 9px; font-size: 11px; font-weight: 600; }}
QFrame#card {{ background: {CARD}; border: 1px solid {BORDER_SOFT}; border-radius: 14px; }}
QFrame#panel {{ background: {PANEL}; border: 1px solid {BORDER_SOFT}; border-radius: 12px; }}
QFrame#drop {{ background: {CARD}; border: 1.5px dashed {BORDER}; border-radius: 18px; }}
QFrame#drop[hover="true"] {{ border-color: {ACCENT}; background: #1F1714; }}
QFrame#sep {{ background: {BORDER_SOFT}; max-height: 1px; min-height: 1px; border: none; }}

QPushButton#nav {{ text-align: left; padding: 9px 12px; border: none; border-radius: 9px;
                   background: transparent; color: {MUTED}; font-weight: 500; }}
QPushButton#nav:hover {{ background: {CARD}; color: {TEXT}; }}
QPushButton#nav:checked {{ background: {CARD_2}; color: {TEXT}; font-weight: 600; }}
QPushButton#nav:disabled {{ color: #3E4350; }}

QPushButton, QToolButton {{ background: {CARD_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 13px; font-weight: 500; }}
QPushButton:hover, QToolButton:hover {{ background: {RAISED}; border-color: #3A404C; }}
QPushButton:pressed, QToolButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled, QToolButton:disabled {{ color: #4E5462; background: {CARD}; border-color: {BORDER_SOFT}; }}
QPushButton#primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: #170E0A; font-weight: 700; padding: 8px 18px; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#primary:disabled {{ background: #4A2D23; border-color: #4A2D23; color: #8E7A73; }}
QPushButton#ghost, QToolButton#ghost {{ background: transparent; border: 1px solid transparent; }}
QPushButton#ghost:hover, QToolButton#ghost:hover {{ background: {CARD_2}; border-color: {BORDER}; }}
QToolButton#tool {{ padding: 5px 9px; border-radius: 7px; }}
QToolButton#tool:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton#seg {{ border-radius: 0; padding: 6px 12px; }}
QPushButton#seg:checked {{ background: {ACCENT}; color: #170E0A; border-color: {ACCENT}; font-weight: 700; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {PANEL}; border: 1px solid {BORDER};
    border-radius: 8px; padding: 6px 9px; selection-background-color: {ACCENT}; selection-color: #170E0A; }}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: #3A404C; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit#title {{ font-size: 18px; font-weight: 700; background: transparent; border: 1px solid transparent; padding: 4px 6px; }}
QLineEdit#title:hover {{ border-color: {BORDER}; }}
QLineEdit#title:focus {{ border-color: {ACCENT}; background: {PANEL}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {CARD}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; outline: none; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: #FFFFFF; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid {BORDER}; background: {PANEL}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QListWidget, QTableWidget, QTreeWidget {{ background: {PANEL}; border: 1px solid {BORDER_SOFT}; border-radius: 10px;
    alternate-background-color: #16181E; gridline-color: transparent; outline: none; }}
QListWidget::item {{ padding: 7px 9px; border-radius: 7px; margin: 1px 3px; }}
QListWidget::item:hover {{ background: {CARD_2}; }}
QListWidget::item:selected, QTableWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QHeaderView::section {{ background: {PANEL}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER_SOFT};
    padding: 8px; font-weight: 600; font-size: 11px; }}
QTableCornerButton::section {{ background: {PANEL}; border: none; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 14px; border: none; border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}

QProgressBar {{ background: {BORDER}; border: none; border-radius: 3px; height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3A404C; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 4px; min-width: 30px; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px 8px; border-radius: 6px; }}
QScrollArea {{ border: none; background: transparent; }}
QMenu {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 5px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; }}
QSplitter::handle {{ background: {BG}; }}
QSplitter::handle:hover {{ background: {BORDER}; }}
QDialog {{ background: {BG}; }}
"""
