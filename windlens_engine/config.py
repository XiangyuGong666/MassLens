from __future__ import annotations

import csv
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = PROJECT_ROOT / "examples" / "demo"
DATA_DIR = Path(os.environ.get("WINDLENS_DATA_DIR", PROJECT_ROOT / "monitoring"))
OUT_DIR = Path(os.environ.get("WINDLENS_OUTPUT_DIR", DEMO_DIR))
SENSOR_MAP_CSV = Path(os.environ.get("WINDLENS_SENSOR_MAP", OUT_DIR / "sensor_map.csv"))

# v7.3 (HDF5) 大文件：(路径, 变量名, 通道数)
MAT = {
    "gnss": (DATA_DIR / "analyGNSS_droperror_dropbaseline_downsample10hz_22_10-11-12.mat",
             "analyGNSS_dropbaseline_downsample", 21),
    "acc": (DATA_DIR / "analyacc_22_101112_dropbaseline_downsample10hz.mat",
            "analyacc_downsample", 36),
    "pressure": (DATA_DIR / "windpressure_22101112_dropbaseline_downsample_10hz_maidongfeng0002.mat",
                 "analywindpressure_downsample", 12),
}
# v5 小文件（scipy.io）
V5_MAT = {
    "time": (DATA_DIR / "analytime_22_10-11-12_downsample_10hz.mat", "analytime_downsample"),
    "speed": (DATA_DIR / "analywindspeed_downsample_10hz.mat", "analywindspeed_downsample"),
    "direct": (DATA_DIR / "analywinddirect_downsample_10hz.mat", "analywinddirect_downsample"),
}

SCAN_CSV = DATA_DIR / "banben10" / "results" / "scan_windows_all.csv"
V450_CSV = DATA_DIR / "modal_sensor_response_v450.csv"
# 新模型 600 阶 × 12 测点响应表（CAD 分量序 ux,uy,uz；显示系需置换为 (uy,uz,ux)）
SENSOR600_CSV = DATA_DIR / "sensor_response_600.csv"

H5_PATH = PROJECT_ROOT / "data" / "masslens_shed_v2.h5"

FS = 10.0                # 采样率 Hz
N_SAMPLES = 67285219     # 共时间轴总长

# 结构频带（Hz）：新模型前 20 阶模态 0.64~1.9 Hz，带宽留余量
BAND_ACC = (0.1, 3.0)    # 加速度带通
HP_GNSS = 0.05           # GNSS 高通去漂


def load_sensor_map(path=None):
    """sensor_map → list[dict]（跳过 # 注释）。

    字段：loc(int), fe_x/y/z（显示系）, gnss_point(int, 0=无 GNSS)。
    v2 表另带 node_row/ansys_node（新模型精确节点对应，无需最近邻）；
    matlab_x/y 仅旧表有，缺失时回退为 fe 坐标。
    GNSS 点号 k 对应通道 3k-2/3k-1/3k（x/y/z）；加速度 loc k 对应通道 3k-2/3k-1/3k。
    """
    path = path or SENSOR_MAP_CSV
    with open(path, encoding="utf-8") as fh:
        lines = [ln for ln in fh if ln.strip() and not ln.startswith("#")]
    out = []
    for row in csv.DictReader(lines):
        fe = (float(row["fe_x_m"]), float(row["fe_y_height_m"]),
              float(row["fe_z_m"]))
        out.append({
            "loc": int(row["loc"]),
            "matlab_x": float(row.get("matlab_x_m") or fe[0]),
            "matlab_y": float(row.get("matlab_y_m") or fe[1]),
            "fe": fe,
            "gnss_point": int(row["gnss_point"]),
            "node_row": int(row["node_row"]) if row.get("node_row") else None,
            "ansys_node": int(row["ansys_node"]) if row.get("ansys_node") else None,
        })
    return out
