"""
tsn_payload.py — 载荷组装、人脸定位与提交前自检

把采样点 + 步态数据组装成公版/私版上传载荷，并：
    - 人脸定位：middleFaces 是「公里数」列表（如 1.637km），
      换算成路径上的采样点索引（修复 PRD P2「公里数被当比例用」）
    - 提交前自检：校验 T1~T10 约束，输出 error/warning

字段结构（与现有 tsnRunServer 上传逻辑对齐）：
    公版点: {a:lat, o:lng, c:累计秒, e:累计步数, i:bool, l:1, s:速度, t:时间戳ms}
    私版点: {latitude, longitude, countTime, locationType, puase, speed, stability, time}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from track_metrics import GaitResult
from track_resample import SamplePoint


@dataclass
class PayloadResult:
    """组装后的载荷。"""
    path: List[dict] = field(default_factory=list)
    step_buckets: List[int] = field(default_factory=list)
    sum_distance_m: float = 0.0
    face_points: List[dict] = field(default_factory=list)  # 中途人脸点
    # 自检结果
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _build_public_point(sp: SamplePoint, cum_sec: int, cum_steps: int, is_final: bool) -> dict:
    """组装公版点结构。"""
    return {
        'a': round(sp.out_lat, 15),
        'o': round(sp.out_lng, 15),
        'c': cum_sec,
        'e': cum_steps,
        'i': is_final,
        'l': 1,
        's': round(sp.speed, 9),
        't': sp.t,
    }


def _build_private_point(sp: SamplePoint, is_final: bool) -> dict:
    """组装私版点结构。"""
    return {
        'countTime': 0,
        'latitude': round(sp.out_lat, 15),
        'locationType': 1,
        'longitude': round(sp.out_lng, 15),
        'puase': is_final,
        'speed': round(sp.speed, 9),
        'stability': 0,
        'time': sp.t,
    }


def locate_face_points(samples: Sequence[SamplePoint],
                       middle_faces_km: Sequence[float]) -> List[dict]:
    """
    把中途人脸「公里数」定位为采样点。

    PRD P2：middleFaces 是公里数（如 1.637 表示 1.637km 处做一次人脸验证），
    需除以总里程得到比例，再定位到对应采样点。

    返回：[{'index', 'lng', 'lat', 'timestamp', 'km'}, ...]
    """
    if not samples:
        return []
    total_km = samples[-1].cum_distance / 1000.0
    if total_km <= 0:
        return []
    face_points = []
    n = len(samples)
    for km in middle_faces_km:
        km = float(km)
        if km <= 0:
            continue
        # 公里数 -> 采样点索引（比例）
        ratio = km / total_km
        ratio = max(0.0, min(ratio, 1.0))
        idx = int(round((n - 1) * ratio))
        sp = samples[idx]
        face_points.append({
            'index': idx,
            'lng': sp.out_lng,
            'lat': sp.out_lat,
            'timestamp': sp.t,
            'km': km,
        })
    return face_points


def build_payload(samples: Sequence[SamplePoint],
                  gait: GaitResult,
                  middle_faces_km: Sequence[float],
                  is_public: bool = True) -> PayloadResult:
    """
    组装完整载荷。

    参数：
        samples: 采样点列表
        gait: 步态结果
        middle_faces_km: 中途人脸公里数列表
        is_public: 是否公版
    """
    result = PayloadResult()
    result.path = []
    n = len(samples)
    # 起点时间戳，用于计算真实累计秒（c 与 t 保持自洽）
    t0 = samples[0].t if samples else 0

    for i, sp in enumerate(samples):
        # 累计秒：基于真实时间戳差值，向下取整（与真实手机非严格 1Hz 采样的 c 语义一致，
        # 连续两点落在同一整数秒内时 c 自然重复，避免 round 导致的跳值）
        cum_sec = int((sp.t - t0) / 1000.0)
        cum_steps = gait.cum_steps[i] if i < len(gait.cum_steps) else 0
        is_final = (i == n - 1) or (i == n - 2)
        if is_public:
            result.path.append(_build_public_point(sp, cum_sec, cum_steps, is_final))
        else:
            result.path.append(_build_private_point(sp, is_final))

    result.step_buckets = gait.step_buckets
    result.sum_distance_m = samples[-1].cum_distance if samples else 0.0
    result.face_points = locate_face_points(samples, middle_faces_km)

    result.errors, result.warnings = self_check(samples, gait, is_public)
    return result


def self_check(samples: Sequence[SamplePoint],
               gait: GaitResult,
               is_public: bool = True,
               limit_speed: Optional[float] = None,
               pace_range: tuple = (6.0, 7.0),
               required_distance_m: Optional[float] = None) -> tuple:
    """
    提交前自检，返回 (errors, warnings)。

    校验 PRD T1~T10 中的可本地校验项。
    """
    errors: List[str] = []
    warnings: List[str] = []

    # 服务器返回的限速配置可能为字符串，统一转 float 防御
    if limit_speed is not None:
        limit_speed = float(limit_speed)

    if not samples:
        errors.append('采样点为空')
        return errors, warnings

    # T3 采样间隔一致（1.0~1.3s 抖动区间）
    for i in range(1, len(samples)):
        dt = samples[i].t - samples[i - 1].t
        if not (1000 <= dt <= 1300):
            errors.append(f'采样间隔异常：index={i}, dt={dt}ms')
            break

    # T4 采样序号严格递增
    for i in range(1, len(samples)):
        if samples[i].index != samples[i - 1].index + 1:
            errors.append(f'采样序号不连续：index={i}')
            break

    # T1 配速区间
    total_m = samples[-1].cum_distance
    total_s = (samples[-1].t - samples[0].t) / 1000.0 if len(samples) > 1 else 0
    avg_speed = total_m / total_s if total_s > 0 else 0
    pace = (1000.0 / avg_speed) / 60.0 if avg_speed > 0 else 0
    if not (pace_range[0] <= pace <= pace_range[1]):
        warnings.append(f'平均配速 {pace:.2f} min/km 超出 {pace_range} 区间')

    # T2 上报速度 <= 限速
    if limit_speed is not None:
        over = [i for i, s in enumerate(samples) if s.speed > limit_speed]
        if over:
            errors.append(f'存在 {len(over)} 个超速点，峰值 {max(s.speed for s in samples):.3f}')

    # T5 步数桶合计 = 末点累计步数
    bucket_sum = sum(gait.step_buckets)
    final_steps = gait.final_steps
    if bucket_sum != final_steps:
        errors.append(f'步数桶合计 {bucket_sum} != 末点 {final_steps}')

    # T6 累计步数单调不减
    for i in range(1, len(gait.cum_steps)):
        if gait.cum_steps[i] < gait.cum_steps[i - 1]:
            errors.append(f'累计步数非单调：index={i}')
            break

    # T8 里程覆盖
    if required_distance_m is not None and total_m < required_distance_m:
        errors.append(f'里程 {total_m:.0f}m < 要求 {required_distance_m:.0f}m')

    return errors, warnings
