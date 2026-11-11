"""m̂_a 附加质量反演 + 识别表读取工具。

2026-10-28 起，软件显示层的逐窗谱峰由 windlens_engine.csd 从原始加速度
自建（识别链两表均不满足显示需求，见 csd.py 头注）。本模块保留两件事：
 1. m_a_from_f1：由 MassLens 数据库 1/f² ∝ m_a 线性关系反演附加质量；
 2. load_peaks/timeline：论文链识别表的流式读取（测试与复核工具，
    含 win_id_start 机制应对同链表 Start_s 列损坏的变体）。
"""
from __future__ import annotations

import csv
from collections import defaultdict

import numpy as np

from . import config

# 论文链文摘版识别表（≤5 峰/窗，3.5 GB 原地引用）。
# 注意（2026-10-28 起）：软件显示层**不再**消费识别表——改由
# windlens_engine.csd 从原始加速度自建逐窗谱峰（原因：文摘版过稀，
# 全量重扫版 adaptive_rescan_peak_details.csv 是逐窗恒定的定值探针网格、
# 非逐窗数据；两处同链表还有 Start_s 全 0.0 的 bug）。
# 本 loader 仅留作工具与测试；win_id_start 机制保留给重扫表。
PEAK_DETAILS = config.DATA_DIR / "scan_peak_details.csv"
WIN_ID_START = False

# 频率跟踪候选集（Hz，论文识别口径，供无识别窗的兜底显示）
F1_REF = 0.6401          # m_a=0 裸模型基频（新模型 03_现行正确模型）


def _ma_slope(h5_path=None):
    """1/f1² 对 m_a 的斜率（s²/kg·m²），由 MassLens 数据库两点得。"""
    import h5py
    h5_path = h5_path or config.H5_PATH
    f0 = f2 = None
    with h5py.File(h5_path, "r") as h:
        for name in h["cases"]:
            ma = float(h["cases"][name].attrs["m_a"])
            f1 = float(h["cases"][name]["freqs"][0])
            if ma == 0.0:
                f0 = f1
            elif ma == 2.0:
                f2 = f1
    if f0 is None or f2 is None:
        raise RuntimeError("数据库缺 m_a=0/2.0 工况")
    return f0, (1.0 / f2 ** 2 - 1.0 / f0 ** 2) / 2.0


_F0_CACHE = {}


def m_a_from_f1(f1, h5_path=None):
    """由跟踪到的 f1 反演附加质量 m̂_a（kg/m²）；f1 高于裸基频时钳到 0。"""
    if "k" not in _F0_CACHE:
        _F0_CACHE["f0"], _F0_CACHE["k"] = _ma_slope(h5_path)
    f0, k = _F0_CACHE["f0"], _F0_CACHE["k"]
    return max((1.0 / f1 ** 2 - 1.0 / f0 ** 2) / k, 0.0)


