"""m_a 参数化扫描驱动：rdb 二进制密度补丁 + LP 模态重启动，逐工况批处理 MAPDL。

用法（项目根目录，解释器 python）：
    python -m masslens_data.sweep                 # 跑全部未完成工况
    python -m masslens_data.sweep --only 0,1.333  # 只跑指定 m_a
    python -m masslens_data.sweep --np 16

机制（planC 路径，2026-10-15 已在 05_独立复核与附加质量/planC_refined_直接解验证
验证：m_a*=1.333 → f1=0.550045）：
每工况先恢复纯净 db/rdb，Python 直接把 rdb 里的膜材密度 double(1200.0) 补丁为
1200 + m_a/0.0012，渲染 run_case.inp，批处理跑一次 MAPDL：
LP 模态 20 阶（/PREP7 换真实 E 1.2e9/1.6e11，与 600 阶基准库同口径）→
导出 freqs/modes/node_map。
注意：不可把 doctor 后的 db 覆盖 rdb（旧模型老路）——新模型 db 带 MODAL 分析
类型，会让 ANTYPE,,RESTART,,,PERTURB 报
"prestressed analysis may not be performed when the previous analysis used NLGEOM,ON"。
断点续跑：jobs/case_XXXX/.done 存在即跳过；台账 jobs/ledger.csv。
崩溃兜底：基态 db/rdb/r001/r002/ldhi 备份为 .bak_masslens，restore_base.py 一键恢复。
"""
from __future__ import annotations

import argparse
import csv
import datetime
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import config

CASE_TIMEOUT = 3600  # 单工况超时秒数（ELFORM + LANB 20 阶，经验值数分钟）


def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


def _base_file(ext):
    return config.WORK_DIR / f"{config.BASE_JOBNAME}.{ext}"


def backup_base():
    """首次运行前备份纯净 db/rdb（已存在则跳过）。"""
    for ext in ("db", "rdb"):
        src, bak = _base_file(ext), _base_file(ext + ".bak_masslens")
        if not bak.exists():
            log(f"备份基态 {src.name} → {bak.name}")
            shutil.copy2(src, bak)


def restore_base():
    """把纯净 db/rdb 恢复回工作目录（每工况前与全部结束后都调用）。"""
    for ext in ("db", "rdb"):
        bak, dst = _base_file(ext + ".bak_masslens"), _base_file(ext)
        if bak.exists():
            shutil.copy2(bak, dst)


def case_name(idx):
    return f"case_{idx:04d}"


def _read_membrane_mass(out_path):
    """从 case.out 的 LP 质量摘要读 mat=1（膜）总质量；读不到返回 None。"""
    import re
    txt = Path(out_path).read_bytes().decode("utf-8", errors="ignore")
    m = re.search(r"MASS SUMMARY BY ELEMENT TYPE[^\n]*\n\s*TYPE\s+MASS\s*\n"
                  r"\s*1\s+([0-9.E+]+)", txt)
    return float(m.group(1)) if m else None


def _patch_rdb_dens(new_dens, old_dens=config.MEMBRANE_DENS):
    """把 rdb 中膜材密度 double(old_dens) 二进制补丁为 new_dens（planC 路径）。

    返回替换处数；一处未找到视为异常（rdb 可能已被污染，应先 restore_base）。
    """
    import struct
    rdb = _base_file("rdb")
    data = rdb.read_bytes()
    needle = struct.pack("<d", float(old_dens))
    n = data.count(needle)
    if n == 0:
        raise RuntimeError(f"{rdb.name} 中未找到密度 double({old_dens})")
    rdb.write_bytes(data.replace(needle, struct.pack("<d", float(new_dens))))
    return n


# MAPDL 无法解析含中文的宏路径，宏以裸文件名拷进工作目录后引用
# （doctor 步骤内联在 run_case.inp：.mac 宏的局部参数作用域看不到 MASS_A）
MAC_COPIES = {"lp_modal20_export.mac": "ml_lp20_export.mac"}


def render_inp(m_a):
    tpl = (config.APDL_DIR / "run_case.inp").read_text(encoding="utf-8")
    return tpl.replace("@MASS_A@", repr(float(m_a)))


