# PyInstaller spec: builds a single-file "VN Reader.exe".  Run build_exe.bat.
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
for pkg in ("winrt", "winocr", "_soundfile_data", "_sounddevice_data", "sounddevice",
            "gender_guesser", "spellchecker",
            "soundfile"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("comtypes", filter=lambda n: ".test" not in n)
datas += [("assets/vnr.ico", "assets"), ("assets/vnr.png", "assets")]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "matplotlib", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VN Reader",
    icon="assets/vnr.ico",
    console=False,
    upx=False,
    runtime_tmpdir=None,
)
