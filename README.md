# Karrot Community Post Crawler

A crawler that collects all community posts (`동네생활`) from Seoul neighborhoods on Karrot (당근마켓).

---

## File Structure

| File | Description |
|------|-------------|
| `app.py` | Main entry point with terminal UI |
| `crawler.py` | Crawling engine (used internally by `app.py`) |
| `export.py` | Tool to convert collected CSV to Excel or export with filters |
| `make_chunks.py` | Splits the full ID range into 2,000-unit chunks and saves to `chunks.txt` |
| `seoul_regions.json` | Mapping table of 685 Seoul neighborhood `regionId`s |

---

## Installation

```bash
pip install aiohttp rich openpyxl
```

---

## Usage

### Basic Run

```bash
python app.py
```

On launch, you will be prompted to enter a chunk number. The crawler will automatically configure the corresponding dbId range and begin crawling.

**Input Format**

| Example | Description |
|---------|-------------|
| `1-680` | Chunks 1 through 680 |
| `500` | Chunk 500 only |

The output file is automatically named in the format `daangn_chunk_1-680.csv`.

---

## How Crawling Works

### Overview

The crawler scans Karrot community posts (`동네생활`) by iterating over internal post IDs (`dbId`) in descending order, from the most recent to the oldest. Each `dbId` corresponds to a potential community post, and the crawler fetches each one to determine whether it exists and whether it belongs to a Seoul neighborhood.

The total dbId range covered is **727,174,000 – 733,968,953** (~6.8 million IDs).

---

### Request Mechanism

Each post is fetched via a single HTTP GET request to Karrot's internal API:

```
GET https://www.daangn.com/kr/community/{dbId}/?_data=routes%2Fkr.community.%24community_agora_id
```

The `_data` query parameter instructs the server to return raw JSON data (used by Remix's server-side data loading), bypassing HTML rendering. This makes responses lightweight and machine-readable.

**HTTP response handling:**

| Status Code | Meaning | Action |
|-------------|---------|--------|
| `200` | Post exists and is active | Parse and save if Seoul |
| `410` | Post exists but was deleted | Parse and save if Seoul |
| `404` | dbId does not exist (gap in numbering) | Skip silently |
| `429` | Rate limit exceeded | Backoff + slow down RPS |
| Other | Network or server error | Retry up to 2 times |

---

### Async + Concurrency Architecture

The crawler is fully asynchronous, built on Python's `asyncio` and `aiohttp`:

- **`asyncio.gather()`** dispatches an entire batch of requests concurrently.
- An **`asyncio.Semaphore`** caps the number of simultaneous TCP connections (default: 10).
- An **`AdaptiveRateLimiter`** (token-bucket based) enforces a per-second request rate (default: 15 RPS), inserting `await asyncio.sleep()` between requests as needed.

This design maximizes throughput while staying well within server-side rate limits under normal conditions.

---

### Adaptive Rate Limiter

The `AdaptiveRateLimiter` automatically adjusts crawling speed based on server responses:

- **On 429 (Too Many Requests):** RPS is immediately halved (`rps × 0.5`), capped at a minimum of 2 RPS. A 10-second cooldown follows.
- **On 5 consecutive clean batches:** RPS is increased by 10% (`rps × 1.1`), up to a configured maximum.

This allows the crawler to self-regulate without manual tuning, recovering speed after a throttle event while backing off aggressively when needed.

---

### Seoul Filtering

After fetching a post, the crawler checks whether its `regionId` is present in `seoul_regions.json`. This file maps 685 Seoul administrative neighborhood IDs (`regionId`) to their district (`gu`) and neighborhood name (`dong`). Only posts matching a Seoul `regionId` are written to the CSV output — posts from other regions are discarded.

---

### Batching and Progress Persistence

Posts are processed in batches of configurable size (default: 200 dbIds per batch). After every 500 collected Seoul posts, the crawler:

1. **Appends** new rows to the CSV file (UTF-8 BOM encoding for Excel compatibility).
2. **Saves** a progress snapshot to a JSON file (`daangn_chunk_<label>_progress.json`).

The progress file stores:

```json
{ "last_dbid": 733500000, "scanned": 12000, "collected": 2340 }
```

On restart with the same chunk label, the crawler reads this file and resumes exactly where it left off — no data is lost and no dbIds are re-scanned.

---

### Collected Data Fields

Each saved post record contains the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `dbId` | int | Karrot's internal numeric post ID |
| `regionId` | int | Administrative neighborhood ID (linked to `seoul_regions.json`) |
| `regionName` | string | Neighborhood name as stored in the post (e.g. `역삼1동`) |
| `gu` | string | District (borough) derived from `seoul_regions.json` (e.g. `강남구`) |
| `title` | string | Post title |
| `content` | string | Post body text, truncated to 500 characters; newlines removed |
| `subject` | string | Post category (e.g. `동네질문`, `동네맛집`, `생활정보`) |
| `status` | string | Post status — `published` for active posts, or deletion-related status for HTTP 410 responses |
| `createdAt` | datetime | Original post creation timestamp |
| `updatedAt` | datetime | Last modification timestamp |
| `commentsCount` | int | Number of comments |
| `emotionCount` | int | Number of reactions (likes/empathy) |
| `readsCount` | int | View count |
| `watchesCount` | int | Number of users who saved/bookmarked the post |
| `writer_nickName` | string | Author's display nickname |
| `writer_temperature` | float | Author's "manner temperature" score (Karrot's trust metric) |
| `writer_writeRegionName` | string | Author's verified neighborhood name |
| `imageCount` | int | Number of images attached to the post |
| `nodeId` | string | GraphQL node ID |
| `articleUrl` | string | Full URL to the original post (e.g. `https://www.daangn.com/kr/community/...`) |

