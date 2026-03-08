"""
crawler.py - 당근 동네생활 크롤링 엔진
======================================
UI 없이 단독 실행 가능, UI에서도 import하여 사용.

Phase 1: 배치 크기·대기 시간·workers 튜닝
Phase 2: asyncio + aiohttp 전환
Phase 3: 적응형 레이트 리미터 (429 자동 대응)
"""

import asyncio
import aiohttp
import json
import csv
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


FIELDNAMES = [
    "dbId","regionId","regionName","gu","title","content",
    "subject","status","createdAt","updatedAt",
    "commentsCount","emotionCount","readsCount","watchesCount",
    "writer_nickName","writer_temperature","writer_writeRegionName",
    "imageCount","nodeId","articleUrl",
]

HEADERS = {
    "accept": "*/*",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    ),
}


@dataclass
class Config:
    rps: float = 15.0              # 🚀 초당 최대 요청 수 (레이트 리미터)
    concurrency: int = 10          # 동시 연결 수 (TCP 커넥션)
    batch_size: int = 200          # 한 번에 처리할 dbId 수
    batch_pause: float = 0.2       # 배치 간 최소 대기
    step: int = 1
    save_every: int = 500
    request_timeout: float = 10.0
    retries: int = 2
    start_dbid: int = 733968953
    end_dbid: int = 727174000
    output_csv: str = "daangn_seoul.csv"
    progress_file: str = "daangn_progress.json"
    regions_file: str = "seoul_regions.json"


@dataclass
class Stats:
    scanned: int = 0
    collected: int = 0
    current_dbid: int = 0
    err_rate: float = 0.0
    speed: float = 0.0
    seoul_speed: float = 0.0
    elapsed: float = 0.0
    running: bool = False
    paused: bool = False
    gu_counts: dict = field(default_factory=dict)
    current_rps: float = 0.0       # 현재 적용 중인 RPS


def load_seoul_map(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return {int(r["id"]): r for r in json.load(f)}


def load_progress(path: str):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def save_progress(path: str, last_dbid: int, scanned: int, collected: int):
    with open(path, "w") as f:
        json.dump({"last_dbid": last_dbid, "scanned": scanned, "collected": collected}, f)


def append_csv(path: str, rows: list):
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, quoting=csv.QUOTE_ALL)
        if not exists:
            w.writeheader()
        w.writerows(rows)


def _clean(text: str, maxlen: int = 0) -> str:
    if not text:
        return ""
    t = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return t[:maxlen] if maxlen else t


def to_row(article: dict, seoul_map: dict) -> dict:
    rid = article.get("regionId")
    info = seoul_map.get(rid, {})
    wr = article.get("writer") or {}
    return {
        "dbId": article.get("dbId", ""),
        "regionId": rid,
        "regionName": article.get("regionName", ""),
        "gu": info.get("gu", ""),
        "title": _clean(article.get("title", "")),
        "content": _clean(article.get("content", ""), maxlen=500),
        "subject": article.get("subject", ""),
        "status": article.get("status", ""),
        "createdAt": article.get("createdAt", ""),
        "updatedAt": article.get("updatedAt", ""),
        "commentsCount": article.get("commentsCount", 0),
        "emotionCount": article.get("emotionCount", 0),
        "readsCount": article.get("readsCount", 0),
        "watchesCount": article.get("watchesCount", 0),
        "writer_nickName": _clean(wr.get("nickName", "")),
        "writer_temperature": wr.get("userTemperature", ""),
        "writer_writeRegionName": wr.get("writeRegionName", ""),
        "imageCount": len(article.get("imageUrls") or []),
        "nodeId": article.get("nodeId", ""),
        "articleUrl": f"https://www.daangn.com{article.get('id', '')}",
    }


# ── 🚀 적응형 레이트 리미터 ──────────────────────────────────────────────────

class AdaptiveRateLimiter:
    """
    토큰 버킷 기반 레이트 리미터.
    - 초당 요청 수(rps) 직접 제어
    - 429 감지 시 자동 감속 (rps * 0.5)
    - 정상 배치 연속 시 자동 가속 (rps * 1.1, 최대 max_rps)
    """

    def __init__(self, rps: float, min_rps: float = 2.0, max_rps: float = 30.0):
        self.rps = rps
        self.min_rps = min_rps
        self.max_rps = max_rps
        self._interval = 1.0 / rps  # 요청 간 최소 간격(초)
        self._last_request = 0.0
        self._lock = asyncio.Lock()
        self._ok_streak = 0         # 연속 정상 배치 수

    async def acquire(self):
        """다음 요청 허가를 기다림. 초당 rps를 넘지 않도록 대기."""
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = asyncio.get_event_loop().time()

    def on_429(self):
        """429 감지 → 속도 50% 감속"""
        old = self.rps
        self.rps = max(self.rps * 0.5, self.min_rps)
        self._interval = 1.0 / self.rps
        self._ok_streak = 0
        return old, self.rps

    def on_success(self):
        """정상 배치 → 5연속이면 속도 10% 증속"""
        self._ok_streak += 1
        if self._ok_streak >= 5:
            old = self.rps
            self.rps = min(self.rps * 1.1, self.max_rps)
            self._interval = 1.0 / self.rps
            self._ok_streak = 0
            return old, self.rps
        return None


