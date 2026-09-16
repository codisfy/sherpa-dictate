# PyInstaller specification for the Linux desktop bundle.

from PyInstaller.utils.hooks import collect_all


sherpa_datas, sherpa_binaries, sherpa_hidden = collect_all("sherpa_onnx")

a = Analysis(
    ["sherpa_entry.py"],
    pathex=["."],
    binaries=sherpa_binaries,
    datas=sherpa_datas
    + [
        ("config.toml", "."),
        ("assets/sherpa.svg", "assets"),
        ("assets/spin-up.svg", "assets"),
        ("assets/spin-down.svg", "assets"),
        ("LICENSE", "."),
        ("THIRD_PARTY_NOTICES.md", "."),
        ("licenses", "licenses"),
        ("/usr/share/common-licenses/LGPL-3", "licenses"),
    ],
    hiddenimports=sherpa_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="sherpa",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="sherpa",
)
