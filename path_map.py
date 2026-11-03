"""
path_map.py — 轨迹可视化模块

把跑线与生成轨迹渲染成静态图（PNG），用于本地验证轨迹形态。

功能：
    - 跑线叠加（灰色底图）
    - 轨迹按分钟配速着色（红=快，蓝=慢）
    - 关键点标注（起点、终点、中途人脸点）

依赖 matplotlib（若未安装则优雅降级，仅打印坐标摘要）。
"""
from __future__ import annotations

from typing import List, Optional, Sequence


def _lnglat_to_xy(lng: float, lat: float, origin_lng: float, origin_lat: float):
    """本地平面近似投影（等距圆柱），返回 (x, y) 米。"""
    import math
    x = (lng - origin_lng) * 111320.0 * math.cos(math.radians(origin_lat))
    y = (lat - origin_lat) * 111320.0
    return x, y


def plot_track(run_line_path: Sequence[Sequence[float]],
               path: Sequence[dict],
               face_points: Optional[Sequence[dict]] = None,
               out_file: str = 'track_map.png',
               is_public: bool = True) -> str:
    """
    渲染轨迹图。

    参数：
        run_line_path: 跑线 [[lng, lat], ...]
        path: 生成轨迹点（公版 {a,o} / 私版 {latitude,longitude}）
        face_points: 中途人脸点列表
        out_file: 输出 PNG 路径
        is_public: 是否公版（决定点字段名）

    返回输出文件路径；若 matplotlib 不可用则返回 None。
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print('未安装 matplotlib，跳过可视化。可 pip install matplotlib 后重试。')
        return None

    if not run_line_path:
        return None

    origin_lng, origin_lat = run_line_path[0][0], run_line_path[0][1]

    # 跑线
    line_xy = [_lnglat_to_xy(p[0], p[1], origin_lng, origin_lat) for p in run_line_path]
    lxs = [p[0] for p in line_xy]
    lys = [p[1] for p in line_xy]

    # 轨迹点
    if is_public:
        pts = [(_lnglat_to_xy(p['o'], p['a'], origin_lng, origin_lat), p.get('s', 0)) for p in path]
    else:
        pts = [(_lnglat_to_xy(p['longitude'], p['latitude'], origin_lng, origin_lat), p.get('speed', 0)) for p in path]

    txs = [p[0][0] for p in pts]
    tys = [p[0][1] for p in pts]
    speeds = [p[1] for p in pts]

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.plot(lxs, lys, color='gray', linewidth=1.5, alpha=0.6, label='跑线')
    sc = ax.scatter(txs, tys, c=speeds, cmap='coolwarm', s=8, label='轨迹(按速度着色)')
    plt.colorbar(sc, ax=ax, label='速度 (m/s)')

    # 关键点
    if pts:
        ax.scatter(txs[0], tys[0], color='green', s=120, marker='o', label='起点', zorder=5)
        ax.scatter(txs[-1], tys[-1], color='red', s=120, marker='x', label='终点', zorder=5)
    if face_points:
        for fp in face_points:
            if is_public:
                fx, fy = _lnglat_to_xy(fp['lng'], fp['lat'], origin_lng, origin_lat)
            else:
                fx, fy = _lnglat_to_xy(fp['lng'], fp['lat'], origin_lng, origin_lat)
            ax.scatter(fx, fy, color='orange', s=100, marker='*', label='人脸点', zorder=6)

    ax.set_xlabel('东向 (m)')
    ax.set_ylabel('北向 (m)')
    ax.set_title('跑步轨迹与跑线叠加')
    ax.legend()
    ax.set_aspect('equal')
    fig.tight_layout()
    fig.savefig(out_file, dpi=150)
    plt.close(fig)
    print(f'轨迹图已保存: {out_file}')
    return out_file
