"""数据束打包：112,143 窗的连续层 + 识别层 + m̂_a → npz + 客户端二进制束。

用法（项目根目录）：python -m windlens_engine.bundle
产物：
  data_wind/window_bundle.npz   —— Python 侧复核用
  data_wind/window_bundle.bin   —— 客户端一次性下载（服务器 /api/wind/bundle 直接转发）

二进制布局（小端）：
  [u32 头长][头部 JSON（4 字节对齐）][float32 数组段 …]
  头部声明各数组的名字、形状与顺序；数组顺序固定见代码内 LAYOUT。
逐窗峰：每窗 top5 × (freq, amp, mac, quality) float32，无峰填 NaN。
"""
from __future__ import annotations

import argparse
import csv
import json
import struct

import numpy as np

from . import config, participate

LAYOUT = ["start_s", "wind", "wind_max", "wdir", "wdir_std", "acc_rms",
          "peaks", "m_a", "track_len", "peaks_shape", "press_rms", "gnss_rms"]
TOPN = 5


def load_scan_csv():
    keys = ["WindowID", "Start_s", "WindMean_mps", "WindMax_mps",
            "WindDirMean_deg", "WindDirStd_deg", "AccelerationRMS"]
    rows = {k: [] for k in keys}
    with open(config.SCAN_CSV, encoding="utf-8-sig", errors="replace",
              newline="") as fh:
        rd = csv.reader(fh)
        hdr = next(rd)
        idx = {k: hdr.index(k) for k in keys}
        need = max(idx.values()) + 1
        for r in rd:
            if len(r) < need:
                continue
            try:
                vals = [float(r[idx[k]]) for k in keys]
            except ValueError:
                continue
            for k, v in zip(keys, vals):
                rows[k].append(v)
    n = len(rows["Start_s"])
    out = {k: np.array(v[:n], dtype=np.float64) for k, v in rows.items()}
    return out, n


def _dedupe_candidates(arr, shapes):
    """(start_s, round(freq,4)) 去重，保 BestMAC 最高行；shapes 随行。

    输入已按 start_s 排序（load_peaks 稳定序）；输出恢复该序。
    """
    key_f = np.round(arr["freq"], 4)
    order = np.lexsort((-arr["mac"], key_f, arr["start_s"]))
    ks, kf = arr["start_s"][order], key_f[order]
    first = np.ones(len(order), dtype=bool)
    first[1:] = (ks[1:] != ks[:-1]) | (kf[1:] != kf[:-1])
    sel = np.sort(order[first])
    if shapes is not None:
        return arr[sel], shapes[sel]
    return arr[sel], None


def aggregate_peaks(n_win, win0_starts):
    """识别层：csd 逐窗谱峰 top5/窗（带实测形状）；基频带峰 → m̂_a；
    谱峰轨迹（经验去噪）→ 槽位轨迹长度 + 事件。

    数据源（第 18 晚起）：windlens_engine.csd 从原始 12 竖向加速度通道提取
    （识别链文摘/重扫两表均不可用：文摘每窗 ≤5 峰，重扫是定值探针网格）。
    返回 (peaks (n_win,5,4) float32, peaks_shape (n_win,5,12) float32,
          m_a (n_win,) float32, track_len (n_win,5) float32,
          events list, track_stats dict)。
    peaks 槽内 quality 编码 0=A / 1=B / 2=C（csd 自定义级：
    A=匹配≥0.5 且功率比≥8；B=匹配≥0.3 且功率比≥4；C=其余入选峰）。
    """
    from . import csd, tracks as _tr
    cs = csd.load()
    n = min(n_win, cs["freq"].shape[0])
    peaks = np.full((n_win, TOPN, 4), np.nan, dtype=np.float32)
    peaks_shape = np.full((n_win, TOPN, 12), np.nan, dtype=np.float32)
    peaks[:n, :, 0] = cs["freq"][:n, :TOPN]
    peaks[:n, :, 1] = cs["amp"][:n, :TOPN]
    peaks[:n, :, 2] = cs["mac"][:n, :TOPN]
    peaks[:n, :, 3] = cs["quality"][:n, :TOPN]
    peaks_shape[:n] = cs["shape"][:n, :TOPN]

    # ---- m̂_a：基频带（0.40~0.70）内显著（功率比≥4）最强峰 → 反演 ----
    m_a = np.full(n_win, np.nan, dtype=np.float32)
    bf, bp, ba = cs["freq"][:n], cs["prom"][:n], cs["amp"][:n]
    in_band = (bf >= 0.40) & (bf <= 0.70) & (bp >= 4.0)
    band_amp = np.where(in_band, ba, -1.0)
    jbest = band_amp.argmax(axis=1)
    has = band_amp[np.arange(n), jbest] > 0
    for i in np.where(has)[0]:
        m_a[i] = participate.m_a_from_f1(float(bf[i, jbest[i]]))

    # ---- 轨迹（经验去噪）：谱峰链接 → 槽位轨迹长度 + 事件 ----
    tr, slot_tr = _tr.build_tracks(cs["freq"][:n], cs["amp"][:n],
                                   cs["mac"][:n], cs["quality"][:n])
    stats = _tr.summarize(tr)
    events = _tr.make_events(tr, min_len=60, n_win=n)  # 事件取 ≥1h 轨迹
    _, tlen = _tr.slot_track_info(peaks, None, slot_tr, tr)
    np.savez_compressed(config.OUT_DIR / "tracks_summary.npz",
                        f_med=[t["f_med"] for t in tr],
                        birth_w=[t["birth_w"] for t in tr],
                        death_w=[t["death_w"] for t in tr],
                        n_win=[t["n_win"] for t in tr],
                        amp_med=[t["amp_med"] for t in tr],
                        mac_med=[t["mac_med"] for t in tr],
                        best_q=[t["best_q"] for t in tr])
    print(f"轨迹: 总 {stats['n_tracks']}，确认 {stats['n_confirmed']}，"
          f"长度 p50={stats['len_p50']:.0f} p90={stats['len_p90']:.0f} "
          f"max={stats['len_max']}；事件(≥1h 轨迹) {len(events)}")
    return peaks, peaks_shape, m_a, tlen, events, stats


