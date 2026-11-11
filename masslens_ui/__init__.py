"""MassLens / 质量透镜 —— UI 层（第 3 晚）。

本地服务器 + 三栏网页。UI 不碰 HDF5：频率与振型只经 masslens_interp.Interpolator
的 query() 获取；几何只经 masslens_data.schema 读取一次。
"""
