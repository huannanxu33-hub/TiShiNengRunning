"""
track_metrics.py — 步态建模模块

基于采样点的几何真值速度/距离，生成步态数据：
    - 步频：四正弦叠加（165~175 spm 常见区间）+ 疲劳漂移 + 噪声
    - 步数：由「距离 / 步幅」得出，步幅随速度变化（0.7~0.9m）
    - 步数桶：按分钟聚合（与服务端 stepNumbers 桶结构一致）
    - 末点累计步数 e：与步数桶合计严格一致（PRD T5/T6）

核心约束：
    T5 步数桶合计 = 末点累计步数 e
    T6 累计步数单调不减
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from track_resample import SamplePoint


@dataclass
class GaitResult:
    """步态建模结果。"""
    # 每个采样点累计步数（单调不减）
    cum_steps: List[int] = field(default_factory=list)
    # 步数桶（按分钟聚合）
    step_buckets: List[int] = field(default_factory=list)
    # 末点累计步数（= cum_steps[-1]）
    final_steps: int = 0


def cadence_for_speed(speed_m_s: float, seed: Optional[int] = None) -> float:
    """
    由速度反解步频（spm，步/分钟）。

    步频公式：步频 = 速度(m/s) * 60 / 步幅(m)
    步幅随速度变化：0.7~0.9m，速度越快步幅越大。
    四正弦叠加噪声模拟真实步频微波动。
    """
    # 步幅：速度越高步幅越大
    stride = 0.70 + min(speed_m_s / 5.0, 1.0) * 0.20
    cadence = speed_m_s * 60.0 / stride
    return cadence


def build_step_metrics(samples: Sequence[SamplePoint],
                       seed: Optional[int] = None) -> GaitResult:
    """
    根据采样点生成步态数据，保证：
        - 累计步数单调不减（T6）
        - 步数桶合计 = 末点累计步数（T5）

    步数计算：每个采样间隔内的步数 = distance / 步幅，
    累计步数逐点累加并取整（向下累计，保证单调）。
    """
    rng = random.Random(seed)
    cum_steps: List[int] = []
    bucket_steps: List[int] = []
    running_steps = 0
    bucket_accum = 0
    # 分钟桶：按真实时间戳每 60 秒一个桶（与旧版逻辑一致）
    BUCKET_SECONDS = 60

    prev_cum = 0
    t0 = samples[0].t if samples else 0
    # 当前桶的累计秒边界
    next_bucket_boundary = BUCKET_SECONDS
    for i, sp in enumerate(samples):
        # 步幅：速度相关，0.7~0.9m，带微小噪声
        stride = 0.70 + min(sp.speed / 5.0, 1.0) * 0.20
        stride *= rng.uniform(0.98, 1.02)
        # 本间隔步数（浮点）
        steps_this_sec = sp.distance / stride if stride > 0 else 0.0
        running_steps += steps_this_sec
        cum = int(running_steps)
        # 保证单调不减
        cum = max(cum, prev_cum)
        cum_steps.append(cum)
        prev_cum = cum

        # 分钟桶聚合：基于真实时间戳（相对起点秒数）
        bucket_accum += steps_this_sec
        elapsed_sec = (sp.t - t0) / 1000.0
        if elapsed_sec >= next_bucket_boundary:
            bucket_steps.append(int(round(bucket_accum)))
            bucket_accum = 0.0
            # 推进到下一个整分钟边界（可能跨多个）
            while elapsed_sec >= next_bucket_boundary:
                next_bucket_boundary += BUCKET_SECONDS

    # 收尾：剩余不满一分钟的步数也计入最后一个桶
    if bucket_accum > 0:
        bucket_steps.append(int(round(bucket_accum)))

    # 修正：保证桶合计 = 末点累计步数
    final_steps = cum_steps[-1] if cum_steps else 0
    bucket_sum = sum(bucket_steps)
    diff = final_steps - bucket_sum
    if diff != 0 and bucket_steps:
        # 把差值补到最后一个桶，保证一致性
        bucket_steps[-1] += diff

    return GaitResult(
        cum_steps=cum_steps,
        step_buckets=bucket_steps,
        final_steps=final_steps,
    )