def build():
    scan, n_scan = load_scan_csv()
    # 对齐窗数：scan CSV 尾部与流式 RMS 的整窗数可能差 1，取交集
    n_rms = []
    for p in (config.OUT_DIR / "window_rms_pressure.npz",
              config.OUT_DIR / "window_rms_gnss.npz"):
        if p.exists():
            n_rms.append(np.load(p)["rms"].shape[0])
    n_win = min([n_scan] + n_rms) if n_rms else n_scan
    scan = {k: v[:n_win] for k, v in scan.items()}
    print(f"连续层: {n_win} 窗（scan 原 {n_scan}）")
    starts = scan["Start_s"]
    peaks, peaks_shape, m_a, tlen, events, _stats = aggregate_peaks(n_win, starts)
    print(f"识别层: 有峰窗 {np.sum(~np.isnan(peaks[:, 0, 1]))}, "
          f"有 m̂_a 窗 {np.sum(~np.isnan(m_a))}")

    arrays = {
        "start_s": starts.astype(np.float32),
        "wind": scan["WindMean_mps"].astype(np.float32),
        "wind_max": scan["WindMax_mps"].astype(np.float32),
        "wdir": scan["WindDirMean_deg"].astype(np.float32),
        "wdir_std": scan["WindDirStd_deg"].astype(np.float32),
        "acc_rms": scan["AccelerationRMS"].astype(np.float32),
        "peaks": peaks,
        "m_a": m_a,
        "track_len": tlen,
        "peaks_shape": peaks_shape,
    }
    for key, path in (("press_rms", config.OUT_DIR / "window_rms_pressure.npz"),
                      ("gnss_rms", config.OUT_DIR / "window_rms_gnss.npz")):
        if path.exists():
            r = np.load(path)["rms"].astype(np.float32)
            arrays[key] = r[:n_win]
            print(f"{key}: {r.shape} 并入")
        else:
            arrays[key] = np.full((n_win, 12 if key == "press_rms" else 21),
                                  np.nan, dtype=np.float32)
            print(f"{key}: 缺文件，NaN 占位（先跑 window_stats）")

    np.savez_compressed(config.OUT_DIR / "window_bundle.npz", **arrays)

    header = {"n_win": int(n_win), "win_s": 60.0, "topn": TOPN,
              "layout": LAYOUT,
              "shapes": {k: list(v.shape) for k, v in arrays.items()},
              "events": events,   # [[全轴窗号, 0出生/1消亡/2迁移, freq], ...]
              "track_min_len": 20,
              "notes": "press_rms=脉动风压强度(去基线,非绝对风压); "
                       "gnss_rms=GNSS动态位移强度(单位未标定); "
                       "peaks 每窗 top5×(freq,amp,mac,quality) NaN 填充, "
                       "quality 0=A/1=B/2=C; track_len=峰所属轨迹长度(窗), "
                       "0=未入轨迹; m_a=基频带峰反演附加质量 kg/m²"}
    h = json.dumps(header).encode("utf-8")
    h += b" " * ((-4 - len(h)) % 4)
    parts = [struct.pack("<I", len(h)), h]
    for k in LAYOUT:
        parts.append(np.ascontiguousarray(arrays[k], dtype=np.float32).tobytes())
    (config.OUT_DIR / "window_bundle.bin").write_bytes(b"".join(parts))
    print(f"输出: {config.OUT_DIR}/window_bundle.npz / .bin")


if __name__ == "__main__":
    argparse.ArgumentParser(prog="windlens_engine.bundle").parse_args()
    build()
