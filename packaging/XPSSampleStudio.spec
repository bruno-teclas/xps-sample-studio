# -*- mode: python ; coding: utf-8 -*-
# Gera o aplicativo com: pyinstaller packaging/XPSSampleStudio.spec
import os, sys
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
excludes = ["tkinter", "matplotlib", "pkg_resources", "setuptools", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
            "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtPdf", "PySide6.QtCharts"]

a = Analysis([os.path.join(ROOT, "run.py")], pathex=[ROOT],
             datas=[(os.path.join(ROOT, "xps_studio", "assets"), os.path.join("xps_studio", "assets"))],
             hiddenimports=["PySide6.QtMultimedia", "PySide6.QtSvg", "scipy.signal", "scipy.special._cdflib", "importlib.resources", "importlib.metadata", "scipy.stats._sobol", "scipy._lib.messagestream"],
             excludes=excludes, noarchive=False)
pyz = PYZ(a.pure)
icon = os.path.join(SPECPATH, "icon.icns" if sys.platform == "darwin" else "icon.ico")
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="XPS Sample Studio",
          console=False, icon=icon, target_arch=os.environ.get("TARGET_ARCH") or None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="XPS Sample Studio")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="XPS Sample Studio.app", icon=icon,
                 bundle_identifier="com.xpssamplestudio.app",
                 info_plist={"CFBundleShortVersionString": "1.0.0", "NSHighResolutionCapable": True,
                             "LSMinimumSystemVersion": "11.0",
                             "CFBundleDocumentTypes": [{"CFBundleTypeName": "Instrumento",
                                 "CFBundleTypeExtensions": ["nki", "sf2", "sfz"], "CFBundleTypeRole": "Viewer"}]})
