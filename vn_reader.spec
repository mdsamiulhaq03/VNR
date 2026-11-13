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
datas += [("assets/fonts", "assets/fonts"), ("assets/icons", "assets/icons")]

# PaddleOCR (PP-OCRv6 small via RapidOCR + ONNX Runtime with DirectML for the GPU). Only the 3 models the app uses are
# bundled (the package folder may hold other downloaded models).
KEEP_MODELS = {"PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx"}
for pkg in ("rapidocr", "onnxruntime", "omegaconf", "pyclipper", "shapely"):
    try:
        d, b, h = collect_all(pkg, filter_submodules=lambda n: ".pytorch" not in n and
                              ".paddle" not in n and ".tensorrt" not in n and
                              ".openvino" not in n and ".mnn" not in n)
    except Exception:
        continue
    d = [(src, dst) for src, dst in d
         if not (src.endswith(".onnx") and src.replace("\\", "/").split("/")[-1]
                 not in KEEP_MODELS)]
    datas += d
    binaries += b
    hiddenimports += h

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
