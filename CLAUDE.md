# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 실행

```bash
# venv 사용
.venv/bin/python app.py

# 청크 직접 지정
.venv/bin/python app.py --chunk 1-680
.venv/bin/python app.py --chunk 1-680 --reset   # 처음부터

# 처음부터 재실행
.venv/bin/python app.py --chunk 301-400 --reset
```

## 패키지 설치

```bash
.venv/bin/pip install aiohttp rich openpyxl
```

## 아키텍처

**`crawler.py`** — 크롤링 엔진 (비동기 asyncio + aiohttp)
- `Config`: 크롤링 파라미터 (rps, concurrency, batch_size 등)
- `Stats`: 실시간 통계 (UI 스레드와 공유)
- `AdaptiveRateLimiter`: 토큰 버킷 기반 속도 제어. 429 감지 시 RPS 50% 감속, 5배치 연속 정상 시 10% 증속
- `run_crawl()`: 동기 진입점 (내부에서 `asyncio.run()` 호출) → `app.py`의 별도 스레드에서 실행

**`app.py`** — 터미널 UI + 진입점
- `rich.Live`로 실시간 대시보드 렌더링 (별도 스레드에서 `run_crawl` 실행)
- 청크 선택 UI: 전체 범위(733,968,953 ~ 727,174,000)를 2,000개 단위로 분할, 5명 분업 안내
- `--chunk 301-400` 형태로 직접 지정 가능

**진행 저장/재개 구조**
- progress 파일: `daangn_chunk_{label}_progress.json` → `{last_dbid, scanned, collected}`
- 재개 시 `start_id = cfg.start_dbid` 유지 + `stats.scanned` 복원 → 배치 공식: `start_id - (scanned + i) * step`
- `last_dbid` 값이 0이어도 `scanned` 값으로 정확히 이어서 시작 가능

**`seoul_regions.json`** — 서울 685개 행정동 regionId → `{id, gu, name}` 매핑

**데이터 흐름**
1. API: `https://www.daangn.com/kr/community/{dbId}/?_data=routes%2Fkr.community.%24community_agora_id`
2. 200/410 → 유효 글, 404 → SKIP (빈 번호), 429 → 속도 감속
3. `regionId`가 `seoul_regions.json`에 있으면 CSV에 저장
4. 500건마다 CSV append + progress 저장 (`save_every=500`)
