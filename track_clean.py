"""
track_clean.py — 跑线清洗模块

对从数据库读取的原始跑线做几何清洗：
    - 去重复相邻点（距离过近的点合并）
    - 去异常跳点（相邻段长超过阈值的异常点）
    - 保证跑线点数足够、总长足够

清洗目标：让跑线成为一条平滑、可沿折线推进的参考轨迹。
"""
from __future__ import annotations

from typing import List, Sequence

from track_geo import haversine

# 相邻两点距离小于该值（米）视为重复点，剔除
MIN_SEGMENT_LEN = 1.0
# 相邻段长超过该值（米）视为异常跳点，剔除后一点
MAX_SEGMENT_LEN = 500.0


def dedupe_points(points: Sequence[Sequence[float]],
                  min_seg: float = MIN_SEGMENT_LEN) -> List[List[float]]:
    """剔除距离过近的重复点，返回新列表。"""
    if len(points) < 2:
        return [list(p) for p in points]
    out = [list(points[0])]
    for p in points[1:]:
        if haversine(out[-1], p) >= min_seg:
            out.append(list(p))
    return out


def remove_jumps(points: Sequence[Sequence[float]],
                 max_seg: float = MAX_SEGMENT_LEN) -> List[List[float]]:
    """剔除相邻段长过大的异常跳点。"""
    if len(points) < 2:
        return [list(p) for p in points]
    out = [list(points[0])]
    for i in range(1, len(points) - 1):
        d_prev = haversine(out[-1], points[i])
        d_next = haversine(points[i], points[i + 1])
        # 若当前点到前一点或后一点距离异常，则跳过该点
        if d_prev > max_seg and d_next > max_seg:
            continue
        out.append(list(points[i]))
    out.append(list(points[-1]))
    return out


def clean_path(points: Sequence[Sequence[float]],
               min_points: int = 4) -> List[List[float]]:
    """
    完整清洗流程：去重复 -> 去跳点 -> 校验点数。
    若清洗后点数不足 min_points，返回原始点（尽力而为）。
    """
    if not points:
        return []
    cleaned = dedupe_points(points)
    cleaned = remove_jumps(cleaned)
    if len(cleaned) < min_points:
        return [list(p) for p in points]
    return cleaned