# ── 비동기 fetch ──────────────────────────────────────────────────────────────

async def fetch_one_async(
    session: aiohttp.ClientSession,
    dbid: int,
    limiter: AdaptiveRateLimiter,
    sem: asyncio.Semaphore,
    retries: int = 2,
):
    """
    비동기 단건 요청 (레이트 리미터 + 세마포어 이중 제어).
    반환값: dict(유효 글) | "SKIP"(404) | {"_error": 원인, "_is_429": bool}(에러)
    """
    url = (
        f"https://www.daangn.com/kr/community/{dbid}/"
        f"?_data=routes%2Fkr.community.%24community_agora_id"
    )
    last_error = "unknown"
    is_429 = False

    async with sem:
        for attempt in range(retries + 1):
            # 레이트 리미터로 요청 간격 제어
            await limiter.acquire()
            try:
                async with session.get(url) as r:
                    if r.status in (200, 410):
                        data = await r.json(content_type=None)
                        return data.get("data", {}).get("communityArticle")
                    if r.status == 404:
                        return "SKIP"
                    if r.status == 429:
                        is_429 = True
                        last_error = "HTTP 429 (Too Many Requests)"
                        await asyncio.sleep(2 ** (attempt + 1))
                        continue
                    last_error = f"HTTP {r.status}"
                    if attempt < retries:
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue
            except aiohttp.ClientError as e:
                last_error = f"ClientError: {type(e).__name__}: {e}"
                if attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
            except asyncio.TimeoutError:
                last_error = f"TimeoutError (>{session.timeout.total}s)"
                if attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
            except Exception as e:
                last_error = f"Unexpected: {type(e).__name__}: {e}"
                if attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
    return {"_error": last_error, "_dbid": dbid, "_is_429": is_429}


async def fetch_batch_async(
    session: aiohttp.ClientSession,
    batch_ids: list,
    limiter: AdaptiveRateLimiter,
    sem: asyncio.Semaphore,
    retries: int,
) -> list:
    """배치 내 모든 dbId를 비동기 요청 (레이트 리미터가 자동으로 속도 조절)."""
    tasks = [
        fetch_one_async(session, dbid, limiter, sem, retries)
        for dbid in batch_ids
    ]
    results = await asyncio.gather(*tasks)
    return list(zip(batch_ids, results))


# ── 메인 크롤링 루프 ──────────────────────────────────────────────────────────

