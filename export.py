"""
export.py - CSV/Excel 반출 도구
================================
실행: python export.py

현재 수집된 daangn_seoul.csv를 Excel로 변환하거나,
구/동/카테고리별 필터링 후 별도 파일로 저장합니다.
"""

import csv
import os
import sys
from collections import Counter


CSV_PATH = "daangn_seoul.csv"


def load_rows():
    if not os.path.exists(CSV_PATH):
        print(f"❌ {CSV_PATH} 파일이 없습니다. 먼저 크롤링을 실행하세요.")
        sys.exit(1)
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    print(f"✅ {len(rows):,}건 로드 ({CSV_PATH})")
    return rows


def show_summary(rows):
    gus = Counter(r["gu"] for r in rows)
    subjects = Counter(r["subject"] for r in rows)
    statuses = Counter(r["status"] for r in rows)

    dates = [r["createdAt"][:10] for r in rows if r.get("createdAt")]
    date_range = f"{min(dates)} ~ {max(dates)}" if dates else "-"

    print(f"\n{'─'*50}")
    print(f"  총 {len(rows):,}건 | 기간: {date_range}")
    print(f"{'─'*50}")

    print(f"\n  구별 분포:")
    for gu, cnt in gus.most_common():
        print(f"    {gu:<10} {cnt:>6,}건")

    print(f"\n  카테고리:")
    for subj, cnt in subjects.most_common(10):
        print(f"    {subj:<14} {cnt:>6,}건")

    print(f"\n  상태: {dict(statuses)}")


def save_csv(rows, path):
    if not rows:
        print("  저장할 데이터가 없습니다.")
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(rows)
    print(f"  ✅ CSV 저장: {path} ({len(rows):,}건)")


def save_excel(rows, path):
    try:
        import openpyxl
    except ImportError:
        print("  ❌ openpyxl 필요: pip install openpyxl")
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "서울 동네생활"

    # 헤더
    headers = list(rows[0].keys())
    ws.append(headers)

    # 데이터
    for r in rows:
        ws.append([r.get(h, "") for h in headers])

    # 열 너비 자동 조정 (간이)
    for i, h in enumerate(headers, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = max(len(h) + 2, 12)

    wb.save(path)
    print(f"  ✅ Excel 저장: {path} ({len(rows):,}건)")


def filter_rows(rows):
    print(f"\n  필터링 (빈 입력 = 전체)")
    gu = input("  구 (예: 강남구): ").strip()
    dong = input("  동 (예: 역삼동): ").strip()
    subject = input("  카테고리 (예: 맛집): ").strip()

    filtered = rows
    if gu:
        filtered = [r for r in filtered if r.get("gu") == gu]
    if dong:
        filtered = [r for r in filtered if r.get("regionName") == dong]
    if subject:
        filtered = [r for r in filtered if r.get("subject") == subject]

    print(f"  → {len(filtered):,}건 필터됨")
    return filtered


def main():
    rows = load_rows()
    show_summary(rows)

    while True:
        print(f"\n{'─'*50}")
        print("  1) CSV 전체 내보내기")
        print("  2) Excel 전체 내보내기")
        print("  3) 필터링 후 CSV 내보내기")
        print("  4) 필터링 후 Excel 내보내기")
        print("  5) 요약 다시 보기")
        print("  q) 종료")
        print(f"{'─'*50}")

        choice = input("  선택: ").strip().lower()

        if choice == "1":
            path = input("  파일명 (기본: daangn_export.csv): ").strip() or "daangn_export.csv"
            save_csv(rows, path)
        elif choice == "2":
            path = input("  파일명 (기본: daangn_export.xlsx): ").strip() or "daangn_export.xlsx"
            save_excel(rows, path)
        elif choice == "3":
            filtered = filter_rows(rows)
            path = input("  파일명 (기본: daangn_filtered.csv): ").strip() or "daangn_filtered.csv"
            save_csv(filtered, path)
        elif choice == "4":
            filtered = filter_rows(rows)
            path = input("  파일명 (기본: daangn_filtered.xlsx): ").strip() or "daangn_filtered.xlsx"
            save_excel(filtered, path)
        elif choice == "5":
            show_summary(rows)
        elif choice == "q":
            break


if __name__ == "__main__":
    main()
