"""MassLens / 质量透镜 —— 插值层（第 2 晚）。

MAC 矩阵逐参数步模态跟踪 + 1/f² 空间频率插值 + 跟踪对齐后的振型线性插值。
对外的唯一接口是 interpolator.Interpolator.query(params) -> (freqs, shapes)；
数据层（masslens_data）只通过 schema.py 定义的 HDF5 格式被读取。
"""

from .interpolator import Interpolator

__all__ = ["Interpolator"]
__version__ = "0.2.0"