async def _run_crawl_async(
    cfg: Config,
    stats: Stats,
    on_batch: Optional[Callable] = None,
    on_save: Optional[Callable] = None,
    on_log: Optional[Callable] = None,
    stop_flag: Optional[Callable] = None,
    pause_flag: Optional[Callable] = None,
):
    log = on_log or (lambda m: print(m))

    seoul_map = load_seoul_map(cfg.regions_file)
    seoul_ids = set(seoul_map.keys())
    log(f"서울 regionId {len(seoul_ids)}개 로드")

    progress = load_progress(cfg.progress_file)
    if progress:
        start_id = cfg.start_dbid
        stats.scanned = progress["scanned"]
        stats.collected = progress["collected"]
        resume_dbid = progress["last_dbid"]
        log(f"이어서: 마지막 dbId={resume_dbid:,}, 스캔={stats.scanned:,}, 서울={stats.collected:,}")
    else:
        start_id = cfg.start_dbid
        stats.scanned = 0
        stats.collected = 0
        if os.path.exists(cfg.output_csv):
            os.remove(cfg.output_csv)

    buffer = []
    pause = cfg.batch_pause
    t_start = time.time()
    gu_counts = {}
    stats.running = True

    # 적응형 레이트 리미터 생성
    limiter = AdaptiveRateLimiter(rps=cfg.rps, min_rps=2.0, max_rps=cfg.rps * 2)
    sem = asyncio.Semaphore(cfg.concurrency)
    stats.current_rps = limiter.rps
    log(f"초기 RPS: {limiter.rps:.1f}, 동시연결: {cfg.concurrency}")

    timeout = aiohttp.ClientTimeout(total=cfg.request_timeout)
    connector = aiohttp.TCPConnector(limit=cfg.concurrency + 10, ttl_dns_cache=300)

    async with aiohttp.ClientSession(
        headers=HEADERS,
        timeout=timeout,
        connector=connector,
    ) as session:
        while True:
            if stop_flag and stop_flag():
                break

            while pause_flag and pause_flag():
                stats.paused = True
                await asyncio.sleep(0.3)
            stats.paused = False

            # 배치 생성
            batch_ids = [
                start_id - (stats.scanned + i) * cfg.step
                for i in range(cfg.batch_size)
            ]
            if batch_ids[-1] < cfg.end_dbid:
                batch_ids = [d for d in batch_ids if d >= cfg.end_dbid]
                if not batch_ids:
                    break

            # 비동기 병렬 요청 (레이트 리미터가 속도 제어)
            real_errors = 0
            got_429 = False
            new_rows = []
            error_samples = []
            results = await fetch_batch_async(session, batch_ids, limiter, sem, cfg.retries)

            for dbid, result in results:
                if result == "SKIP":
                    pass
                elif isinstance(result, dict) and "_error" in result:
                    real_errors += 1
                    if result.get("_is_429"):
                        got_429 = True
                    if len(error_samples) < 3:
                        error_samples.append(result["_error"])
                elif result is None:
                    real_errors += 1
                else:
                    rid = result.get("regionId")
                    if rid in seoul_ids:
                        row = to_row(result, seoul_map)
                        new_rows.append(row)
                        gu = row["gu"]
                        gu_counts[gu] = gu_counts.get(gu, 0) + 1

            stats.scanned += len(batch_ids)
            stats.collected += len(new_rows)
            stats.current_dbid = batch_ids[-1]
            stats.err_rate = real_errors / len(batch_ids) if batch_ids else 0
            stats.gu_counts = dict(gu_counts)

            elapsed = time.time() - t_start
            stats.elapsed = elapsed
            stats.speed = stats.scanned / elapsed if elapsed > 0 else 0
            stats.seoul_speed = stats.collected / elapsed if elapsed > 0 else 0

            buffer.extend(new_rows)

            # 적응형 속도 조절
            if got_429:
                old, new = limiter.on_429()
                stats.current_rps = limiter.rps
                err_detail = " | ".join(error_samples) if error_samples else "429"
                log(f"🔽 429 감지! RPS {old:.1f} → {new:.1f} [{err_detail}]")
                # 429 후 10초 쿨다운
                await asyncio.sleep(10)
            elif stats.err_rate > 0.2:
                err_detail = " | ".join(error_samples) if error_samples else "원인 불명"
                log(f"⚠ 에러율 {stats.err_rate:.0%} [{err_detail}]")
            else:
                result = limiter.on_success()
                if result:
                    old, new = result
                    stats.current_rps = limiter.rps
                    log(f"🔼 속도 회복: RPS {old:.1f} → {new:.1f}")

            stats.current_rps = limiter.rps

            if on_batch:
                on_batch(stats)

            # 중간 저장
            if len(buffer) >= cfg.save_every:
                append_csv(cfg.output_csv, buffer)
                save_progress(cfg.progress_file, stats.current_dbid, stats.scanned, stats.collected)
                buffer = []
                if on_save:
                    on_save(stats)

            await asyncio.sleep(pause)

    # 잔여 저장
    if buffer:
        append_csv(cfg.output_csv, buffer)
    save_progress(cfg.progress_file, stats.current_dbid, stats.scanned, stats.collected)
    stats.running = False
    log(f"완료: {stats.scanned:,}건 스캔 → 서울 {stats.collected:,}건")


def run_crawl(
    cfg: Config,
    stats: Stats,
    on_batch: Optional[Callable] = None,
    on_save: Optional[Callable] = None,
    on_log: Optional[Callable] = None,
    stop_flag: Optional[Callable] = None,
    pause_flag: Optional[Callable] = None,
):
    """
    메인 크롤링 진입점 (동기 래퍼).
    app.py의 스레드에서 호출 → 내부에서 asyncio 이벤트 루프를 생성해 실행.
    """
    asyncio.run(
        _run_crawl_async(
            cfg, stats,
            on_batch=on_batch,
            on_save=on_save,
            on_log=on_log,
            stop_flag=stop_flag,
            pause_flag=pause_flag,
        )
    )
