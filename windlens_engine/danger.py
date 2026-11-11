"""最不利模态组合离线穷举（第 21 晚，用户"危险组合预警"）。

前提（用户口径）：能量相当（等权）、同相叠加是最不利包络。
对前 K 阶模态（K=3/4/5）穷举所有组合，计算全场最大位移，
取每种 K 下位移最大的 top 组合（含位置）。结果供监测页对照预警：
当前组合若接近某最不利组合（模态集重合 + 位移比高），给出规避提示。

**稳健强度口径（2026-11-02 用户反馈"不要被单点绑架"）**：
maxU = 前 10 大 |u| 节点的均值（而非绝对最大值）；热点 = 前 5 大节点的分布
（允许多点/相邻），spread = ≥50%绝对最大值的节点占比。

输出：`data_wind/danger_combos.json`
  {"basis": "basis_ext60", "premise": "equal-weight same-phase",
   "K3": [ {modes, freqs, maxU, node, coords}, ... top20 ],
   "K4": [...], "K5": [...]}

用法（项目根目录）：python -m windlens_engine.danger
"""
from __future__ import annotations

import heapq
import json
import time
from pathlib import Path

import numpy as np

from . import config

BASIS = config.OUT_DIR / "basis_ext60.npz"
OUT = config.OUT_DIR / "danger_combos.json"
TOPN = 20


def _norm_max(fields):
    """(P,N,3) → 每组合的稳健强度（前 10 大 |u| 节点均值，防单点绑架）与绝对最大节点。"""
    mag = np.linalg.norm(fields, axis=2)          # (P,N)
    idx = mag.argmax(axis=1)
    robust = np.sort(mag, axis=1)[:, -10:].mean(axis=1)
    return robust, idx


def extract_hotspots(shapes, combo, coords, maxu, max_spots=5, frac=0.50):
    """该组合位移场的高响应点分布：按 |u| 取前 max_spots 个节点（允许相邻，
    直接展示分布而非单点）；扩散度 = ≥50%绝对最大值的节点占比。

    背景（用户反馈）：场常被单个集中点主导——需要展示分布而不是永远那一个点。
    """
    field = np.zeros(shapes.shape[1:], dtype=np.float64)
    for c in combo:
        field += shapes[c]
    mag = np.linalg.norm(field, axis=1)
    order = np.argsort(-mag)[:max_spots]
    thr = frac * mag.max()
    spread = float((mag >= thr).mean())
    return list(order), spread


def enumerate_k(k, basis_limit=60, topn=TOPN, batch_first=True):
    """穷举前 basis_limit 阶中任 K 阶组合（等权同相），返回 topn 列表。"""
    z = np.load(BASIS)
    freqs, shapes = z["freqs"][:basis_limit], z["shapes"][:basis_limit]
    M = len(freqs)
    heap = []                       # (maxU, combo_tuple, node)
    t0 = time.time()
    n_eval = 0

    def offer(combo, maxu, node):
        nonlocal n_eval
        n_eval += 1
        item = (float(maxu), tuple(int(x) for x in combo), int(node))
        if len(heap) < topn:
            heapq.heappush(heap, item)
        elif maxu > heap[0][0]:
            heapq.heapreplace(heap, item)

    if k == 3:
        for i in range(M - 2):
            rest = np.arange(i + 1, M)
            if len(rest) < 2:
                continue
            for j in rest[:-1]:
                others = rest[rest > j]
                if not len(others):
                    continue
                fields = shapes[i] + shapes[j] + shapes[others]
                maxu, idx = _norm_max(fields)
                for o, mu in enumerate(maxu):
                    offer((i, j, int(others[o])), mu, idx[o])
            if i % 10 == 0:
                print(f"  K3 i={i}/{M}，已评 {n_eval} 组合，用时 {time.time()-t0:.0f}s",
                      flush=True)
    elif k == 4:
        for i in range(M - 3):
            for j in range(i + 1, M - 2):
                rest = np.arange(j + 1, M)
                if len(rest) < 2:
                    continue
                for l in rest[:-1]:
                    others = rest[rest > l]
                    if not len(others):
                        continue
                    fields = shapes[i] + shapes[j] + shapes[l] + shapes[others]
                    maxu, idx = _norm_max(fields)
                    for o, mu in enumerate(maxu):
                        offer((i, j, l, int(others[o])), mu, idx[o])
            if i % 10 == 0:
                print(f"  K4 i={i}/{M}，已评 {n_eval} 组合，用时 {time.time()-t0:.0f}s",
                      flush=True)
    elif k == 5:
        for i in range(M - 4):
            for j in range(i + 1, M - 3):
                for l in range(j + 1, M - 2):
                    rest = np.arange(l + 1, M)
                    if len(rest) < 2:
                        continue
                    for m in rest[:-1]:
                        others = rest[rest > m]
                        if not len(others):
                            continue
                        fields = shapes[i] + shapes[j] + shapes[l] + shapes[m] + shapes[others]
                        maxu, idx = _norm_max(fields)
                        for o, mu in enumerate(maxu):
                            offer((i, j, l, m, int(others[o])), mu, idx[o])
            if i % 5 == 0:
                print(f"  K5 i={i}/{M}，已评 {n_eval} 组合，用时 {time.time()-t0:.0f}s",
                      flush=True)
    else:
        raise ValueError(k)
    out = sorted(heap, key=lambda x: -x[0])
    return out, n_eval


def main():
    z = np.load(BASIS)
    freqs_all, shapes_all = z["freqs"], z["shapes"]
    coords = None
    # 节点坐标用于展示最不利位置
    from masslens_data import schema, config as dcfg
    with __import__("h5py").File(dcfg.H5_PATH, "r") as h:
        coords = h["geometry/coords"][:]

    result = {"basis": "basis_ext60", "premise": "equal-weight same-phase",
              "node_count": int(coords.shape[0])}
    for k in (3, 4, 5):
        top, n = enumerate_k(k, basis_limit=60 if k < 5 else 30)
        rows = []
        for maxu, combo, node in top:
            spots, spread = extract_hotspots(shapes_all, combo, coords, maxu)
            rows.append({
                "modes": [int(c + 1) for c in combo],
                "freqs": [round(float(freqs_all[c]), 4) for c in combo],
                "maxU": round(maxu, 6),
                "node": node + 1,
                "coords": [round(float(v), 3) for v in coords[node]],
                "hotspots": [[round(float(v), 3) for v in coords[s]] for s in spots],
                "spread": round(spread, 4),   # ≥80%maxU 的节点占比（扩散度）
            })
        result[f"K{k}"] = rows
        print(f"K{k}: 评估 {n} 组合 → top1 位移 {rows[0]['maxU']}（模态 {rows[0]['modes']}）",
              flush=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"写出 {OUT}", flush=True)


if __name__ == "__main__":
    main()
