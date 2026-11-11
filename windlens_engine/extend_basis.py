"""60 阶扩展模态基导出与装配（WindLens 高频实测峰需要真高阶振型）。

背景：20 阶模态基只到 1.63 Hz，而实测识别峰常在 1.6~3.0 Hz，
就近匹配全部塌陷到 FE20——视觉上成了"单一模态"。本模块一次性补 60 阶
（m_a=0，LP 重启动，同 sweep 口径），装成 data_wind/basis_ext60.npz。

用法（项目根目录）：python -m windlens_engine.extend_basis
步骤：恢复纯净基态 → 渲染 m_a=0 驱动 → MAPDL 批处理（LANB 60 + MXPAND）
     → 解析 freqs/modes/node_map → 对齐 h5 行序 → npz。
注意：ASCII 宏；CADOE_LIBDIR242 绕过 fx0.msb ACL 损坏（与 sweep 同款）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from masslens_data import build_db, config as dcfg, sweep as sweeps
from . import config

OUT_DIR = config.OUT_DIR / "ext60"
MAC60_SRC = dcfg.APDL_DIR / "lp_modal60_export.mac"
MAC60_DST = dcfg.WORK_DIR / "ml_lp60_export.mac"

DRIVER = """! ext60 driver: m_a=0, planC path (pristine rdb), LP modal 60 + export
FINISH
/FILNAME,run_pressure_only,1
/INPUT,'ml_lp60_export','mac'
/EXIT,NOSAVE
"""


def run_mapdl(timeout=1800):
    """跑 m_a=0 的 60 阶导出。返回导出文件目录。"""
    if not MAC60_SRC.exists():
        raise RuntimeError(f"缺宏 {MAC60_SRC}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sweeps.backup_base()
    sweeps.restore_base()
    shutil.copy2(MAC60_SRC, MAC60_DST)
    for stale in ("freqs.txt", "modes.txt", "node_map.txt"):
        p = dcfg.WORK_DIR / stale
        if p.exists():
            p.unlink()
    inp = OUT_DIR / "run_ext60.inp"
    inp.write_text(DRIVER, encoding="ascii")
    out = OUT_DIR / "case.out"
    cmd = [dcfg.MAPDL_EXE, "-b", "-smp", "-np", str(dcfg.NP),
           "-j", "masslens_ext60", "-i", str(inp), "-o", str(out)]
    env = os.environ.copy()
    t0 = time.time()
    print("启动 MAPDL（60 阶 LP 导出）…", flush=True)
    proc = subprocess.Popen(cmd, cwd=str(dcfg.WORK_DIR), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=15)
        raise RuntimeError("超时")
    print(f"MAPDL 用时 {time.time() - t0:.0f}s，rc={rc}", flush=True)
    for f in ("freqs.txt", "modes.txt", "node_map.txt"):
        src = dcfg.WORK_DIR / f
        if not src.exists():
            sweeps.restore_base()
            raise RuntimeError(f"导出缺 {f}（详见 {out}）")
        shutil.move(str(src), str(OUT_DIR / f))
    sweeps.restore_base()
    return OUT_DIR


def assemble(out_dir=OUT_DIR):
    """freqs/modes/node_map → 对齐 h5 行序 → basis_ext60.npz。返回 (freqs, shapes)。"""
    import h5py
    from masslens_data import mesh_io
    geom, _ = mesh_io.load_geometry(dcfg.MESH_CDB)
    nn = len(geom["node_ids"])
    id_pos = {int(n): k for k, n in enumerate(geom["node_ids"])}
    freqs = np.loadtxt(out_dir / "freqs.txt", ndmin=1)
    map_ids = build_db.parse_node_map(out_dir / "node_map.txt")
    shapes = build_db.parse_modes(out_dir / "modes.txt", len(map_ids))
    if len(freqs) != shapes.shape[0]:
        raise RuntimeError(f"频率 {len(freqs)} 阶 ≠ 振型 {shapes.shape[0]} 阶")
    order = np.array([id_pos[int(n)] for n in map_ids])
    shapes = build_db.shapes_to_display(shapes[:, order, :])  # CAD 分量 → 显示系
    with h5py.File(config.H5_PATH, "r") as h:
        coords = h["geometry/coords"][:]
        assert len(coords) == nn
    out = config.OUT_DIR / "basis_ext60.npz"
    np.savez_compressed(out, freqs=freqs.astype(np.float64),
                        shapes=shapes.astype(np.float32))
    print(f"装配 {len(freqs)} 阶 × {nn} 节点 → {out}", flush=True)
    print("频率范围: %.4f ~ %.4f Hz" % (freqs[0], freqs[-1]), flush=True)
    return freqs, shapes


def main():
    print("== 60 阶扩展模态基 ==", flush=True)
    d = run_mapdl()
    freqs, _ = assemble(d)
    # 与前 20 阶（h5 m_a=0）核对
    import h5py
    with h5py.File(config.H5_PATH, "r") as h:
        for name in h["cases"]:
            if float(h["cases"][name].attrs["m_a"]) == 0.0:
                f20 = h["cases"][name]["freqs"][:20]
                break
    dev = np.abs(freqs[:20] - f20) / f20
    print(f"与库内前 20 阶相对偏差: max {dev.max() * 100:.3f}%", flush=True)
    assert dev.max() < 0.005, "前 20 阶与库不一致超 0.5%"
    print("核对 PASS", flush=True)


if __name__ == "__main__":
    main()
