from dataclasses import dataclass
from typing import Optional, List, Tuple
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from TiShiNengSdkPrivate import TiShiNengPrivate
from TiShiNengSdkPublic import TiShiNengSdkPublic
from database import get_db
from models import TsnAccount_Model
from tsnClient import getTsnClientById


@dataclass
class ValidRunCount:
    morning_run_count: Optional[int] = None
    sun_run_count: Optional[int] = None
    freedom_run_count: Optional[int] = None
    total_valid_count: Optional[int] = None
    total_distance_km: Optional[float] = None
    total_run_count: Optional[int] = None
    valid_records_count: Optional[int] = None
    total_records_count: Optional[int] = None

    def __str__(self):
        lines = []
        if self.morning_run_count is not None:
            lines.append(f'  晨跑有效次数: {self.morning_run_count}')
        if self.sun_run_count is not None:
            lines.append(f'  阳光跑有效次数: {self.sun_run_count}')
        if self.freedom_run_count is not None:
            lines.append(f'  自由跑有效次数: {self.freedom_run_count}')
        if self.total_valid_count is not None:
            lines.append(f'  总有效次数: {self.total_valid_count}')
        if self.total_distance_km is not None and self.total_distance_km > 0:
            lines.append(f'  总里程(km): {self.total_distance_km:.2f}')
        if self.total_run_count is not None:
            lines.append(f'  总跑步次数: {self.total_run_count}')
        if self.valid_records_count is not None:
            lines.append(f'  遍历有效记录数: {self.valid_records_count}')
        if self.total_records_count is not None:
            lines.append(f'  遍历总记录数: {self.total_records_count}')
        return '\n'.join(lines) if lines else '  暂无数据'


def _get_succeed(summary: dict, run_type: str) -> Optional[int]:
    run_data = summary.get(run_type, {})
    if not run_data:
        return None
    succeed = run_data.get('succeed')
    if succeed is None:
        return None
    try:
        return int(succeed)
    except (ValueError, TypeError):
        return None


async def crawl_valid_count_public(tsn: TiShiNengSdkPublic) -> ValidRunCount:
    result = ValidRunCount()
    try:
        summary = await tsn.sumExerciseRecord()
        logger.debug(f'公版 sumExerciseRecord 原始返回: {summary}')
        result.morning_run_count = _get_succeed(summary, 'morningRun')
        result.sun_run_count = _get_succeed(summary, 'sunRun')
        result.freedom_run_count = _get_succeed(summary, 'freedomRun')
        total = 0
        for ct in [result.morning_run_count, result.sun_run_count, result.freedom_run_count]:
            if ct is not None:
                total += ct
        if total > 0:
            result.total_valid_count = total
        total_dist = 0.0
        for run_type in ['morningRun', 'sunRun', 'freedomRun']:
            run_data = summary.get(run_type, {})
            if run_data:
                dist_str = run_data.get('runSportRange', '0')
                try:
                    dist = float(dist_str)
                    total_dist += dist
                except (ValueError, TypeError):
                    pass
        if total_dist > 0:
            result.total_distance_km = round(total_dist, 2)
    except Exception as e:
        logger.warning(f'公版 sumExerciseRecord 失败，尝试 statisticsExerciseRecord 获取: {e}')
        try:
            stats = await tsn.statisticsExerciseRecord()
            total_count = int(stats.get('allNum', 0) or stats.get('yearsNum', 0) or stats.get('totalCountZongSunRun', 0))
            result.total_valid_count = total_count
            total_dist_str = stats.get('yearsTotalMileage') or stats.get('allTotalMileage') or stats.get('sysTermTotalMileage')
            if total_dist_str:
                result.total_distance_km = round(float(total_dist_str), 2)
        except Exception as e2:
            logger.error(f'备用 statisticsExerciseRecord 也失败: {e2}')
    try:
        total_records = 0
        seen_ids = set()
        base = await tsn.listExerciseRecord(1, '', 1)
        dates = base.get('dates', [])
        for date_info in dates:
            date_str = date_info['date']
            page = 1
            while True:
                page_data = await tsn.listExerciseRecord(1, date_str, page)
                records = page_data.get('records', [])
                if not records:
                    break
                for rec in records:
                    rid = rec.get('id')
                    if rid not in seen_ids:
                        seen_ids.add(rid)
                        total_records += 1
                page += 1
                if page > 20:
                    break
        if total_records > 0:
            result.total_run_count = total_records
    except Exception as e:
        logger.warning(f'统计总记录数失败: {e}')
    return result


async def crawl_valid_count_private(tsn: TiShiNengPrivate) -> ValidRunCount:
    result = ValidRunCount()
    try:
        summary = await tsn.sumSportRecord()
        if isinstance(summary, dict):
            result.morning_run_count = summary.get('morningRun') or summary.get('morningRunCount')
            result.sun_run_count = summary.get('sunRun') or summary.get('sunRunCount')
            result.freedom_run_count = summary.get('freedom') or summary.get('freedomRunCount')
            result.total_valid_count = summary.get('validCount') or summary.get('totalValid')
            if result.total_valid_count is None:
                total = (result.morning_run_count or 0) + (result.sun_run_count or 0) + (result.freedom_run_count or 0)
                if total > 0:
                    result.total_valid_count = total
    except Exception as e:
        logger.warning(f'私版 sumSportRecord 失败，将遍历记录: {e}')
    if result.total_valid_count is None:
        valid_count = 0
        total_count = 0
        total_distance = 0.0
        seen_ids = set()
        try:
            for page in range(1, 20):
                resp = await tsn.appSportRecordList(2, page, 10)
                data_list = resp.get('data', [])
                if not data_list:
                    break
                for rec in data_list:
                    rid = rec.get('id')
                    if rid in seen_ids:
                        break
                    seen_ids.add(rid)
                    total_count += 1
                    if rec.get('sportStatus') == 1:
                        valid_count += 1
                        dist = float(rec.get('formatSportRange', rec.get('sportRange', 0)))
                        total_distance += dist
            result.total_records_count = total_count
            result.valid_records_count = valid_count
            result.total_valid_count = valid_count
            result.total_distance_km = total_distance
        except Exception as e:
            logger.exception(f'遍历私版记录失败: {e}')
    if result.total_records_count is not None:
        result.total_run_count = result.total_records_count
    return result


async def crawl_valid_count_for_account(account_id: int) -> ValidRunCount:
    async for db in get_db():
        tsn = await getTsnClientById(account_id, db)
        if tsn.isPublic():
            return await crawl_valid_count_public(tsn)
        else:
            return await crawl_valid_count_private(tsn)


async def crawl_valid_count_for_all_accounts() -> List[Tuple[TsnAccount_Model, ValidRunCount]]:
    results = []
    async for db in get_db():
        stmt = select(TsnAccount_Model).options(selectinload(TsnAccount_Model.school))
        db_accounts = await db.execute(stmt)
        accounts = db_accounts.scalars().all()
        for account in accounts:
            try:
                vc = await crawl_valid_count_for_account(account.id)
                results.append((account, vc))
            except Exception as e:
                logger.error(f'账号 {account.id} 爬取失败: {e}')
                results.append((account, ValidRunCount()))
        return results
