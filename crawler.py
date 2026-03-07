"""
crawler.py - 당근 동네생활 크롤링 엔진
======================================
UI 없이 단독 실행 가능, UI에서도 import하여 사용.
"""

import requests
import json
import csv
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

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
    workers: int = 10
    batch_size: int = 1
    batch_pause: float = 4.0
    step: int = 1
    save_every: int = 50
    start_dbid: int = 733968953
    end_dbid: int = 727174000       # 유효 글 하한 (이 아래는 404)
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
    """텍스트 필드에서 줄바꿈, 탭, 캐리지리턴 제거."""
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


def fetch_one(session: requests.Session, dbid: int):
    """200/410=유효 글, 404=존재하지 않는 번호, 그 외=에러."""
    try:
        r = session.get(
            f"https://www.daangn.com/kr/community/{dbid}/"
            f"?_data=routes%2Fkr.community.%24community_agora_id",
            timeout=15,
        )
        if r.status_code in (200, 410):
            return r.json().get("data", {}).get("communityArticle")
        if r.status_code == 404:
            return "SKIP"  # 빈 번호, 에러 아님
    except:
        pass
    return None


def run_crawl(
    cfg: Config,
    stats: Stats,
    on_batch: Callable = None,
    on_save: Callable = None,
    on_log: Callable = None,
    stop_flag: Callable = None,
    pause_flag: Callable = None,
):
    """
    메인 크롤링 루프.
    - on_batch(stats): 배치 완료마다 호출
    - on_save(stats): CSV 저장마다 호출
    - on_log(msg): 로그 메시지
    - stop_flag(): True 반환 시 중단
    - pause_flag(): True 반환 시 일시정지 대기
    """
    log = on_log or (lambda m: print(m))

    seoul_map = load_seoul_map(cfg.regions_file)
    seoul_ids = set(seoul_map.keys())
    log(f"서울 regionId {len(seoul_ids)}개 로드")

    progress = load_progress(cfg.progress_file)
    if progress:
        start_id = progress["last_dbid"] - 1
        stats.scanned = progress["scanned"]
        stats.collected = progress["collected"]
        log(f"이어서: dbId={start_id:,}, 스캔={stats.scanned:,}, 서울={stats.collected:,}")
    else:
        start_id = cfg.start_dbid
        stats.scanned = 0
        stats.collected = 0
        if os.path.exists(cfg.output_csv):
            os.remove(cfg.output_csv)

    session = requests.Session()
    session.headers.update(HEADERS)

    buffer = []
    pause = cfg.batch_pause
    consecutive_errors = 0
    t_start = time.time()
    gu_counts = {}
    stats.running = True

    while True:
        # 중단
        if stop_flag and stop_flag():
            break

        # 일시정지
        while pause_flag and pause_flag():
            stats.paused = True
            time.sleep(0.3)
        stats.paused = False

        # 배치 생성
        batch_ids = [start_id - (stats.scanned + i) * cfg.step for i in range(cfg.batch_size)]
        if batch_ids[-1] < cfg.end_dbid:
            batch_ids = [d for d in batch_ids if d >= cfg.end_dbid]
            if not batch_ids:
                break

        # 병렬 요청
        ok = 0
        skipped = 0
        real_errors = 0
        new_rows = []
        with ThreadPoolExecutor(max_workers=cfg.workers) as ex:
            futures = [ex.submit(fetch_one, session, did) for did in batch_ids]
            for f in as_completed(futures):
                result = f.result()
                if result == "SKIP":
                    skipped += 1       # 404 = 빈 번호, 정상
                elif result is None:
                    real_errors += 1   # 진짜 에러
                else:
                    ok += 1
                    rid = result.get("regionId")
                    if rid in seoul_ids:
                        row = to_row(result, seoul_map)
                        new_rows.append(row)
                        gu = row["gu"]
                        gu_counts[gu] = gu_counts.get(gu, 0) + 1

        stats.scanned += len(batch_ids)
        stats.collected += len(new_rows)
        stats.current_dbid = batch_ids[-1]
        # 에러율 = 진짜 에러만 (404 제외)
        stats.err_rate = real_errors / len(batch_ids) if batch_ids else 0
        stats.gu_counts = dict(gu_counts)

        elapsed = time.time() - t_start
        stats.elapsed = elapsed
        stats.speed = stats.scanned / elapsed if elapsed > 0 else 0
        stats.seoul_speed = stats.collected / elapsed if elapsed > 0 else 0

        buffer.extend(new_rows)

        # 쓰로틀링
        if stats.err_rate > 0.2:
            consecutive_errors += 1
            pause = min(pause * 1.5, 30.0)
            log(f"⚠ 에러율 {stats.err_rate:.0%}, 쿨다운 → {pause:.1f}초")
            if consecutive_errors >= 5:
                log("⏸ 연속 에러, 60초 대기...")
                time.sleep(60)
                consecutive_errors = 0
        else:
            consecutive_errors = 0
            if pause > cfg.batch_pause:
                pause = max(pause * 0.8, cfg.batch_pause)

        if on_batch:
            on_batch(stats)

        # 중간 저장
        if len(buffer) >= cfg.save_every:
            append_csv(cfg.output_csv, buffer)
            save_progress(cfg.progress_file, stats.current_dbid, stats.scanned, stats.collected)
            buffer = []
            if on_save:
                on_save(stats)

        time.sleep(pause)

    # 잔여 저장
    if buffer:
        append_csv(cfg.output_csv, buffer)
    save_progress(cfg.progress_file, stats.current_dbid, stats.scanned, stats.collected)
    stats.running = False
    log(f"완료: {stats.scanned:,}건 스캔 → 서울 {stats.collected:,}건")