---

## Pause & Resume

You can stop the crawler at any time with `Ctrl+C`. Progress is automatically saved and can be resumed later.

**To stop:**
- Press `Ctrl+C` — collected data and current position are saved immediately.

**To resume:**
- Run again with the same chunk label and the crawler will automatically continue from where it left off.
```bash
python app.py --chunk 1-680
```

**To restart from the beginning:**
```bash
python app.py --chunk 1-680 --reset
```

---

## Exporting Data (export.py)

After collection, you can convert the CSV to Excel or export filtered subsets.

```bash
python export.py
```

Menu options:

| # | Function |
|---|----------|
| 1 | Export full dataset as CSV |
| 2 | Export full dataset as Excel |
| 3 | Export filtered subset as CSV |
| 4 | Export filtered subset as Excel |
| 5 | Show summary by district / category |

Filter options: **district** (e.g. `강남구`), **neighborhood** (e.g. `역삼1동`), **category** (e.g. `동네맛집`)

---

## CLI Options (app.py)

| Option | Default | Description |
|--------|---------|-------------|
| `--chunk` | — | Chunk number or range (e.g. `1-680`, `500`). Skips the interactive prompt when specified |
| `--start` | 733968953 | Starting dbId (skips chunk UI when specified directly) |
| `--end` | 727174000 | Ending dbId |
| `--step` | 1 | dbId increment (1 = full scan, 10 = 10% sample) |
| `--workers` | 10 | Number of concurrent connections |
| `--batch` | 1 | Batch size |
| `--pause` | 4.0 | Cooldown between batches (seconds) |
| `--output` | daangn_seoul.csv | Output filename override |
| `--reset` | — | Ignore previous progress and start from the beginning |

---

## Step Reference

Full dbId range: 727,174,000 – 733,968,953 (~6.8 million IDs)

| Step | Sample Rate | Estimated Seoul Posts | Estimated Time |
|------|-------------|----------------------|----------------|
| 1 | 100% (full) | ~1,270,000 | ~8.3 days |
| 10 | 10% | ~127,000 | ~20 hours |
| 50 | 2% | ~25,000 | ~4 hours |
| 100 | 1% | ~13,000 | ~2 hours |
| 1000 | 0.1% | ~1,300 | ~12 minutes |
