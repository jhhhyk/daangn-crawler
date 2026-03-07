"""
app.py - 당근 동네생활 크롤러 (터미널 UI)
==========================================
실행:
  python app.py                          # 기본 설정
  python app.py --start 733968953 --end 733000000  # 범위 지정
  python app.py --step 10                # STEP만 변경
  python app.py --start 733500000 --end 733000000 --step 50 --workers 15

종료: Ctrl+C (진행 자동 저장)
필요 패키지: pip install rich openpyxl
"""

import threading
import signal
import sys
import os
import csv
import time
import argparse
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn
from rich import box

from crawler import Config, Stats, run_crawl, FIELDNAMES

console = Console()

# ── 청크 상수 ─────────────────────────────────────────────
CHUNK_TOTAL_START = 733_968_953
CHUNK_TOTAL_END   = 727_174_000
CHUNK_SIZE        = 2_000
CHUNK_PEOPLE      = 5


def _all_chunks():
    """전체 dbId 범위를 CHUNK_SIZE 단위로 분할한 리스트 반환. [(idx, start, end), ...]"""
    chunks, cur, idx = [], CHUNK_TOTAL_START, 1
    while cur > CHUNK_TOTAL_END:
        end = max(cur - CHUNK_SIZE + 1, CHUNK_TOTAL_END)
        chunks.append((idx, cur, end))
        cur = end - 1
        idx += 1
    return chunks


def _parse_chunk_range(raw: str, total: int):
    """'500' 또는 '1-847' 형태 → (from, to) 정수 튜플. 실패 시 ValueError."""
    raw = raw.strip()
    if "-" in raw:
        a, b = raw.split("-", 1)
        c_from, c_to = int(a), int(b)
    else:
        c_from = c_to = int(raw)
    if not (1 <= c_from <= c_to <= total):
        raise ValueError
    return c_from, c_to


def select_chunk_interactive():
    """
    청크 선택 UI.
    반환: (start_dbid, end_dbid, label)
    """
    chunks = _all_chunks()
    total  = len(chunks)
    per    = total // CHUNK_PEOPLE
    rem    = total % CHUNK_PEOPLE

    console.print("\n [bold orange1]청크 분배 안내[/]")
    console.print(f"  전체 청크: {total:,}개  (dbId 2,000개 단위)\n")

    start_i = 0
    for p in range(1, CHUNK_PEOPLE + 1):
        count = per + (1 if p <= rem else 0)
        end_i = start_i + count - 1
        first, last = chunks[start_i], chunks[end_i]
        console.print(
            f"  [bold]{p}번[/]: 청크 {first[0]:>4} ~ {last[0]:>4}  "
            f"dbId {last[2]:,} ~ {first[1]:,}  ({count}개 청크)"
        )
        start_i = end_i + 1

    console.print()
    console.print("  크롤링할 청크 번호를 입력하세요.")
    console.print("  예) 단일: [bold]500[/]   범위: [bold]1-847[/]")
    console.print()

    while True:
        raw = input("  청크 번호: ").strip()
        try:
            c_from, c_to = _parse_chunk_range(raw, total)
            start_dbid = chunks[c_from - 1][1]
            end_dbid   = chunks[c_to   - 1][2]
            label      = raw.replace(" ", "")
            console.print(
                f"\n  [green]선택 완료[/]: 청크 {c_from}~{c_to}  "
                f"(dbId {end_dbid:,} ~ {start_dbid:,})\n"
            )
            return start_dbid, end_dbid, label
        except ValueError:
            console.print(f"  [red]올바른 형식으로 입력하세요. (범위: 1~{total:,})[/]")
# ─────────────────────────────────────────────────────────

