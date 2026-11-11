"""第 1 晚验收：Python 读出任意工况的频率与振型，并自动校验参考点。

用法（项目根目录）：python -m masslens_data.verify_db [--case case_0003]
校验项：
 1. m_a=0 工况 f1 ≈ 0.6332 Hz（±0.5%）
 2. m_a=1.1695 工况 f1 ≈ 0.550 Hz（±0.5%）
 3. f1 随 m_a 单调不增
 4. m_a=0 与 m_a=1.1695 的第 1 阶振型 MAC > 0.99（均布质量几乎不改振型）
退出码：全部通过 0，否则 1。
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

from . import config, schema


def mac(phi_a, phi_b):
    """普通内积 MAC（振型已质量归一；严格质量加权 MAC 留给第 2 晚跟踪模块）。"""
    a = phi_a.reshape(-1)
    b = phi_b.reshape(-1)
    return float(abs(a @ b) ** 2 / ((a @ a) * (b @ b)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default=None, help="指定工况名，默认逐个列出")
    args = ap.parse_args()

    geom = schema.load_geometry(config.H5_PATH)
    cases = schema.list_cases(config.H5_PATH)
    print(f"数据库 {config.H5_PATH.name}：{len(geom['node_ids'])} 节点，"
          f"膜 {len(geom['elem_shell'])} 单元，索 {len(geom['elem_link'])} 单元，"
          f"{len(cases)} 工况")
    print(f"{'case':<10}{'m_a(kg/m^2)':>12}{'f1(Hz)':>10}{'f2(Hz)':>10}{'阶数':>6}")
    for name, m_a in cases:
        _, freqs, shapes = schema.load_case(config.H5_PATH, name)
        print(f"{name:<10}{m_a:>12.4f}{freqs[0]:>10.5f}{freqs[1]:>10.5f}"
              f"{len(freqs):>6}")

    if args.case:
        m_a, freqs, shapes = schema.load_case(config.H5_PATH, args.case)
        print(f"\n{args.case}（m_a={m_a} kg/m^2）前 5 阶频率："
              + ", ".join(f"{f:.4f}" for f in freqs[:5]))
        print(f"第 1 阶振型：形状 {shapes[0].shape}，"
              f"|φ|max={np.linalg.norm(shapes[0], axis=1).max():.4e}")

    checks = []
    by_ma = {round(m_a, 6): name for name, m_a in cases}

    _, freqs0, shapes0 = schema.load_case(config.H5_PATH, by_ma[0.0])
    ok = abs(freqs0[0] - config.F1_REF_MA0) / config.F1_REF_MA0 <= config.F1_TOL
    checks.append((ok, f"m_a=0 时 f1={freqs0[0]:.5f} Hz（参考 {config.F1_REF_MA0}）"))

    _, freqs_r, shapes_r = schema.load_case(config.H5_PATH, by_ma[config.MA_REF])
    ok = abs(freqs_r[0] - config.F1_REF_MA) / config.F1_REF_MA <= config.F1_TOL
    checks.append((ok, f"m_a={config.MA_REF} 时 f1={freqs_r[0]:.5f} Hz"
                       f"（参考 {config.F1_REF_MA}）"))

    f1s = [schema.load_case(config.H5_PATH, n)[1][0] for n, _ in cases]
    ok = all(b <= a + 1e-9 for a, b in zip(f1s, f1s[1:]))
    checks.append((ok, "f1 随 m_a 单调不增：" + ", ".join(f"{f:.4f}" for f in f1s)))

    v = mac(shapes0[0], shapes_r[0])
    checks.append((v > 0.99, f"第 1 阶振型 MAC(m_a=0 vs {config.MA_REF})={v:.5f}"))

    print("\n验收校验：")
    all_ok = True
    for ok, msg in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
        all_ok &= ok
    print("结论：" + ("全部通过" if all_ok else "存在未通过项"))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