def load_peaks(path=None, qualities=("A", "B"), f_range=(0.1, 3.0),
               amp_min=0.0, with_shape=False, win_id_start=None):
    """读 CSD-MAC 逐峰识别表（全量重扫 11.2M 行，流式筛选）。返回结构化数组。

    字段：start_s, freq (MeasFreq_Hz), amp (PeakAmplitude), mac (BestMAC),
    fe_mode (BestFE_ModeID), fe_freq (BestFE_Freq_Hz), quality, wind, wdir。
    with_shape=True 时返回 (arr, shapes (n,12) f4)：shapes 为该行实测
    CSD 形状幅值 UAbs_01..12（竖向分量口径，2026-10-28 与 FE CAD_uz 核对
    MAC 0.84~0.95）；缺列时形状置 NaN。
    win_id_start=True 时窗键从 WindowID 恢复（start_s=(WindowID-1)×60），
    用于 Start_s 列损坏（全 0.0）的全量重扫表；None 时按 PEAK_DETAILS 口径自动。
    """
    path = path or PEAK_DETAILS
    if win_id_start is None:
        win_id_start = WIN_ID_START if path == PEAK_DETAILS else False
    keys = ["MeasFreq_Hz", "PeakAmplitude", "BestMAC",
            "BestFE_ModeID", "BestFE_Freq_Hz", "QualityLevel",
            "WindMean_mps", "WindDirMean_deg"]
    if not win_id_start:
        keys.insert(0, "Start_s")
    out = {k: [] for k in keys}
    if win_id_start:
        out["Start_s"] = []
    shapes = [] if with_shape else None
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        rd = csv.reader(fh)
        hdr = next(rd)
        idx = {k: hdr.index(k) for k in keys}
        iw = hdr.index("WindowID") if win_id_start else -1
        iu = []
        if with_shape:
            for k_ in range(1, 13):
                try:
                    iu.append(hdr.index(f"UAbs_{k_:02d}"))
                except ValueError:
                    iu.append(-1)
        need = max(list(idx.values()) + [iw] + (iu if with_shape else [])) + 1
        qset = set(qualities)
        for r in rd:
            if len(r) < need or r[idx["QualityLevel"]] not in qset:
                continue
            try:
                fq = float(r[idx["MeasFreq_Hz"]])
                amp = float(r[idx["PeakAmplitude"]])
            except ValueError:
                continue
            if not (f_range[0] <= fq <= f_range[1]) or amp < amp_min:
                continue
            for k in keys:
                out[k].append(r[idx[k]])
            if win_id_start:
                try:
                    out["Start_s"].append((float(r[iw]) - 1.0) * 60.0)
                except ValueError:
                    out["Start_s"].append("nan")
            if with_shape:
                row_s = []
                for j in iu:
                    try:
                        row_s.append(float(r[j]) if j >= 0 else float("nan"))
                    except ValueError:
                        row_s.append(float("nan"))
                shapes.append(row_s)
    n = len(out["Start_s"])
    dt = np.dtype([("start_s", "f8"), ("freq", "f8"), ("amp", "f8"),
                   ("mac", "f8"), ("fe_mode", "f8"), ("fe_freq", "f8"),
                   ("quality", "U1"), ("wind", "f8"), ("wdir", "f8")])
    arr = np.zeros(n, dtype=dt)
    arr["start_s"] = [float(x) for x in out["Start_s"]]
    arr["freq"] = [float(x) for x in out["MeasFreq_Hz"]]
    arr["amp"] = [float(x) for x in out["PeakAmplitude"]]
    arr["mac"] = [float(x) if x else 0.0 for x in out["BestMAC"]]
    arr["fe_mode"] = [float(x) if x else 0.0 for x in out["BestFE_ModeID"]]
    arr["fe_freq"] = [float(x) if x else 0.0 for x in out["BestFE_Freq_Hz"]]
    arr["quality"] = out["QualityLevel"]
    arr["wind"] = [float(x) if x else float("nan") for x in out["WindMean_mps"]]
    arr["wdir"] = [float(x) if x else float("nan") for x in out["WindDirMean_deg"]]
    order = np.argsort(arr["start_s"], kind="stable")
    arr = arr[order]
    if with_shape:
        shapes = np.asarray(shapes, dtype=np.float32)[order]
        return arr, shapes
    return arr


def timeline(peaks, start_s=None, end_s=None, top_n=5, f1_band=(0.40, 0.70)):
    """聚合逐窗时间线。

    每窗记录：start_s, wind, wdir, f1（f1_band 内最强峰频率）, m_a_hat,
    peaks（top_n 个 dict: freq/amp/mac/fe_mode/quality）。
    """
    if start_s is not None:
        peaks = peaks[peaks["start_s"] >= start_s - 1e-9]
    if end_s is not None:
        peaks = peaks[peaks["start_s"] < end_s - 1e-9]
    by_win = defaultdict(list)
    for r in peaks:
        by_win[float(r["start_s"])].append(r)
    records = []
    for ws in sorted(by_win):
        rows = sorted(by_win[ws], key=lambda r: -float(r["amp"]))[:top_n]
        cand = [r for r in rows if f1_band[0] <= float(r["freq"]) <= f1_band[1]]
        f1 = float(cand[0]["freq"]) if cand else float("nan")
        records.append({
            "start_s": ws,
            "wind": float(rows[0]["wind"]),
            "wdir": float(rows[0]["wdir"]),
            "f1": f1,
            "m_a_hat": m_a_from_f1(f1) if cand else float("nan"),
            "peaks": [{"freq": float(r["freq"]), "amp": float(r["amp"]),
                       "mac": float(r["mac"]),
                       "fe_mode": int(r["fe_mode"]) if r["fe_mode"] else 0,
                       "quality": str(r["quality"])} for r in rows]})
    return records
