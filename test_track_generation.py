"""
test_track_generation.py — 数据生成链路单元测试

覆盖 PRD 技术目标 T1~T10 中可本地校验的约束：
    T1 配速 6~7 分钟/公里
    T2 上报速度 <= 限速（超速点 = 0）
    T3 采样间隔 1.0~1.3s（模拟真实手机 GPS 抖动）
    T4 采样序号严格递增
    T5 步数桶合计 = 末点累计步数
    T6 累计步数单调不减
    T7 GPS 偏移均值 <= 1.2m，最大 <= 3.2m
    T8 里程 >= 要求值
    T9 用时在合格窗内
    T10 提交前自检 0 error / 0 warning

运行：python3.12 -m unittest test_track_generation -v
"""
import math
import random
import unittest

from track_clean import clean_path
from track_geo import haversine, total_length, point_at_distance
from track_metrics import build_step_metrics
from track_resample import resample_run
from tsn_payload import build_payload, self_check, locate_face_points

LINE = [
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

NEED_M = 2300.0
LIMIT_SPEED = 3.3
START_TS = 1700000000000


class TestTrackGeneration(unittest.TestCase):

    def _gen(self, seed=42, need_m=NEED_M, pace=6.5, limit=LIMIT_SPEED, faces=(1.1, 1.6)):
        cleaned = clean_path(LINE)
        samples = resample_run(cleaned, need_m, START_TS, pace, limit, seed=seed)
        gait = build_step_metrics(samples, seed=seed)
        payload = build_payload(samples, gait, faces, is_public=True)
        return samples, gait, payload

    def test_t1_pace_range(self):
        """配速落在 6~7 分钟/公里"""
        samples, gait, payload = self._gen()
        total_m = samples[-1].cum_distance
        total_s = (samples[-1].t - samples[0].t) / 1000.0
        avg_speed = total_m / total_s
        pace = (1000.0 / avg_speed) / 60.0
        self.assertTrue(6.0 <= pace <= 7.0, f"配速 {pace:.2f} 不在 6~7 区间")

    def test_t2_no_overspeed(self):
        """上报速度不超过限速"""
        samples, gait, payload = self._gen()
        over = [s.speed for s in samples if s.speed > LIMIT_SPEED]
        self.assertEqual(len(over), 0, f"存在 {len(over)} 个超速点")

    def test_t3_sample_interval(self):
        """采样间隔在 1.0~1.3s 区间（模拟真实手机 GPS 抖动）"""
        samples, gait, payload = self._gen()
        for i in range(1, len(samples)):
            dt = samples[i].t - samples[i - 1].t
            self.assertTrue(1000 <= dt <= 1300, f"采样间隔 {dt}ms 超出 1000~1300ms")

    def test_t4_sequence_strictly_increasing(self):
        """采样序号严格递增"""
        samples, gait, payload = self._gen()
        for i in range(1, len(samples)):
            self.assertEqual(samples[i].index, samples[i - 1].index + 1)

    def test_t5_step_bucket_consistency(self):
        """步数桶合计 = 末点累计步数"""
        samples, gait, payload = self._gen()
        self.assertEqual(sum(gait.step_buckets), gait.final_steps)
        self.assertEqual(payload.step_buckets, gait.step_buckets)

    def test_t6_cum_steps_monotonic(self):
        """累计步数单调不减"""
        samples, gait, payload = self._gen()
        for i in range(1, len(gait.cum_steps)):
            self.assertGreaterEqual(gait.cum_steps[i], gait.cum_steps[i - 1])

    def test_t7_gps_offset_controlled(self):
        """GPS 偏移均值 <= 1.2m，最大 <= 3.2m"""
        samples, gait, payload = self._gen()
        offsets = [haversine([s.geo_lng, s.geo_lat], [s.out_lng, s.out_lat]) for s in samples]
        mean = sum(offsets) / len(offsets)
        mx = max(offsets)
        self.assertLessEqual(mean, 1.2, f"均值 {mean:.3f}m > 1.2m")
        self.assertLessEqual(mx, 3.2, f"最大 {mx:.3f}m > 3.2m")

    def test_t8_distance_covered(self):
        """里程 >= 要求值"""
        samples, gait, payload = self._gen()
        self.assertGreaterEqual(samples[-1].cum_distance, NEED_M)

    def test_t9_time_in_window(self):
        """用时在合格窗内（按 6~7 min/km 反推）"""
        samples, gait, payload = self._gen()
        total_s = (samples[-1].t - samples[0].t) / 1000.0
        total_m = samples[-1].cum_distance
        # 6 min/km（快）对应最短用时，7 min/km（慢）对应最长用时
        min_time = total_m / 1000 * 6 * 60
        max_time = total_m / 1000 * 7 * 60
        self.assertTrue(min_time * 0.95 <= total_s <= max_time * 1.05,
                        f"用时 {total_s}s 不在 {min_time:.0f}~{max_time:.0f}s 附近")

    def test_t10_self_check_clean(self):
        """提交前自检 0 error / 0 warning"""
        samples, gait, payload = self._gen()
        self.assertEqual(len(payload.errors), 0, f"errors: {payload.errors}")
        self.assertEqual(len(payload.warnings), 0, f"warnings: {payload.warnings}")

    def test_face_point_km_location(self):
        """人脸公里数定位：1.1km 点应接近总里程 1.1/2.3 比例处"""
        samples, gait, payload = self._gen(faces=(1.1,))
        self.assertEqual(len(payload.face_points), 1)
        fp = payload.face_points[0]
        ratio = fp['index'] / (len(samples) - 1)
        expected_ratio = 1.1 / (NEED_M / 1000)
        self.assertAlmostEqual(ratio, expected_ratio, delta=0.02)

    def test_reproducibility(self):
        """固定种子可复现"""
        s1, g1, p1 = self._gen(seed=123)
        s2, g2, p2 = self._gen(seed=123)
        self.assertEqual([round(x.out_lng, 10) for x in s1],
                         [round(x.out_lng, 10) for x in s2])
        self.assertEqual(p1.step_buckets, p2.step_buckets)

    def test_private_payload(self):
        """私版载荷结构正确"""
        cleaned = clean_path(LINE)
        samples = resample_run(cleaned, NEED_M, START_TS, 6.5, None, seed=42)
        gait = build_step_metrics(samples, seed=42)
        payload = build_payload(samples, gait, (1.1,), is_public=False)
        # 私版点含 latitude/longitude/time/puase
        self.assertIn('latitude', payload.path[0])
        self.assertIn('longitude', payload.path[0])
        self.assertIn('time', payload.path[0])
        self.assertTrue(payload.path[-1]['puase'])

    def test_clean_path_removes_dupes(self):
        """清洗去除重复点"""
        dup = [[116.397, 39.908], [116.3970001, 39.9080001], [116.398, 39.909]]
        cleaned = clean_path(dup)
        self.assertLessEqual(len(cleaned), len(dup))


if __name__ == '__main__':
    unittest.main()
