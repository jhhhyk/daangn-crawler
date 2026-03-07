"""
make_chunks.py - dbId 범위를 2000개 단위로 분할해 chunks.txt로 저장
"""

START  = 733_968_953
END    = 727_174_000
CHUNK  = 2_000
PEOPLE = 5

chunks = []
cur = START
idx = 1
while cur > END:
    chunk_end = max(cur - CHUNK + 1, END)
    chunks.append((idx, cur, chunk_end))
    cur = chunk_end - 1
    idx += 1

total = len(chunks)
per_person = total // PEOPLE
remainder  = total % PEOPLE

# 5명 분배 (앞사람이 1개 더)
assignments = []
start_i = 0
for person in range(1, PEOPLE + 1):
    count = per_person + (1 if person <= remainder else 0)
    end_i = start_i + count - 1
    assignments.append((person, start_i, end_i, chunks[start_i], chunks[end_i]))
    start_i = end_i + 1

with open("chunks.txt", "w", encoding="utf-8") as f:
    f.write(f"총 dbId 범위: {START:,} ~ {END:,}  |  청크 크기: {CHUNK:,}  |  총 청크 수: {total:,}\n")
    f.write("=" * 70 + "\n\n")

    f.write("[5명 분배 요약]\n")
    for person, si, ei, first, last in assignments:
        f.write(
            f"  {person}번: 청크 {first[0]:>4} ~ {last[0]:>4}  "
            f"({last[2]:,} ~ {first[1]:,})  "
            f"({ei - si + 1}개 청크 / dbId {first[1] - last[2] + 1:,}개)\n"
        )
    f.write("\n" + "=" * 70 + "\n\n")

    f.write(f"{'청크번호':>6}  {'start_dbid':>13}  {'end_dbid':>13}  {'담당자':>6}\n")
    f.write("-" * 50 + "\n")
    for person, si, ei, first, last in assignments:
        for i in range(si, ei + 1):
            cidx, cs, ce = chunks[i]
            f.write(f"{cidx:>6}  {cs:>13,}  {ce:>13,}  {person}번\n")

print(f"chunks.txt 저장 완료  (총 {total}개 청크, 1인당 약 {per_person}개)")
print()
print("[5명 분배 요약]")
for person, si, ei, first, last in assignments:
    print(
        f"  {person}번: 청크 {first[0]:>4} ~ {last[0]:>4}  "
        f"(start={first[1]:,} / end={last[2]:,})  "
        f"{ei - si + 1}개 청크"
    )