# ── 설정 (CLI 인자 또는 기본값) ─────────────────────────
def parse_args():
    p = argparse.ArgumentParser(
        description="🥕 당근 동네생활 크롤러 - 서울 전수 수집",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument("--start", type=int, default=733968953,
                   help="시작 dbId (기본: 733968953)")
    p.add_argument("--end", type=int, default=727174000,
                   help="종료 dbId (기본: 727174000)")
    p.add_argument("--step", type=int, default=1,
                   help="dbId 간격 (1=전수, 100=1%%샘플) (기본: 1)")
    p.add_argument("--workers", type=int, default=10,
                   help="동시 요청 수 (기본: 10)")
    p.add_argument("--batch", type=int, default=1,
                   help="배치 크기 (기본: 1)")
    p.add_argument("--pause", type=float, default=4.0,
                   help="배치 간 쿨다운 초 (기본: 4.0)")
    p.add_argument("--output", type=str, default="daangn_seoul.csv",
                   help="출력 CSV 파일명 (기본: daangn_seoul.csv)")
    p.add_argument("--chunk", type=str, default=None,
                   help="청크 번호 또는 범위 (예: 500, 1-847). 지정 시 대화형 선택 생략")
    p.add_argument("--format", choices=["csv", "xlsx"], default=None,
                   help="출력 형식: csv 또는 xlsx. 지정 시 대화형 선택 생략")
    p.add_argument("--reset", action="store_true",
                   help="이전 진행 무시하고 처음부터 시작")
    return p.parse_args()

args = parse_args()

cfg = Config(
    workers=args.workers,
    batch_size=args.batch,
    batch_pause=args.pause,
    step=args.step,
    save_every=500,
    start_dbid=args.start,
    end_dbid=args.end,
    output_csv=args.output,
    progress_file=args.output.replace(".csv", "_progress.json"),
    regions_file="seoul_regions.json",
)

# reset은 청크 확정 후 main()에서 처리
# ────────────────────────────────────────────────────────

stats = Stats()
logs = []
stop_event = threading.Event()
pause_event = threading.Event()


def on_log(msg):
    ts = time.strftime("%H:%M:%S")
    logs.append(f"[dim]{ts}[/] {msg}")
    if len(logs) > 50:
        logs.pop(0)


def on_batch(s):
    pass


def on_save(s):
    on_log(f"[green]💾 저장: {s.collected:,}건[/]")


def fmt_num(n):
    return f"{n:,}"


def fmt_time(sec):
    if sec < 60:
        return f"{int(sec)}초"
    if sec < 3600:
        return f"{int(sec//60)}분 {int(sec%60)}초"
    return f"{int(sec//3600)}시간 {int((sec%3600)//60)}분"


def build_display():
    total_scans = max((cfg.start_dbid - cfg.end_dbid) // cfg.step, 1)
    pct = min(stats.scanned / total_scans * 100, 100) if total_scans > 0 else 0
    eta = (total_scans - stats.scanned) / stats.speed if stats.speed > 0 else 0

    # ── 상단: 진행률 바 ──
    bar_width = 50
    filled = int(bar_width * pct / 100)
    bar = f"[orange1]{'━' * filled}[/][dim]{'─' * (bar_width - filled)}[/]"
    progress_text = f"{bar}  [bold]{pct:.3f}%[/]"

    # ── 통계 ──
    status_icon = "[bold green]● 실행중[/]" if stats.running and not stats.paused else \
                  "[bold yellow]● 일시정지[/]" if stats.paused else \
                  "[dim]● 대기[/]"

    stats_lines = [
        f"  상태      {status_icon}",
        f"  스캔      [bold]{fmt_num(stats.scanned)}[/]  [dim]({stats.speed:.1f}/초)[/]",
        f"  서울 수집  [bold orange1]{fmt_num(stats.collected)}[/]  [dim]({stats.seoul_speed:.1f}/초)[/]",
        f"  현재 dbId  [bold]{fmt_num(stats.current_dbid)}[/]",
        f"  에러율     {'[red]' if stats.err_rate > 0.1 else ''}{stats.err_rate:.0%}{'[/]' if stats.err_rate > 0.1 else ''}",
        f"  경과       {fmt_time(stats.elapsed)}",
        f"  남은 예상  {fmt_time(eta) if eta > 0 else '-'}",
    ]

    # ── 구별 분포 ──
    gu_sorted = sorted(stats.gu_counts.items(), key=lambda x: -x[1])[:12]
    if gu_sorted:
        max_cnt = gu_sorted[0][1] if gu_sorted else 1
        gu_lines = []
        for gu, cnt in gu_sorted:
            bar_len = int(cnt / max_cnt * 20)
            gu_lines.append(f"  {gu:<8} [orange1]{'█' * bar_len}[/] {cnt}")
        gu_text = "\n".join(gu_lines)
    else:
        gu_text = "  [dim]수집 시작 후 표시됩니다[/]"

    # ── 설정 요약 ──
    sample_pct = f"{1/cfg.step*100:.2f}%" if cfg.step > 1 else "전수"
    est_seoul = int(total_scans * 0.187)
    config_text = (
        f"  Workers    {cfg.workers}\n"
        f"  Batch      {cfg.batch_size}\n"
        f"  Pause      {cfg.batch_pause}초\n"
        f"  Step       {cfg.step}  [dim]({sample_pct})[/]\n"
        f"  예상 스캔  {fmt_num(total_scans)}\n"
        f"  예상 서울  ~{fmt_num(est_seoul)}\n"
        f"  예상 시간  ~{fmt_time(total_scans / 34000 * 3600)}\n"
        f"  CSV 파일   {cfg.output_csv}"
    )

    # ── 로그 ──
    log_text = "\n".join(logs[-12:]) if logs else "[dim]  대기 중...[/]"

    # ── 조합 ──
    output = Text.from_markup(
        f"\n [bold orange1]🥕 당근 동네생활 크롤러[/]  [dim]서울 전수 수집[/]\n"
        f" {'─' * 58}\n\n"
        f" {progress_text}\n\n"
    )

    # 패널들을 텍스트로 조합
    sections = (
        f"[bold dim]─── 진행 상황 ───[/]\n{chr(10).join(stats_lines)}\n\n"
        f"[bold dim]─── 구별 분포 ───[/]\n{gu_text}\n\n"
        f"[bold dim]─── 설정 ───[/]\n{config_text}\n\n"
        f"[bold dim]─── 로그 ───[/]\n{log_text}\n\n"
        f" [dim]Ctrl+C: 저장 후 종료 | P: 일시정지/재개[/]"
    )

    return Panel(
        Text.from_markup(
            f" [bold orange1]🥕 당근 동네생활 크롤러[/]  [dim]서울 전수 수집[/]\n"
            f" {'─' * 56}\n\n"
            f" {progress_text}\n\n"
            f"{sections}"
        ),
        border_style="dim",
        box=box.ROUNDED,
        padding=(1, 2),
    )


def crawl_thread():
    try:
        run_crawl(
            cfg, stats,
            on_batch=on_batch,
            on_save=on_save,
            on_log=on_log,
            stop_flag=lambda: stop_event.is_set(),
            pause_flag=lambda: pause_event.is_set(),
        )
    except Exception as e:
        on_log(f"[red]오류: {e}[/]")


def export_excel():
    """현재 CSV를 Excel로 변환"""
    if not os.path.exists(cfg.output_csv):
        return None
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "서울 동네생활"
        with open(cfg.output_csv, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            for row in reader:
                ws.append(row)
        xlsx_path = cfg.output_csv.replace(".csv", ".xlsx")
        wb.save(xlsx_path)
        return xlsx_path
    except ImportError:
        return None


def main():
    console.clear()
    console.print(f"\n [bold orange1]🥕 당근 동네생활 크롤러[/]\n")

    # ── 청크 선택 ─────────────────────────────────────────
    chunk_raw = args.chunk
    if chunk_raw is None and "--start" not in sys.argv and "--end" not in sys.argv:
        # 대화형 청크 선택
        try:
            s_dbid, e_dbid, label = select_chunk_interactive()
        except KeyboardInterrupt:
            return
        cfg.start_dbid = s_dbid
        cfg.end_dbid   = e_dbid
        if args.output == "daangn_seoul.csv":
            cfg.output_csv    = f"daangn_chunk_{label}.csv"
            cfg.progress_file = f"daangn_chunk_{label}_progress.json"
    elif chunk_raw is not None:
        # --chunk 인자로 직접 지정
        chunks = _all_chunks()
        try:
            c_from, c_to = _parse_chunk_range(chunk_raw, len(chunks))
        except ValueError:
            console.print(f"[red]--chunk 범위가 잘못되었습니다: {chunk_raw}[/]")
            return
        cfg.start_dbid = chunks[c_from - 1][1]
        cfg.end_dbid   = chunks[c_to   - 1][2]
        label = chunk_raw.replace(" ", "")
        if args.output == "daangn_seoul.csv":
            cfg.output_csv    = f"daangn_chunk_{label}.csv"
            cfg.progress_file = f"daangn_chunk_{label}_progress.json"
        console.print(
            f"  청크 {c_from}~{c_to}  "
            f"(dbId {cfg.end_dbid:,} ~ {cfg.start_dbid:,})\n"
        )
    # ──────────────────────────────────────────────────────

    # ── 출력 형식 선택 ────────────────────────────────────
    if args.format:
        output_format = args.format
    else:
        console.print("  출력 형식을 선택하세요.")
        console.print("  [bold]1[/]) CSV   [bold]2[/]) Excel")
        console.print()
        while True:
            fmt_input = input("  선택 (1/2): ").strip()
            if fmt_input == "1":
                output_format = "csv"
                break
            elif fmt_input == "2":
                output_format = "xlsx"
                break
            else:
                console.print("  [red]1 또는 2를 입력하세요.[/]")
        console.print()
    # ──────────────────────────────────────────────────────

    # reset (청크 확정 후)
    if args.reset:
        for f in [cfg.progress_file, cfg.output_csv]:
            if os.path.exists(f):
                os.remove(f)

    # 시작 안내
    total_scans = max((cfg.start_dbid - cfg.end_dbid) // cfg.step, 1)
    console.print(f"  Step={cfg.step} → 스캔 ~{fmt_num(total_scans)}건, 서울 ~{fmt_num(int(total_scans*0.187))}건")
    console.print(f"  예상 소요: ~{fmt_time(total_scans / 34000 * 3600)}\n")

    # 이전 진행 확인
    if os.path.exists(cfg.progress_file):
        import json
        with open(cfg.progress_file) as f:
            p = json.load(f)
        console.print(f"  [yellow]⚡ 이전 진행 발견: 스캔={p['scanned']:,}, 서울={p['collected']:,}[/]")
        console.print(f"     이어서 진행합니다. 새로 시작하려면 {cfg.progress_file} 삭제 후 재실행\n")

    console.print(f"  [dim]Enter로 시작, Ctrl+C로 종료[/]")
    try:
        input()
    except KeyboardInterrupt:
        return

    # 크롤링 스레드 시작
    t = threading.Thread(target=crawl_thread, daemon=True)
    t.start()

    # UI 루프
    try:
        with Live(build_display(), console=console, refresh_per_second=2) as live:
            while t.is_alive():
                live.update(build_display())
                time.sleep(0.5)
            live.update(build_display())
    except KeyboardInterrupt:
        on_log("[yellow]중단 요청, 저장 중...[/]")
        stop_event.set()
        t.join(timeout=10)

    # 완료 요약
    console.print(f"\n [bold]완료[/]: {fmt_num(stats.scanned)}건 스캔 → [orange1]{fmt_num(stats.collected)}건[/] 서울 수집")

    if stats.collected > 0:
        if output_format == "xlsx":
            xlsx = export_excel()
            if xlsx:
                os.remove(cfg.output_csv)
                console.print(f" 저장: {xlsx}")
            else:
                console.print(f" [red]Excel 변환 실패 (pip install openpyxl). CSV로 저장됨: {cfg.output_csv}[/]")
        else:
            console.print(f" 저장: {cfg.output_csv}")

    console.print()


if __name__ == "__main__":
    main()
