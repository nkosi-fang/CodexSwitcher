# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

project_root = Path(sys.argv[0]).resolve().parent
icon_path = project_root / "icon_app.ico"

all_datas = []
all_datas.append((str(project_root / "icon_tray.png"), "."))
all_binaries = []
all_hidden = []
for pkg in ("qt_material",):
    datas, binaries, hidden = collect_all(pkg)
    all_datas += datas
    all_binaries += binaries
    all_hidden += hidden

block_cipher = None


a = Analysis(
    [str(project_root / "pyside_switcher.py")],
    pathex=[str(project_root)],
    binaries=all_binaries,
    datas=all_datas,
    hiddenimports=all_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6.Qt3DAnimation","PySide6.Qt3DCore","PySide6.Qt3DExtras","PySide6.Qt3DInput","PySide6.Qt3DLogic","PySide6.Qt3DRender","PySide6.QtCharts","PySide6.QtDataVisualization","PySide6.QtGraphs","PySide6.QtGraphsWidgets","PySide6.QtDesigner","PySide6.QtHelp","PySide6.QtLocation","PySide6.QtMultimedia","PySide6.QtNetworkAuth","PySide6.QtNfc","PySide6.QtPdf","PySide6.QtPositioning","PySide6.QtPrintSupport","PySide6.QtQml","PySide6.QtQuick","PySide6.QtQuick3D","PySide6.QtQuickControls2","PySide6.QtQuickDialogs2","PySide6.QtQuickEffects","PySide6.QtQuickLayouts","PySide6.QtQuickParticles","PySide6.QtQuickShapes","PySide6.QtQuickTest","PySide6.QtQuickTimeline","PySide6.QtQuickVectorImage","PySide6.QtQuickWidgets","PySide6.QtRemoteObjects","PySide6.QtScxml","PySide6.QtSensors","PySide6.QtSerialBus","PySide6.QtSerialPort","PySide6.QtSpatialAudio","PySide6.QtSvg","PySide6.QtTest","PySide6.QtTextToSpeech","PySide6.QtUiTools","PySide6.QtVirtualKeyboard","PySide6.QtWebChannel","PySide6.QtWebEngineCore","PySide6.QtWebEngineQuick","PySide6.QtWebEngineWidgets","PySide6.QtWebSockets","PySide6.QtWebView"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="CodexSwitcher_v2",
    icon=str(icon_path),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
)
