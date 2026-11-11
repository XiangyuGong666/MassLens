"""路径与参数配置（第 1 晚锁定）。"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APDL_DIR = PROJECT_ROOT / "apdl"
JOBS_DIR = PROJECT_ROOT / "jobs"
DATA_DIR = PROJECT_ROOT / "data"

MAPDL_EXE = os.environ.get("MASSLENS_MAPDL_EXE", "ANSYS242.exe")
WORK_DIR = Path(os.environ.get("MASSLENS_MAPDL_WORK_DIR", PROJECT_ROOT / "mapdl-work"))
MESH_CDB = WORK_DIR / "mesh_new.cdb"
BASE_JOBNAME = "run_pressure_only"

SHELL_THICKNESS = 0.0012   # m（膜厚 1.2 mm）
MEMBRANE_DENS = 1200.0     # kg/m³（膜材密度，附加质量只加膜面）
MEMBRANE_MASS0 = 70392.8   # kg（新模型膜面质量：48883.9 m² × 0.0012 × 1200，2026-10-27 换型）
N_MODES = 20               # 每工况导出阶数
NP = 8                     # MAPDL SMP 核数

# 扫描网格（新模型：含已验证参考点 0 / 1.333）
GRID_MA = [0.0, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9, 1.0, 1.1695, 1.333,
           1.5, 1.65, 1.8, 1.9, 2.0]  # kg/m^2

H5_PATH = DATA_DIR / "masslens_shed_v2.h5"
MANIFEST_PATH = DATA_DIR / "manifest_v2.json"

# 已知参考点（新模型 03_现行正确模型，用于自动验收）
F1_REF_MA0 = 0.6401        # m_a=0 基频 Hz（新模型裸态）
MA_REF = 1.333             # kg/m²（新模型附加质量参考点）
F1_REF_MA = 0.550          # m_a=1.333 时 f1 Hz（与实测一致）
F1_TOL = 0.005             # 验收容差 ±0.5%

# 左栏预设工况（m_a 数值为占位，待确认后在 manifest 中修订）
PRESETS = {"无附加质量": 0.0, "小雨": 0.3, "中雨": 0.9, "大雨": 2.0}
