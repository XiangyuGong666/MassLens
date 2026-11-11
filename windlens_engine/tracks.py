"""模态谱峰轨迹跟踪：3 个月数据池上的"经验去噪"与事件（第 17~18 晚）。

口径（2026-10-28 定版）：峰来自 windlens_engine.csd 的逐窗谱峰提取
（逐窗真实检测峰，频率逐窗变化），把相邻窗 |Δf|≤TOL_F 的峰链接成轨迹；
真模态在时间上持续、噪声峰闪烁，轨迹长度即置信度。

- 续接：与"未闭合轨迹"中 f_last 最近者 |Δf|≤TOL_F(0.02 Hz≈1 谱 bin)
  且窗距 ≤GAP(5 窗) 则续接，否则开新轨迹。
- 确认轨迹 ≥ MIN_LEN 窗（默认 20 窗 = 20 min）；出生/消亡/迁移（消亡后
  SWITCH_GAP 窗内邻近频率出生）作为时间轴事件输出。
- 同一窗内同一轨迹只吸收一个峰（取频率最近者），防止同窗多峰虚增长度。
"""
from __future__ import annotations

import bisect
from collections import defaultdict

import numpy as np

TOL_F = 0.02     # Hz，续接频率容差
GAP = 5          # 窗，允许断检窗数
MIN_LEN = 20     # 窗，确认轨迹最短长度（20 min）
SWITCH_DF = (0.02, 0.08)   # 迁移事件的轨迹间频差范围
SWITCH_GAP = 10            # 迁移：消亡后多少窗内出生算同一簇


def build_tracks(freq_arr, amp_arr, mac_arr, q_arr, tol_f=TOL_F, gap=GAP):
    """(n_win,K) 谱峰数组 → 轨迹链接。

    返回 (spells, slot_track)：spells 为 dict 列表（id/f_med/birth_w/
    death_w/n_win/amp_med/mac_med/best_q）；slot_track (n_win,K) int32，
    该槽所属轨迹 id（-1=无）。
    """
    n_win, K = freq_arr.shape
    slot_track = np.full((n_win, K), -1, dtype=np.int32)
    open_keys = []          # 有序 f_last
    open_tr = {}            # f_last → [spell]
    spells = []

    def _finalize(tk):
        tk["f_med"] = float(np.median(tk["_fs"]))
        tk["amp_med"] = float(np.median(tk["_amps"]))
        tk["mac_med"] = float(np.median(tk["_macs"]))
        tk["death_w"] = tk.pop("_last_w")
        del tk["_fs"], tk["_amps"], tk["_macs"]
        spells.append(tk)

    for w in range(n_win):
        stale = [fk for fk in open_keys
                 if w - open_tr[fk][-1]["_last_w"] > gap]
        for fk in stale:
            for tk in open_tr.pop(fk):
                _finalize(tk)
            open_keys.remove(fk)
        used = set()        # 本窗已被吸收的轨迹 id（防同窗多峰虚增）
        for k in range(K):
            f = float(freq_arr[w, k])
            if not (f == f):
                continue
            kk = bisect.bisect_left(open_keys, f)
            cand = []
            if kk < len(open_keys):
                cand.append(open_keys[kk])
            if kk > 0:
                cand.append(open_keys[kk - 1])
            hit = None
            for fk in cand:
                if abs(fk - f) > tol_f:
                    continue
                tk = min(open_tr[fk], key=lambda t: w - t["_last_w"])
                if tk["id"] in used:
                    continue
                if w - tk["_last_w"] <= gap:
                    hit = (fk, tk)
                    break
            amp = float(amp_arr[w, k])
            mac = float(mac_arr[w, k])
            q = int(q_arr[w, k]) if q_arr[w, k] == q_arr[w, k] else 3
            if hit is None:
                tk = {"id": len(spells) + sum(len(b) for b in open_tr.values()),
                      "birth_w": w, "_last_w": w, "n_win": 1, "best_q": q,
                      "_fs": [f], "_amps": [amp], "_macs": [mac]}
                open_tr.setdefault(f, []).append(tk)
                bisect.insort(open_keys, f)
            else:
                fk, tk = hit
                tk["_last_w"] = w
                tk["n_win"] += 1
                tk["best_q"] = min(tk["best_q"], q)
                tk["_fs"].append(f)
                tk["_amps"].append(amp)
                tk["_macs"].append(mac)
                if abs(f - fk) > tol_f / 2:   # 漂移超半容差换键，抑制抖动
                    open_tr[fk].remove(tk)
                    if not open_tr[fk]:
                        del open_tr[fk]
                        open_keys.remove(fk)
                    open_tr.setdefault(f, []).append(tk)
                    bisect.insort(open_keys, f)
            used.add(tk["id"])
            slot_track[w, k] = tk["id"]
    for bucket in open_tr.values():
        for tk in bucket:
            _finalize(tk)
    # 创建期 id = 出生序（len(spells)+open 单调递增），天然唯一，
    # slot_track 与 spells 经它对应，无需重编号
    return spells, slot_track


def summarize(spells, min_len=MIN_LEN):
    """轨迹统计：确认数、长度分布。"""
    conf = [t for t in spells if t["n_win"] >= min_len]
    lens = np.array([t["n_win"] for t in conf]) if conf else np.array([0])
    return {"n_tracks": len(spells), "n_confirmed": len(conf),
            "len_p50": float(np.percentile(lens, 50)),
            "len_p90": float(np.percentile(lens, 90)),
            "len_max": int(lens.max())}


def make_events(spells, _start2idx=None, min_len=60, n_win=None):
    """确认轨迹 → 事件列表 [[win_idx, type, freq], ...]。

    type: 0=出生 1=消亡 2=迁移（消亡后 SWITCH_GAP 窗内邻近频率出生）。
    首窗已存在/末窗仍持续的不产生对应事件。
    """
    if n_win is None:
        n_win = max((t["death_w"] for t in spells), default=0) + 1
    conf = [t for t in spells if t["n_win"] >= min_len]
    events, births = [], defaultdict(list)
    for t in conf:
        if t["birth_w"] > 0:
            events.append([t["birth_w"], 0, t["f_med"]])
            births[t["birth_w"]].append(t)
    deaths = []
    for t in conf:
        if t["death_w"] < n_win - 1:
            events.append([t["death_w"], 1, t["f_med"]])
            deaths.append(t)
    for d in deaths:
        for w in range(d["death_w"] + 1, min(d["death_w"] + SWITCH_GAP + 1, n_win)):
            for b in births.get(w, []):
                if SWITCH_DF[0] <= abs(b["f_med"] - d["f_med"]) <= SWITCH_DF[1]:
                    events.append([w, 2, b["f_med"]])
    events.sort(key=lambda e: e[0])
    return events


def slot_track_info(peaks_arr, _win0_starts, slot_track, spells):
    """显示层 top5 槽 → (track_id, track_len) 两个 (n_win,5) float32 数组。"""
    n_win = peaks_arr.shape[0]
    tid = np.full((n_win, 5), -1.0, dtype=np.float32)
    tlen = np.zeros((n_win, 5), dtype=np.float32)
    lens = np.zeros(len(spells) + 1)
    for t in spells:
        lens[t["id"]] = t["n_win"]
    for i in range(n_win):
        for k in range(5):
            f = float(peaks_arr[i, k, 0])
            if not (f == f):
                continue
            tk_id = int(slot_track[i, k]) if k < slot_track.shape[1] else -1
            if 0 <= tk_id < len(lens):
                tid[i, k] = tk_id
                tlen[i, k] = lens[tk_id]
    return tid, tlen
