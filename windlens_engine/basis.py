"""传感器处模态基：从 MassLens 数据库提取测点位置的前 M 阶振型。

口径：
- 测点 → FE 节点：sensor_map_v2.csv 自带 node_row（新模型精确节点）；
  FE 坐标最近邻距离仅作交叉校验。
- 通道顺序：加速度 loc k ↔ 通道 3k-2/3k-1/3k（x/y/z，FE 显示系分量）；
  GNSS 点 k ↔ 通道 3k-2/3k-1/3k（plotGNSS.m 确认）。
- 频率/振型用 m_a=0 工况（通道序 = 频率升序，即第 2 晚跟踪锚点口径）。
- 与 sensor_response_600.csv 交叉核对（600 阶 × 12 测点，crosscheck_sensor600）。
"""
from __future__ import annotations

import h5py
import numpy as np

from . import config


def sensor_node_indices(sensor_map=None, h5_path=None):
    """12 个测点位置 → h5 节点行号（0-based），返回 (idx (12,), max_dist)。

    v2 传感器表自带 node_row（新模型精确节点），直接采用；同时用 FE 坐标
    最近邻距离做交叉校验（防止表与库错位）。
    """
    sensor_map = sensor_map or config.load_sensor_map()
    h5_path = h5_path or config.H5_PATH
    with h5py.File(h5_path, "r") as h:
        coords = h["geometry/coords"][:]
    idx, dmax = [], 0.0
    for s in sensor_map:
        d = np.linalg.norm(coords - np.array(s["fe"]), axis=1)
        idx.append(int(s["node_row"]) if s.get("node_row") is not None
                   else int(d.argmin()))
        dmax = max(dmax, float(d.min()))
    return np.array(idx), dmax


def build_basis(n_modes=20, sensor_map=None, h5_path=None):
    """返回 dict(freqs, phi_acc (M,36), phi_gnss (M,21), gnss_locs, node_idx)。

    phi_acc[m, 3k+d] = 第 m 阶模态在测点 k+1 的 d 分量（d=0,1,2 ↔ x,y,z）。
    phi_gnss 只含 gnss_point>0 的位置（当前为 1,2,7,8,9,11,12 号位置）。
    """
    sensor_map = sensor_map or config.load_sensor_map()
    h5_path = h5_path or config.H5_PATH
    idx, dmax = sensor_node_indices(sensor_map, h5_path)
    assert dmax < 0.01, f"测点映射漂移 {dmax:.3f} m，超出容差"

    with h5py.File(h5_path, "r") as h:
        # m_a=0 工况：attrs m_a == 0 的那个 case
        for name in h["cases"]:
            if float(h["cases"][name].attrs["m_a"]) == 0.0:
                freqs = h["cases"][name]["freqs"][:n_modes]
                shapes = h["cases"][name]["shapes"][:n_modes]  # (M,N,3)
                break
        else:
            raise RuntimeError("数据库中没有 m_a=0 工况")

    phi_points = shapes[:, idx, :]                     # (M,12,3)
    m = len(freqs)
    phi_acc = phi_points.reshape(m, 36)
    gnss_rows = [i for i, s in enumerate(sensor_map) if s["gnss_point"] > 0]
    gnss_rows.sort(key=lambda i: sensor_map[i]["gnss_point"])  # 按 GNSS 点号排
    phi_gnss = phi_points[:, gnss_rows, :].reshape(m, 21)
    return {"freqs": freqs, "phi_acc": phi_acc, "phi_gnss": phi_gnss,
            "phi_points": phi_points, "gnss_locs": [i + 1 for i in gnss_rows],
            "node_idx": idx}


def parse_v450(path=None):
    """modal_sensor_response_v450.csv → (freqs (K,), resp (K,12,4))。

    每 12 行一阶：行格式 'mode*,freq,*,*,ux,uy,uz,|u|'（前四列有 Fortran 溢出星号）。
    """
    path = path or config.V450_CSV
    rows = []
    for ln in open(path, encoding="utf-8", errors="replace"):
        ln = ln.strip()
        if not ln or ln.startswith("mode"):
            continue
        f = [x.strip() for x in ln.split(",")]
        if len(f) < 8:
            continue
        try:
            rows.append((float(f[1]),) + tuple(float(x) for x in f[4:8]))
        except ValueError:
            continue
    assert len(rows) % 12 == 0, f"v450 行数 {len(rows)} 不是 12 的倍数"
    k = len(rows) // 12
    freqs = np.array([rows[i * 12][0] for i in range(k)])
    resp = np.array([[r[1:] for r in rows[i * 12:(i + 1) * 12]] for i in range(k)])
    return freqs, resp


def crosscheck_v450(basis, tol_mac=0.99):
    """h5 基与 v450 表逐阶对照（频率就近匹配 + MAC）。返回 (min_mac, report_lines)。"""
    f450, resp = parse_v450()
    lines, macs = [], []
    for m in range(len(basis["freqs"])):
        f = basis["freqs"][m]
        k = int(np.argmin(np.abs(f450 - f)))
        a = basis["phi_points"][m].reshape(-1).astype(np.float64)
        b = resp[k, :, :3].reshape(-1)
        mac = float(abs(a @ b) ** 2 / ((a @ a) * (b @ b)))
        macs.append(mac)
        lines.append(f"  第{m + 1}阶: h5 f={f:.5f} ↔ v450[{k}] f={f450[k]:.5f}"
                     f"  MAC={mac:.5f} {'OK' if mac >= tol_mac else 'FAIL'}")
    return min(macs), lines


def crosscheck_sensor600(basis, tol_mac=0.99):
    """h5 v2 基与新模型 sensor_response_600.csv 逐阶对照（频率就近 + MAC）。

    表内分量为 CAD 系 (ux,uy,uz)，先置换为显示系 (uy,uz,ux) 再比。
    返回 (min_mac, report_lines)。
    """
    f600, resp = parse_v450(config.SENSOR600_CSV)
    resp = resp[:, :, [1, 2, 0, 3]]          # CAD → 显示系，usum 不动
    lines, macs = [], []
    for m in range(len(basis["freqs"])):
        f = basis["freqs"][m]
        k = int(np.argmin(np.abs(f600 - f)))
        a = basis["phi_points"][m].reshape(-1).astype(np.float64)
        b = resp[k, :, :3].reshape(-1)
        mac = float(abs(a @ b) ** 2 / ((a @ a) * (b @ b)))
        macs.append(mac)
        lines.append(f"  第{m + 1}阶: h5 f={f:.5f} ↔ s600[{k}] f={f600[k]:.5f}"
                     f"  MAC={mac:.5f} {'OK' if mac >= tol_mac else 'FAIL'}")
    return min(macs), lines
