"""
track_resample.py — 采样、配速规划、体能衰减与 GPS 噪声

核心原则（来自 PRD）：
    「里程、速度、步数用几何真值保持一致；只有对外经纬度带真实量级的 GPS 噪声。」

数据流：
    runLinePath(清洗后) -> 速度规划(配速 6~7min/km + 首点加速 + 疲劳衰减)
    -> 1Hz 沿折线推进(几何真值) -> 对经纬度叠加 AR(1) GPS 噪声
    -> 输出每采样点的 {t, 几何坐标, 噪声坐标, 真实速度, 距离}

设计要点：
    - T1 配速 6~7 分钟/公里（目标速度 2.38~2.78 m/s）
    - T2 上报速度 <= limitSpeed（超速点 = 0）
    - T3 采样间隔 1.0~1.3s（模拟真实手机 GPS 抖动，旧版逻辑）
    - T4 采样序号严格递增
    - T7 GPS 偏移均值 <= 1.2m，最大 <= 3.2m
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from track_geo import haversine, point_at_distance, total_length

# 配速区间（分钟/公里），PRD T1 要求 6~7
PACE_MIN_PER_KM = 6.0
PACE_MAX_PER_KM = 7.0
# 首点加速：前 N 秒从起步速度线性加速到目标配速
RAMP_UP_SECONDS = 8
# 起步速度占目标速度的比例
RAMP_START_RATIO = 0.45
# 采样间隔（毫秒）：模拟真实手机 GPS 的不稳定性（旧版逻辑 1000~1300ms）
SAMPLE_INTERVAL_MIN_MS = 1000
SAMPLE_INTERVAL_MAX_MS = 1300


@dataclass
class SamplePoint:
    """单个采样点。"""
    index: int                    # 采样序号（严格递增，从 0 开始）
    t: int                        # 绝对时间戳（毫秒）
    # 几何真值坐标（沿跑线推进，无噪声）
    geo_lng: float
    geo_lat: float
    # 叠加噪声后的对外坐标
    out_lng: float
    out_lat: float
    # 几何真值速度（m/s），由沿折线推进的距离/时间得出
    speed: float
    # 本采样间隔内沿折线前进的距离（米）
    distance: float
    # 累计里程（米）
    cum_distance: float


@dataclass
class SpeedProfile:
    """速度规划结果：每秒的目标速度（几何真值，m/s）。"""
    target_speeds: List[float] = field(default_factory=list)


def pace_to_speed(pace_min_per_km: float) -> float:
    """配速（分钟/公里）转速度（m/s）。"""
    return 1000.0 / (pace_min_per_km * 60.0)


def build_speed_profile(total_time_s: int,
                        base_pace_min: float,
                        limit_speed: Optional[float] = None,
                        seed: Optional[int] = None) -> SpeedProfile:
    """
    构造每秒目标速度曲线。

    包含：
        1. 首点加速 ramp-up（前 RAMP_UP_SECONDS 秒从低速线性加速）
        2. 巡航段：围绕 base_pace 的轻微随机波动（疲劳趋势缓慢下降）
        3. 随机"撞墙"凹坑：1~2 次短暂降速再恢复

    所有速度以 limit_speed 为上界裁剪（若提供）。
    """
    rng = random.Random(seed)
    base_speed = pace_to_speed(base_pace_min)
    # 目标速度上限：若有限速则取 min(限速, 巡航速度) 并留裕量
    cruise_speed = base_speed
    if limit_speed is not None:
        cruise_speed = min(cruise_speed, limit_speed * 0.96)

    speeds: List[float] = []

    # 首点加速段
    ramp_steps = min(RAMP_UP_SECONDS, total_time_s)
    for i in range(ramp_steps):
        ratio = RAMP_START_RATIO + (1.0 - RAMP_START_RATIO) * (i / ramp_steps)
        speeds.append(cruise_speed * ratio)

    # 巡航 + 疲劳缓慢衰减段
    remaining = total_time_s - ramp_steps
    if remaining > 0:
        # 疲劳系数：整体从 1.0 缓慢降到 ~0.94
        for i in range(remaining):
            progress = i / max(remaining, 1)
            fatigue = 1.0 - 0.06 * progress
            # 轻微随机波动 ±2%
            jitter = rng.uniform(-0.02, 0.02)
            s = cruise_speed * fatigue * (1.0 + jitter)
            # 限速裁剪
            if limit_speed is not None:
                s = min(s, limit_speed)
            speeds.append(s)

    # 随机"撞墙"凹坑：在总时长 40%~70% 之间随机选 1~2 处，降速 20%~30%，持续 20~40 秒
    if total_time_s > 90:
        num_walls = rng.randint(1, 2)
        for _ in range(num_walls):
            wall_start = rng.randint(int(total_time_s * 0.4), int(total_time_s * 0.7))
            wall_len = rng.randint(20, 40)
            wall_depth = rng.uniform(0.20, 0.30)
            for j in range(wall_start, min(wall_start + wall_len, total_time_s)):
                speeds[j] *= (1.0 - wall_depth)

    return SpeedProfile(target_speeds=speeds)


def estimate_time_for_distance(distance_m: float, pace_min: float) -> int:
    """按目标配速估算完成 distance_m 里程所需秒数。"""
    speed = pace_to_speed(pace_min)
    return max(1, int(round(distance_m / speed)))


def _meter_to_deg(lat: float, meters: float) -> float:
    """把米转换为纬度方向度数（近似），供噪声偏移使用。"""
    return meters / 111320.0


class GpsNoise:
    """有状态的 GPS 噪声生成器，逐点产生经纬度偏移。"""

    def __init__(self, seed: Optional[int] = None):
        self.rng = random.Random(seed)
        # AR(1) 漂移状态（米）
        self._drift_east = 0.0
        self._drift_north = 0.0
        self._phase = self.rng.uniform(0, 2 * math.pi)
        self.rho = 0.9
        self.white_scale = 0.5
        self.sway_amp = 0.4
        self.jump_prob = 0.002
        self.jump_scale = 1.5
        self.max_offset = 3.2

    def _tanh_clip(self, v: float) -> float:
        """tanh 软饱和，把偏移限制在 ±max_offset 内。"""
        return self.max_offset * math.tanh(v / self.max_offset)

    def next(self, lat: float):
        """返回 (d_lng_deg, d_lat_deg) 的噪声偏移。"""
        # AR(1) 漂移更新
        self._drift_east = self.rho * self._drift_east + self.rng.gauss(0, 0.15)
        self._drift_north = self.rho * self._drift_north + self.rng.gauss(0, 0.15)
        # 摆动
        self._phase += self.rng.uniform(0.05, 0.15)
        sway_east = self.sway_amp * math.sin(self._phase)
        sway_north = self.sway_amp * math.cos(self._phase)
        # 白噪声
        white_east = self.rng.gauss(0, self.white_scale)
        white_north = self.rng.gauss(0, self.white_scale)
        # 偶发跳点
        jump_east = jump_north = 0.0
        if self.rng.random() < self.jump_prob:
            jump_east = self.rng.uniform(-self.jump_scale, self.jump_scale)
            jump_north = self.rng.uniform(-self.jump_scale, self.jump_scale)

        east_m = self._tanh_clip(self._drift_east + sway_east + white_east + jump_east)
        north_m = self._tanh_clip(self._drift_north + sway_north + white_north + jump_north)

        # 米 -> 度
        d_lng = _meter_to_deg(lat, east_m) / max(math.cos(math.radians(lat)), 0.3)
        d_lat = _meter_to_deg(lat, north_m)
        return d_lng, d_lat


def resample_run(run_line_path: Sequence[Sequence[float]],
                 need_distance_m: float,
                 start_timestamp_ms: int,
                 pace_min: float,
                 limit_speed: Optional[float] = None,
                 seed: Optional[int] = None) -> List[SamplePoint]:
    """
    沿跑线按 1Hz 采样，生成完整轨迹。

    以「累计几何里程达到 need_distance_m」为停止条件（保证里程达标，PRD T8）。
    每个采样点的速度由动态速度曲线给出（首点加速 + 疲劳衰减 + 撞墙 + 限速裁剪）。

    参数：
        run_line_path: 清洗后的跑线 [[lng, lat], ...]
        need_distance_m: 目标里程（米）
        start_timestamp_ms: 起始绝对时间戳（毫秒）
        pace_min: 目标配速（分钟/公里）
        limit_speed: 限速（m/s），可选
        seed: 随机种子，用于复现

    返回：
        List[SamplePoint]，几何真值速度已按配速与限速规划，
        坐标已叠加 GPS 噪声。
    """
    rng = random.Random(seed)
    if len(run_line_path) < 2:
        raise ValueError('跑线点数不足')

    # 服务器返回的配置（如 limitSpeed）可能为字符串，统一转 float 防御
    if limit_speed is not None:
        limit_speed = float(limit_speed)
    pace_min = float(pace_min)

    total_line = total_length(run_line_path)
    if total_line <= 0:
        raise ValueError('跑线总长为 0')

    # 基准巡航速度（配速反解，并受限速约束）
    base_speed = pace_to_speed(pace_min)
    cruise_speed = base_speed
    if limit_speed is not None:
        cruise_speed = min(cruise_speed, limit_speed * 0.96)

    # 估算总时长（用于疲劳与撞墙的归一化），上浮 15% 以覆盖里程
    est_time_s = estimate_time_for_distance(need_distance_m, pace_min)
    total_time_est = int(est_time_s * 1.15)

    # 预生成撞墙窗口（在预估时间轴上随机选 1~2 处）
    wall_windows = []
    if total_time_est > 90:
        for _ in range(rng.randint(1, 2)):
            ws = rng.randint(int(total_time_est * 0.4), int(total_time_est * 0.7))
            wl = rng.randint(20, 40)
            wd = rng.uniform(0.20, 0.30)
            wall_windows.append((ws, wl, wd))

    def speed_at(sec_idx: int) -> float:
        """第 sec_idx 秒的目标速度（m/s）。"""
        # 首点加速
        if sec_idx < RAMP_UP_SECONDS:
            ratio = RAMP_START_RATIO + (1.0 - RAMP_START_RATIO) * (sec_idx / RAMP_UP_SECONDS)
            s = cruise_speed * ratio
        else:
            # 疲劳衰减
            progress = min(sec_idx / total_time_est, 1.0)
            fatigue = 1.0 - 0.06 * progress
            jitter = rng.uniform(-0.02, 0.02)
            s = cruise_speed * fatigue * (1.0 + jitter)
        # 撞墙
        for (ws, wl, wd) in wall_windows:
            if ws <= sec_idx < ws + wl:
                s *= (1.0 - wd)
                break
        # 限速裁剪
        if limit_speed is not None:
            s = min(s, limit_speed)
        return max(s, 0.0)

    noise = GpsNoise(seed=seed)

    samples: List[SamplePoint] = []
    cum_dist = 0.0
    current_t = start_timestamp_ms
    idx = 0
    # 安全上限：避免极端情况下死循环（最多 2 小时）
    max_seconds = 7200
    while cum_dist < need_distance_m and idx < max_seconds:
        speed = speed_at(idx)
        # 采样间隔抖动：模拟真实手机 GPS 不稳定性（旧版逻辑 1~1.3s）
        dt_ms = rng.randint(SAMPLE_INTERVAL_MIN_MS, SAMPLE_INTERVAL_MAX_MS)
        dt = dt_ms / 1000.0
        step_dist = speed * dt
        cum_dist += step_dist
        # 沿折线取几何坐标（超出总长则折返：取模）
        along = cum_dist % total_line
        geo_lng, geo_lat = point_at_distance(run_line_path, along)

        # 叠加 GPS 噪声
        d_lng, d_lat = noise.next(geo_lat)
        out_lng = geo_lng + d_lng
        out_lat = geo_lat + d_lat

        samples.append(SamplePoint(
            index=idx,
            t=current_t,
            geo_lng=geo_lng,
            geo_lat=geo_lat,
            out_lng=out_lng,
            out_lat=out_lat,
            speed=speed,
            distance=step_dist,
            cum_distance=cum_dist,
        ))
        current_t += dt_ms
        idx += 1

    return samples
