"""把 jobs/case_XXXX 的 APDL 文本输出装配成 HDF5 数据库 + manifest.json。

用法（项目根目录）：python -m masslens_data.build_db
幂等：重复运行整体重建 masslens_shed_v1.h5，不会重复堆叠工况。
"""
from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

import numpy as np

from . import config, mesh_io, schema


def log(msg):
    print(msg, flush=True)


def parse_modes(path, nn):
    """modes.txt → (n_mode, nn, 3)。块头 'MODE    i'，随后 nn 行 3E25.16。"""
    blocks, cur = [], []
    for ln in Path(path).read_text().splitlines():
        if ln.startswith("MODE"):
            if cur:
                blocks.append(cur)
            cur = []
        elif ln.strip():
            cur.append([float(v) for v in ln.split()])
    if cur:
        blocks.append(cur)
    arr = np.asarray(blocks, dtype=np.float64)
    assert arr.ndim == 3 and arr.shape[1] == nn and arr.shape[2] == 3, \
        f"{path}: 形状异常 {arr.shape}，期望 (M,{nn},3)"
    return arr


def parse_node_map(path):
    """node_map.txt → (nn,) int64，第 i 行对应的 ANSYS 节点号（E25.16 写出）。"""
    ids = []
    for ln in Path(path).read_text().splitlines():
        f = ln.split()
        if len(f) >= 2:
            ids.append(int(round(float(f[1]))))
    return np.asarray(ids, dtype=np.int64)


# ---- 新模型 CAD 系 → 旧 FE 显示系（2026-10-27 换型；前端/传感器表/实测通道全在显示系） ----
# old_x(跨向 ±57.275) = cad_y − (−1898.725)；old_y(高度 0~41.73) = cad_z + 5.0；
# old_z(长向 0~316.55) = cad_x − 636.0。位移向量分量同序置换（无符号翻转）。
CAD2DISP_PERM = (1, 2, 0)          # u_disp = u_cad[:, :, [1,2,0]]
CAD2DISP_OFFSET = (1898.725, 5.0, -636.0)   # 置换后逐轴加


def cad_to_display(coords):
    """(N,3) CAD 坐标 → 旧 FE 显示系坐标。"""
    c = np.asarray(coords, dtype=np.float64)
    out = c[:, CAD2DISP_PERM].copy()
    for ax in range(3):
        out[:, ax] += CAD2DISP_OFFSET[ax]
    return out


def shapes_to_display(shapes):
    """(M,N,3) 振型位移分量按显示系置换（向量无平移）。"""
    return np.asarray(shapes)[:, :, CAD2DISP_PERM]


def build():
    geom, n_tri = mesh_io.load_geometry(config.MESH_CDB)
    nn = len(geom["node_ids"])
    log(f"几何：{nn} 节点，膜单元 {len(geom['elem_shell'])}"
        f"（含三角 {n_tri}），索单元 {len(geom['elem_link'])}")

    coords = cad_to_display(geom["coords"])   # CAD 系 → 旧 FE 显示系
    span = coords.max(axis=0) - coords.min(axis=0)
    assert abs(span[0] - 114.55) < 1 and abs(span[1] - 41.73) < 0.5 \
        and abs(span[2] - 316.55) < 1, f"显示系包围盒异常 {span}"
    log(f"显示系包围盒：X {coords[:,0].min():.2f}~{coords[:,0].max():.2f}，"
        f"Y {coords[:,1].min():.2f}~{coords[:,1].max():.2f}，"
        f"Z {coords[:,2].min():.2f}~{coords[:,2].max():.2f}")

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    schema.create_db(config.H5_PATH, geom["node_ids"], coords,
                     geom["elem_shell"], geom["elem_link"])

    id_pos = {int(n): k for k, n in enumerate(geom["node_ids"])}
    manifest_cases = []
    for jobdir in sorted(config.JOBS_DIR.glob("case_*")):
        if not (jobdir / ".done").exists():
            continue
        m = re.match(r"case_(\d+)", jobdir.name)
        idx = int(m.group(1))
        m_a = config.GRID_MA[idx]
        freqs = np.loadtxt(jobdir / "freqs.txt", ndmin=1)
        map_ids = parse_node_map(jobdir / "node_map.txt")
        shapes = parse_modes(jobdir / "modes.txt", len(map_ids))

        if len(map_ids) != nn or set(map_ids.tolist()) != set(id_pos):
            raise RuntimeError(f"{jobdir.name}: node_map 与 mesh.cdb 节点集不一致")
        order = np.array([id_pos[int(n)] for n in map_ids])  # ANSYS 行序 → 库行序
        shapes = shapes_to_display(shapes[:, order, :])      # CAD 分量 → 显示系
        if len(freqs) != shapes.shape[0]:
            raise RuntimeError(f"{jobdir.name}: 频率 {len(freqs)} 阶 ≠ 振型 {shapes.shape[0]} 阶")

        dens_eff = config.MEMBRANE_DENS + m_a / config.SHELL_THICKNESS
        schema.add_case(config.H5_PATH, jobdir.name, m_a, dens_eff, idx,
                        freqs, shapes)
        manifest_cases.append({"case": jobdir.name, "m_a": m_a,
                               "grid_index": idx, "f1_hz": float(freqs[0]),
                               "status": "done"})
        log(f"{jobdir.name}: m_a={m_a}，f1={freqs[0]:.6f} Hz，{len(freqs)} 阶入库")

    manifest = {
        "format_version": schema.FORMAT_VERSION,
        "structure": schema.STRUCTURE_DESC,
        "units": schema.UNITS,
        "database": config.H5_PATH.name,
        "grid_ma": config.GRID_MA,
        "n_modes": config.N_MODES,
        "cases": manifest_cases,
        "presets": {**config.PRESETS,
                    "_note": "雨况对应的 m_a 为占位值，待确认后修订"},
        "reference_points": {
            "f1_ma0_hz": config.F1_REF_MA0,
            "ma_ref_kg_m2": config.MA_REF,
            "f1_ma_ref_hz": config.F1_REF_MA},
        "source": {
            "method": "planC rdb 密度二进制补丁 + LP 线性摄动模态重启动"
                      "（2026-10-15 验证：m_a*=1.333 → f1=0.550045）",
            "workdir": str(config.WORK_DIR),
            "mesh_cdb": str(config.MESH_CDB),
            "coordinate_frame": "旧 FE 显示系（X 跨向 ±57.275 / Y 高度 0~41.73 / "
                                "Z 长向 0~316.55）；源网格 CAD 系经仿射映射，"
                                "振型分量同序置换 (y,z,x)",
            "ansys": "2024 R2"},
        "created": datetime.datetime.now().isoformat(timespec="seconds")}
    config.MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"数据库：{config.H5_PATH}\nmanifest：{config.MANIFEST_PATH}")


if __name__ == "__main__":
    build()
