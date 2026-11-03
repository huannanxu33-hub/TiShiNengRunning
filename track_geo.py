"""
track_geo.py — 几何基础模块

提供跑线几何运算：haversine 距离、折线总长、沿折线按距离插值推进、
按累计距离索引定位（供人脸公里数定位、配速着色等使用）。

坐标约定：
    - 跑线 runLinePath 为 [[lng, lat], ...]，即每点 [经度, 纬度]。
    - 本模块内部统一使用 (lng, lat) 元组，避免与「lat, lng」混淆。
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

EARTH_RADIUS_M = 6371000.0

Point = Tuple[float, float]  # (lng, lat)


def haversine(p1: Sequence[float], p2: Sequence[float]) -> float:
    """两点间 haversine 距离（米）。p = [lng, lat] 或 (lng, lat)。"""
    lon1, lat1 = float(p1[0]), float(p1[1])
    lon2, lat2 = float(p2[0]), float(p2[1])
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_M * c


def segment_lengths(points: Sequence[Sequence[float]]) -> List[float]:
    """计算折线每段的长度（米），长度为 len(points)-1。"""
    return [haversine(points[i], points[i + 1]) for i in range(len(points) - 1)]


def total_length(points: Sequence[Sequence[float]]) -> float:
    """折线总长（米）。"""
    return sum(segment_lengths(points))


def cumulative_lengths(points: Sequence[Sequence[float]]) -> List[float]:
    """累计距离数组，cum[i] 表示从起点到第 i 个点的累计距离（米）。"""
    cum = [0.0]
    for d in segment_lengths(points):
        cum.append(cum[-1] + d)
    return cum


def interpolate(p1: Sequence[float], p2: Sequence[float], ratio: float) -> Point:
    """在 p1、p2 之间按 ratio（0~1）线性插值，返回 (lng, lat)。"""
    ratio = max(0.0, min(ratio, 1.0))
    lng = p1[0] + (p2[0] - p1[0]) * ratio
    lat = p1[1] + (p2[1] - p1[1]) * ratio
    return (lng, lat)


def point_at_distance(points: Sequence[Sequence[float]], dist: float) -> Point:
    """
    返回沿折线前进 dist 米处的坐标点。

    若 dist 超出折线总长，则返回终点；若为负，返回起点。
    """
    if dist <= 0:
        return (float(points[0][0]), float(points[0][1]))
    cum = cumulative_lengths(points)
    total = cum[-1]
    if dist >= total:
        return (float(points[-1][0]), float(points[-1][1]))
    # 二分定位所在段
    lo, hi = 0, len(cum) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if cum[mid] <= dist:
            lo = mid
        else:
            hi = mid
    seg_len = cum[hi] - cum[lo]
    ratio = (dist - cum[lo]) / seg_len if seg_len > 0 else 0.0
    return interpolate(points[lo], points[hi], ratio)


def distance_between_along_path(points: Sequence[Sequence[float]],
                                d1: float, d2: float) -> float:
    """沿折线从距离 d1 到 d2 的路径长度（米），d2 >= d1。"""
    return max(0.0, d2 - d1)
