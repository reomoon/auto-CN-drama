"""대사 분석 → 하이라이트 구간 + 나레이션 대본(plan.json) + 확인용 plan.md."""
import json
import re
from pathlib import Path

from . import config, llm, media, tts

NARR_CPS = 5.0          # 한국어 TTS 초당 글자 수(공백 제외). 렌더할 때마다 실측값으로 자동 보정
MAX_LINES = 1800        # 프롬프트에 넣을 최대 대사 수


def slugify(s):
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s).strip("_")[:60] or "drama"


def fmt(t):
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


def narr_sec(text):
    return len(re.sub(r"\s", "", text or "")) / NARR_CPS


def load_lines(sources):
    tdir = config.WORK / "_transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    lines = []
    for si, src in enumerate(sources):
        p = Path(src)
        cache = tdir / f"{slugify(p.stem)}_{p.stat().st_size}.json"
        segs = media.add_energy([dict(s) for s in media.transcribe(p, cache)], p)
        for s in segs:
            lines.append({"id": f"L{len(lines) + 1}", "src": si, **s})
    return lines


def prefilter(lines, budget=MAX_LINES):
    """대사가 너무 많으면 시끄럽고 대사가 몰린 30초 구간 위주로 남긴다."""
    if len(lines) <= budget:
        return lines
    wins = {}
    for ln in lines:
        wins.setdefault((ln["src"], int(ln["start"] // 30)), []).append(ln)
    ranked = sorted(wins.values(), key=lambda w: -(sum(x["energy"] for x in w) / len(w) + 0.02 * len(w)))
    keep = []
    for w in ranked:
        if len(keep) + len(w) <= budget:
            keep += w
    print(f"  · 대사 {len(lines)}개 중 핵심 구간 {len(keep)}개만 분석에 사용")
    return sorted(keep, key=lambda x: (x["src"], x["start"]))


def item_seconds(item, by_id):
    """(항목 길이, 나레이션 길이). 나레이션 항목은 나레이션 길이만큼만 화면을 쓴다."""
    n = (item.get("narration") or "").strip()
    if n:
        return narr_sec(n) + 0.5, narr_sec(n) + 0.5
    a, b = by_id[item["from"]], by_id[item["to"]]
    return b["end"] - a["start"] + 0.4, 0.0


PROMPT = """너는 중국 막장 드라마 숏폼(틱톡·네이버 클립) 해설 채널의 작가다.
드라마: {drama}
드라마에 대해 아는 정보는 활용하되, 아래 대사와 어긋나면 대사를 따른다.

{style}

## 원본 대사 (음성 인식 결과라 오타가 있을 수 있음)
형식: 대사ID [s소스번호 시작-끝 ⚡음량(0~1, 높을수록 고성·싸움)] 중국어
{lines}

## 할 일
위 대사에서 가장 막장스럽고 몰입되는 장면들을 골라 {min_s:.0f}~{max_s:.0f}초 분량의 세로 숏폼 한 편을 구성하라.
타임라인 항목 하나 = 원본 영상의 한 구간(from 대사 시작 ~ to 대사 끝).

규칙:
1. 항목 종류는 두 가지다.
   - 나레이션 항목: narration에 해설을 쓴다. 원본 소리는 작게 깔리고 나레이션만 들린다. subs는 빈 객체.
   - 원본 항목: narration은 빈 문자열. 원본 대사가 그대로 들리므로 subs에 그 구간 모든 대사ID의 한국어 번역을 넣는다.
2. 첫 항목은 반드시 나레이션 훅, 마지막 항목은 반드시 나레이션(내 의견 + 댓글 유도 질문).
3. 나레이션 항목의 길이 = 나레이션 글자수(공백 제외) ÷ {cps} + 0.5초. 화면은 from 지점부터 그 길이만큼 재생된다.
   → from은 나레이션 내용과 어울리는 장면의 시작점으로 고른다.
4. 전체 길이 {min_s:.0f}~{max_s:.0f}초, 그중 나레이션 항목이 40% 이상이어야 한다.
   예) 나레이션 6개 × 평균 7초 = 42초 + 원본 5개 × 평균 7초 = 35초 → 77초
5. 원본 항목 하나는 2~10초, 대사가 가장 강렬한 순간만 짧게 끊는다.
6. from/to는 위 목록에 있는 대사ID만, 같은 소스(s번호) 안에서, from이 to보다 앞이어야 한다.
7. 번역은 자연스러운 한국어 구어체로, 한 줄 20자 이내로 짧게 의역한다.

JSON만 출력하라:
{{
 "title": "업로드 제목 (30자 이내, 궁금증 유발)",
 "overlay_title": ["영상 상단 고정 문구 1줄 (12자 이내)", "2줄 (12자 이내, 선택)"],
 "timeline": [
  {{"src": 0, "from": "L12", "to": "L15", "narration": "나레이션 문장", "subs": {{}}}},
  {{"src": 0, "from": "L40", "to": "L42", "narration": "", "subs": {{"L40": "번역", "L41": "번역", "L42": "번역"}}}}
 ],
 "opinion": "이 영상에서 말한 내 의견 한 줄 요약",
 "caption": "업로드 본문 (2~3문장, 마지막에 질문)",
 "hashtags": ["#중드", "#중국드라마", "...총 6~10개"]
}}"""


def auto_trim(it, by_id, limit):
    """구간이 limit초보다 길면, 그 안에서 음량(격한 정도)이 가장 높은 연속 대사 구간만 남긴다."""
    a, b = by_id[it["from"]], by_id[it["to"]]
    if b["end"] - a["start"] <= limit:
        return False
    ids = [f"L{n}" for n in range(int(it["from"][1:]), int(it["to"][1:]) + 1) if f"L{n}" in by_id]
    best = None
    for i in range(len(ids)):
        for j in range(i, len(ids)):
            if by_id[ids[j]]["end"] - by_id[ids[i]]["start"] > limit:
                break
            score = sum(by_id[x]["energy"] for x in ids[i:j + 1]) + 0.3 * (j - i)
            if best is None or score > best[0]:
                best = (score, i, j)
    if not best:
        return False
    keep = ids[best[1]:best[2] + 1]
    it["from"], it["to"] = keep[0], keep[-1]
    if it.get("subs"):
        it["subs"] = {k: v for k, v in it["subs"].items() if k in keep}
    return True


def _is_narr(it):
    return bool((it.get("narration") or "").strip())


def _energy(it, by_id):
    ids = [f"L{n}" for n in range(int(it["from"][1:]), int(it["to"][1:]) + 1) if f"L{n}" in by_id]
    return sum(by_id[x]["energy"] for x in ids) / max(1, len(ids))


def auto_fit(tl, by_id):
    """길이·나레이션 비중을 코드로 맞춘다: 긴 원본 클립 줄이기 → 덜 격한 원본 클립 빼기."""
    notes = []

    def stats():
        s = [item_seconds(it, by_id) for it in tl]
        return sum(x[0] for x in s), sum(x[1] for x in s)

    def bad(tot, nar):
        return tot > config.TARGET_MAX_SEC or (tot and nar / tot < 0.4)

    tot, nar = stats()
    if bad(tot, nar):
        for it in tl:
            if not _is_narr(it) and auto_trim(it, by_id, 7.0):
                notes.append(f"원본 클립 {it['from']}~{it['to']}로 축소")
        tot, nar = stats()
    while bad(tot, nar):
        cands = [it for it in tl[1:-1] if not _is_narr(it)]
        if not cands:
            break
        worst = min(cands, key=lambda it: _energy(it, by_id))
        if tot - item_seconds(worst, by_id)[0] < config.TARGET_MIN_SEC:
            break
        tl.remove(worst)
        notes.append(f"원본 클립 {worst['from']}~{worst['to']} 제외")
        tot, nar = stats()
    return notes


def make_validator(by_id):
    def validate(p):
        errs = []
        for k in ("title", "timeline", "caption", "hashtags"):
            if k not in p:
                errs.append(f"'{k}' 키 없음")
        tl = p.get("timeline") or []
        if not tl:
            return errs + ["timeline 비어 있음"]
        total = narr_total = 0.0
        durs = []
        for i, it in enumerate(tl, 1):
            f, t = it.get("from"), it.get("to")
            if f not in by_id or t not in by_id:
                errs.append(f"{i}번 항목: 없는 대사ID ({f}, {t})")
                continue
            a, b = by_id[f], by_id[t]
            if a["src"] != b["src"] or a["start"] > b["start"]:
                errs.append(f"{i}번 항목: from/to 순서나 소스가 잘못됨 ({f}~{t})")
                continue
            it["src"] = a["src"]
            narr = (it.get("narration") or "").strip()
            auto_trim(it, by_id, limit=20.0 if narr else 10.0)
            if not narr and not it.get("subs"):
                errs.append(f"{i}번 원본 항목에 subs(번역) 없음")
            sec, nsec = item_seconds(it, by_id)
            durs.append(f"{i}번 {sec:.1f}초{'(나레이션)' if narr else ''}")
            total += sec
            narr_total += nsec
        if not (tl[0].get("narration") or "").strip():
            errs.append("첫 항목은 나레이션 훅이어야 함")
        if not (tl[-1].get("narration") or "").strip():
            errs.append("마지막 항목은 나레이션(의견)이어야 함")
        if not errs:
            notes = auto_fit(tl, by_id)
            if notes:
                print("  · 길이 자동 조정: " + ", ".join(notes))
            s = [item_seconds(it, by_id) for it in tl]
            total, narr_total = sum(x[0] for x in s), sum(x[1] for x in s)
            durs = [f"{i}번 {x[0]:.1f}초{'(나레이션)' if x[1] else ''}" for i, x in enumerate(s, 1)]
            if not (config.TARGET_MIN_SEC - 2 <= total <= config.MAX_SEC - 1):
                errs.append(f"예상 길이 {total:.1f}초 → {config.TARGET_MIN_SEC:.0f}~{config.TARGET_MAX_SEC:.0f}초로 맞출 것 "
                            f"(나레이션은 초당 {NARR_CPS}자로 계산. 현재 항목별: {', '.join(durs)})")
            if total and narr_total / total < 0.35:
                errs.append(f"나레이션 비중 {narr_total / total:.0%} → 나레이션 항목을 늘리거나 길게 쓸 것")
        p["_est_total"], p["_est_narr"] = round(total, 1), round(narr_total, 1)
        return errs
    return validate


def write_review(plan, by_id, path):
    L = [f"# {plan['drama']} — 검토용", "",
         f"- 제목: **{plan.get('title', '')}**",
         f"- 상단 문구: {' / '.join(plan.get('overlay_title') or [])}",
         f"- 대본 작성: {plan['engine']}  |  예상 길이 {plan.get('_est_total')}초 (나레이션 {plan.get('_est_narr')}초)",
         f"- 의견: {plan.get('opinion', '')}", "", "## 타임라인", ""]
    t = 0.0
    for i, it in enumerate(plan["timeline"], 1):
        a, b = by_id[it["from"]], by_id[it["to"]]
        sec, _ = item_seconds(it, by_id)
        narrated = bool((it.get("narration") or "").strip())
        kind = "🎙 나레이션" if narrated else "🎬 원본"
        span = f"{fmt(a['start'])}부터" if narrated else f"{fmt(a['start'])}~{fmt(b['end'])}"
        L.append(f"### {i}. {kind}  (완성본 {fmt(t)}~{fmt(t + sec)}, 원본 s{a['src']} {span})")
        if kind.startswith("🎙"):
            L.append(f"> {it['narration']}")
        n_from, n_to = int(it["from"][1:]), int(it["to"][1:])
        for n in range(n_from, n_to + 1):
            ln = by_id.get(f"L{n}")
            if ln:
                ko = (it.get("subs") or {}).get(ln["id"], "")
                L.append(f"- `{ln['id']}` {ln['text']}" + (f" → **{ko}**" if ko else ""))
        L.append("")
        t += sec
    L += ["## 업로드 문구", "", plan.get("caption", ""), "", " ".join(plan.get("hashtags") or []), "",
          "---", "수정하려면 같은 폴더의 `plan.json`을 고친 뒤 `render`를 다시 실행하세요."]
    Path(path).write_text("\n".join(L), encoding="utf-8")


def run(drama, videos, engine="auto", name=None, tts_engine=None):
    videos = [str(Path(v).resolve()) for v in videos]
    for v in videos:
        if not Path(v).exists():
            raise FileNotFoundError(v)
    wdir = config.WORK / slugify(name or drama)
    wdir.mkdir(parents=True, exist_ok=True)

    print(f"[1/3] 대사 추출 ({len(videos)}개 영상)")
    lines = load_lines(videos)
    if not lines:
        raise RuntimeError("대사를 하나도 인식하지 못했습니다. 영상에 음성이 있는지 확인하세요.")
    by_id = {ln["id"]: ln for ln in lines}
    (wdir / "lines.json").write_text(json.dumps(lines, ensure_ascii=False, indent=1), encoding="utf-8")

    global NARR_CPS
    NARR_CPS = tts.chars_per_sec(tts_engine)
    eng = llm.next_engine() if engine == "auto" else engine
    print(f"[2/3] 하이라이트 선정 + 대본 작성 ({eng})")
    shown = prefilter(lines)
    text = "\n".join(f"{x['id']} [s{x['src']} {fmt(x['start'])}-{fmt(x['end'])} ⚡{x['energy']}] {x['text']}"
                     for x in shown)
    style = (config.PROMPTS / "style.txt").read_text(encoding="utf-8")
    prompt = PROMPT.format(drama=drama, style=style, lines=text, cps=NARR_CPS,
                           min_s=config.TARGET_MIN_SEC, max_s=config.TARGET_MAX_SEC)
    plan, used = llm.generate_json(prompt, engine=eng, validate=make_validator(by_id))
    plan.update({"drama": drama, "engine": used, "sources": videos})

    print("[3/3] 저장")
    (wdir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    write_review(plan, by_id, wdir / "plan.md")
    print(f"\n완료 → {wdir / 'plan.md'}  (예상 {plan['_est_total']}초, 작성: {used})")
    return wdir
