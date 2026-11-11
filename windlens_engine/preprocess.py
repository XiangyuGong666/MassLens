"""预处理：GNSS 去漂、加速度带通（口径见 config.BAND_ACC / HP_GNSS）。

GNSS 位移含米级慢漂与分段重置，只取动态分量：0.05 Hz 高通（零相位）。
加速度 0.1~3 Hz 带通覆盖前 20 阶模态频带（0.63~1.9 Hz）并压制低频噪声。
风压仅去均值（供展示与激励侧分析）。全程 sosfiltfilt 零相位，不引入时移。
"""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfiltfilt

from . import config


def _sos_band(lo, hi, fs, order=4):
    return butter(order, [lo, hi], btype="bandpass", fs=fs, output="sos")


def _sos_high(fc, fs, order=2):
    return butter(order, fc, btype="highpass", fs=fs, output="sos")


def preprocess_window(w, fs=config.FS):
    """w: extract.load_window 的 arrays dict。返回预处理后的新 dict（不改原数组）。"""
    out = dict(w)
    sos_hp = _sos_high(config.HP_GNSS, fs)
    out["gnss"] = sosfiltfilt(sos_hp, np.asarray(w["gnss"]), axis=1)
    sos_bp = _sos_band(*config.BAND_ACC, fs)
    out["acc"] = sosfiltfilt(sos_bp, np.asarray(w["acc"]), axis=1)
    p = np.asarray(w["pressure"])
    out["pressure"] = p - p.mean(axis=1, keepdims=True)
    return out
