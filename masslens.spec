# Build from a Windows venv with PyInstaller installed.
from pathlib import Path

ROOT = Path(SPECPATH).resolve()
datas = [(str(ROOT / "masslens_ui" / "static"), "masslens_ui/static"),
         (str(ROOT / "examples" / "demo"), "examples/demo")]
for filename in ("masslens_shed_v2.h5", "manifest_v2.json", "interp_error.json"):
    datas.append((str(ROOT / "data" / filename), "data"))
datas.append((str(ROOT / "LICENSE"), "."))

a = Analysis(
    [str(ROOT / "_launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    excludes=["matplotlib", "pandas", "PIL"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MassLens",
          console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="MassLens", upx=False)
