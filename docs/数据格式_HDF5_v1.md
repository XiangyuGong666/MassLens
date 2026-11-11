# MassLens 数据库格式 v1（2026-09-30 定稿，冻结）

本文档定义 MassLens / 质量透镜的离线模态数据库格式。**后续所有阶段（插值、模态跟踪、UI）都建立在此格式上；任何改动必须升版本号并改写本文档。**

## 文件组成

| 文件 | 内容 |
|---|---|
| `masslens_shed_v2.h5` | HDF5 数据库：几何 + 全部工况的频率与振型 |
| `manifest_v2.json` | 参数网格、预设工况、参考点、来源信息（人读） |

## HDF5 布局

```
/                       attrs:
                          format_version = "1.0"
                          structure      = 结构描述（乐清电厂3号煤棚…）
                          ansys_version  = "2024 R2"
                          created        = ISO 时间
                          units          = 单位字典
/geometry/
    node_ids   (N,)   int32     ANSYS 节点号，升序。**所有振型数组的行序与此一致**
    coords     (N,3)  float64   节点坐标，单位 m（平衡态几何）
    elem_shell (E1,4) int32     SHELL181 膜单元连接，值为 node_ids 的 0-based 下标。
                                三角单元末位重复第 3 节点补齐（可视化约定）
    elem_link  (E2,2) int32     LINK180 索单元连接，同上
/cases/case_XXXX/       attrs:
                          m_a        float64  均布附加质量面密度 kg/m²
                          dens_eff   float64  等效膜密度 kg/m³（= 1200 + m_a/0.0012）
                          grid_index int32    在参数网格中的序号
    freqs      (M,)   float64   固有频率 Hz，M=20。
                                **为 LP 重启动的原始模态序，未做换序跟踪**（第 2 晚处理）
    shapes     (M,N,3) float32  振型节点位移，ANSYS 质量归一化（φᵀMφ=1），gzip 压缩
```

## 口径约定（正确性关键）

1. **结构唯一**：浙能乐清电厂 3 号气承式膜结构煤棚（317×115×45 m，±45° 正交索网，内压 270 Pa），不使用任何经典算例。
2. **生成方法**：通过补丁修改重启动文件中的膜密度 `dens = 1200 + m_a/0.0012`（质量只加膜面，索不加），随后进行 LP 线性摄动模态重启动；刚度与预应力沿用基态，全库同口径。
3. **行序对齐**：`shapes` 的行序 = `geometry/node_ids` 升序。APDL 导出时的行序由每工况 `node_map.txt` 记录并在建库时置换对齐。
4. **未跟踪**：`freqs`/`shapes` 的阶次是各工况自己的原始排序，跨工况的模态对应关系（MAC 跟踪）由第 2 晚的跟踪模块建立，不在本格式内。
5. **扩展预留**：分区权重等多参数工况将新增 `/cases/case_XXXX` 属性（如 `block_weights`），不改变既有数据集结构。

## manifest_v2.json 字段

- `grid_ma`：m_a 采样网格（kg/m²）
- `cases[]`：每工况 `case / m_a / grid_index / f1_hz / status`
- `presets`：左栏预设工况（无附加质量/小雨/中雨/大雨）对应的 m_a——**当前为占位值，待确认**
- `reference_points`：已知参考点（m_a=0 → f1=0.6401 Hz；m_a=1.333 → f1=0.5501 Hz）
- `source`：生成方法、坐标系、ANSYS 版本

## 读写接口

只允许通过 `masslens_data/schema.py` 读写本格式：
`create_db / add_case / load_geometry / list_cases / load_case`。
