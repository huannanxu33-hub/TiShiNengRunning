"""
simulate_run.py — 离线模拟入口

在本地生成完整轨迹，不连接服务器、不上传数据（PRD E3）。

用法：
    python simulate_run.py --km 2.3 --seed 42 [--pace 6.5] [--limit-speed 3.3]
    python simulate_run.py --line "[[116.397,39.908],...]" --km 2.3

输出：
    - 生成轨迹 JSON 到本地文件
    - 打印自检结果与关键指标
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from track_clean import clean_path
from track_metrics import build_step_metrics
from track_resample import resample_run
from tsn_payload import build_payload, self_check

# 默认测试跑线（北京某校园折返线，起终点相距约 30m，符合 PRD 约束 25~47m）
DEFAULT_LINE: List[List[float]] = [
    [116.397000, 39.908000],
    [116.397100, 39.908100],
    [116.397200, 39.908150],
    [116.397300, 39.908120],
    [116.397400, 39.908050],
    [116.397350, 39.907980],
    [116.397250, 39.907950],
    [116.397150, 39.907980],
    [116.397050, 39.908030],
    [116.397000, 39.908000],
]


def simulate(run_line_path: Sequence[Sequence[float]],
             need_distance_m: float,
             pace_min: float = 6.5,
             limit_speed: Optional[float] = None,
             middle_faces_km: Sequence[float] = (),
             is_public: bool = True,
             start_timestamp_ms: Optional[int] = None,
             seed: Optional[int] = None) -> dict:
    """生成一条完整轨迹，返回可序列化结果。"""
    import time as _time
    if start_timestamp_ms is None:
        start_timestamp_ms = int(_time.time() * 1000)

    cleaned = clean_path(run_line_path)
    samples = resample_run(cleaned, need_distance_m, start_timestamp_ms,
                           pace_min, limit_speed, seed=seed)
    gait = build_step_metrics(samples, seed=seed)
    payload = build_payload(samples, gait, middle_faces_km, is_public=is_public)

    # 附加指标
    total_m = payload.sum_distance_m
    total_s = (samples[-1].t - samples[0].t) / 1000.0 if len(samples) > 1 else 0
    avg_speed = total_m / total_s if total_s > 0 else 0
    pace = (1000.0 / avg_speed) / 60.0 if avg_speed > 0 else 0

    return {
        'is_public': is_public,
        'seed': seed,
        'start_timestamp_ms': start_timestamp_ms,
        'pace_min_per_km': round(pace, 2),
        'total_distance_m': round(total_m, 2),
        'total_time_s': total_s,
        'avg_speed_m_s': round(avg_speed, 3),
        'peak_speed_m_s': round(max((s.speed for s in samples), default=0), 3),
        'step_buckets': payload.step_buckets,
        'final_steps': gait.final_steps,
        'bucket_sum': sum(payload.step_buckets),
        'face_points': payload.face_points,
        'errors': payload.errors,
        'warnings': payload.warnings,
        'path': payload.path,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='离线跑步轨迹模拟')
    parser.add_argument('--km', type=float, default=2.3, help='目标里程（公里）')
    parser.add_argument('--seed', type=int, default=42, help='随机种子')
    parser.add_argument('--pace', type=float, default=6.5, help='目标配速（分钟/公里）')
    parser.add_argument('--limit-speed', type=float, default=None, help='限速（m/s）')
    parser.add_argument('--line', type=str, default=None, help='跑线 JSON [[lng,lat],...]')
    parser.add_argument('--faces', type=str, default=None, help='中途人脸公里数，逗号分隔，如 1.1,1.6')
    parser.add_argument('--out', type=str, default=None, help='输出 JSON 文件路径')
    args = parser.parse_args(argv)

    line = DEFAULT_LINE
    if args.line:
        line = json.loads(args.line)

    faces = []
    if args.faces:
        faces = [float(x) for x in args.faces.split(',') if x.strip()]

    result = simulate(
        run_line_path=line,
        need_distance_m=args.km * 1000,
        pace_min=args.pace,
        limit_speed=args.limit_speed,
        middle_faces_km=faces,
        is_public=True,
        seed=args.seed,
    )

    print('=== 模拟结果 ===')
    print(f"配速: {result['pace_min_per_km']} min/km")
    print(f"里程: {result['total_distance_m']} m")
    print(f"用时: {result['total_time_s']} s")
    print(f"平均速度: {result['avg_speed_m_s']} m/s")
    print(f"峰值速度: {result['peak_speed_m_s']} m/s")
    print(f"步数桶合计: {result['bucket_sum']}, 末点: {result['final_steps']}")
    print(f"自检: {len(result['errors'])} error / {len(result['warnings'])} warning")
    if result['errors']:
        print('Errors:')
        for e in result['errors']:
            print(f'  - {e}')
    if result['warnings']:
        print('Warnings:')
        for w in result['warnings']:
            print(f'  - {w}')

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f'已保存到: {out_path}')

    return 0 if not result['errors'] else 1


if __name__ == '__main__':
    sys.exit(main())