def run_case(idx, m_a, np_cnt):
    """跑单个工况，返回 (status, f1, note)。"""
    name = case_name(idx)
    jobdir = config.JOBS_DIR / name
    jobdir.mkdir(parents=True, exist_ok=True)
    done = jobdir / ".done"
    if done.exists():
        log(f"{name} (m_a={m_a}) 已完成，跳过")
        return "done", None, "skipped"

    restore_base()
    dens_eff = config.MEMBRANE_DENS + m_a / config.SHELL_THICKNESS
    if abs(dens_eff - config.MEMBRANE_DENS) > 1e-12:
        n_hit = _patch_rdb_dens(dens_eff)
        log(f"{name} rdb 密度补丁 1200 → {dens_eff:.2f}（{n_hit} 处）")
    for src_name, dst_name in MAC_COPIES.items():
        shutil.copy2(config.APDL_DIR / src_name, config.WORK_DIR / dst_name)
    for stale in ("freqs.txt", "modes.txt", "node_map.txt"):
        p = config.WORK_DIR / stale
        if p.exists():
            p.unlink()
    err_snap = _base_file("err")
    err_mtime = err_snap.stat().st_mtime if err_snap.exists() else None

    inp = jobdir / "run_case.inp"
    inp.write_text(render_inp(m_a), encoding="utf-8")
    out = jobdir / "case.out"
    cmd = [config.MAPDL_EXE, "-b", "-smp", "-np", str(np_cnt),
           "-j", "masslens", "-i", str(inp), "-o", str(out)]
    # 本机 Language\fx0.msb ACL 损坏会导致 MAPDL 加载消息库失败并中断批处理，
    # Preserve any MAPDL environment overrides configured by the caller.
    env = os.environ.copy()
    t0 = time.time()
    log(f"{name} (m_a={m_a}) 启动 MAPDL …")
    proc = subprocess.Popen(cmd, cwd=str(config.WORK_DIR), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        rc = proc.wait(timeout=CASE_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=15)  # 等其退净，避免下一轮覆盖 db/rdb 撞文件锁
        log(f"{name} 超时 {CASE_TIMEOUT}s，已终止")
        return "failed", None, "timeout"
    dt = time.time() - t0

    freqs_f, modes_f = config.WORK_DIR / "freqs.txt", config.WORK_DIR / "modes.txt"
    node_map_f = config.WORK_DIR / "node_map.txt"
    if rc != 0 or not freqs_f.exists() or not modes_f.exists() \
            or modes_f.stat().st_size == 0 or not node_map_f.exists():
        log(f"{name} 失败：rc={rc}，导出文件缺失（详见 {out}）")
        return "failed", None, f"rc={rc}"

    freqs = [float(s) for s in freqs_f.read_text().split()]
    if len(freqs) < config.N_MODES:
        log(f"{name} 失败：频率不足 {config.N_MODES} 阶（{len(freqs)}）")
        return "failed", None, "freqs short"

    # doctor 生效校验：LP 质量摘要中的膜面质量应随 m_a 线性增长
    mem = _read_membrane_mass(out)
    expect = config.MEMBRANE_MASS0 * (1 + m_a / (config.MEMBRANE_DENS
                                                 * config.SHELL_THICKNESS))
    if mem is None or abs(mem / expect - 1) > 0.01:
        log(f"{name} 失败：膜面质量 {mem} ≠ 期望 {expect:.0f}（doctor 未生效）")
        return "failed", None, f"membrane mass {mem} != {expect:.0f}"

    for f in ("freqs.txt", "modes.txt", "node_map.txt"):
        src = config.WORK_DIR / f
        if src.exists():
            shutil.move(str(src), str(jobdir / f))
    if err_snap.exists() and (err_mtime is None
                              or err_snap.stat().st_mtime != err_mtime):
        shutil.copy2(err_snap, jobdir / "err_snapshot.txt")
    done.touch()
    log(f"{name} 完成：f1={freqs[0]:.6f} Hz，用时 {dt:.0f}s")
    return "done", freqs[0], ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="逗号分隔的 m_a 列表，只跑这些")
    ap.add_argument("--np", type=int, default=config.NP)
    args = ap.parse_args()

    grid = config.GRID_MA
    if args.only:
        want = {float(s) for s in args.only.split(",")}
        grid = [m for m in grid if m in want]
        missing = want - set(grid)
        if missing:
            log(f"警告：{sorted(missing)} 不在网格 {config.GRID_MA} 中")

    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    ledger = config.JOBS_DIR / "ledger.csv"
    new = not ledger.exists()
    backup_base()
    try:
        with ledger.open("a", newline="", encoding="utf-8") as fh:
            wr = csv.writer(fh)
            if new:
                wr.writerow(["case", "m_a", "status", "f1_hz", "time", "note"])
            for m_a in grid:
                idx = config.GRID_MA.index(m_a)
                status, f1, note = run_case(idx, m_a, args.np)
                if status != "done" or f1 is not None:
                    wr.writerow([case_name(idx), m_a, status,
                                 f1 if f1 is not None else "",
                                 datetime.datetime.now().isoformat(timespec="seconds"),
                                 note])
                    fh.flush()
    finally:
        log("恢复纯净基态 db/rdb")
        restore_base()
    log("扫描结束，台账见 jobs/ledger.csv")


if __name__ == "__main__":
    main()
