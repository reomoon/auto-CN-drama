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


def merge_fragments(segs, max_gap=0.35, max_chars=24, max_sec=5.0):
    """Whisper가 쉼표마다 자른 조각을 문장 단위로 합친다 (번역이 밀리거나 끊기는 것 방지).
    간격이 길거나 너무 길어지면 합치지 않는다 — 다른 사람의 말까지 한 줄로 합쳐지지 않게."""
    out = []
    for s in segs:
        if out:
            p = out[-1]
            if (s["start"] - p["end"] <= max_gap and len(p["text"]) + len(s["text"]) <= max_chars
                    and s["end"] - p["start"] <= max_sec
                    and not re.search(r"[。！？!?…]$", p["text"])):
                p["text"] = p["text"].rstrip("，,、 ") + "，" + s["text"]
                p["end"] = s["end"]
                continue
        out.append(dict(s))
    return out


def transcript(src):
    """문장 단위로 합친 대사 (음성 인식 캐시 사용)."""
    p = Path(src)
    tdir = config.WORK / "_transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    # _v2: 반복·지어낸 문장을 막는 설정으로 다시 인식한 결과
    return merge_fragments(media.transcribe(p, tdir / f"{slugify(p.stem)}_{p.stat().st_size}_v2.json"))


def load_lines(sources, windows=None):
    """windows: {소스 번호: (시작, 끝)} — 회차를 여러 편으로 나눌 때 그 편의 구간만."""
    lines = []
    for si, src in enumerate(sources):
        p = Path(src)
        segs = media.add_energy(transcript(p), p)
        st, en = media.story_range(p)        # 오프닝 곡·엔딩 크레딧 구간 대사는 빼기
        if windows and si in windows:
            st, en = max(st, windows[si][0]), min(en, windows[si][1])
        before = len(segs)
        segs = [s for s in segs if st <= s["start"] and s["end"] <= en]
        if before - len(segs):
            print(f"    이번 편 구간 {fmt(st)}~{fmt(en)} 대사 {len(segs)}개 사용 (오프닝·크레딧·다른 편 구간 제외)")
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


STORY_RULES = """### 이야기 구성 (가장 중요)
시청자는 이 드라마를 처음 본다. 끝까지 보면 "누가, 무슨 일을 당했고, 어떻게 되는지"가 한 줄기 이야기로 이해돼야 한다.
1. 먼저 대사 전체를 읽고 이 영상이 따라갈 **핵심 갈등 하나**를 정해 "story" 필드에 한 문장으로 적는다.
   (쇼츠 포인트가 주어졌으면 그것을 우선한다.) 그 갈등과 상관없는 곁가지 장면은 넣지 않는다.
2. 흐름: ①훅(가장 충격적인 사실 예고) → ②인물 관계와 상황 소개 → ③사건 전개 → ④반전·절정 → ⑤내 의견 + 질문
3. 첫 항목(훅)을 뺀 나머지 항목은 **원본 시간 순서대로** 배치한다.
4. 원본 항목 바로 앞의 나레이션은 그 장면을 이해하는 데 필요한 정보(누가 누구에게 왜)를 미리 알려준다.
   원본 장면이 나레이션 내용을 직접 보여주게 연결한다.
5. 나레이션 항목의 화면(from)은 나레이션이 말하는 내용과 같은 장면에서 고른다.
6. 원본 항목은 **짧고 센 장면 몰아보기**로 쓴다: 뺨·고성·오열·폭로·배신이 터지는 순간(⚡음량이 높은 대사)을 4~6개,
   하나에 2~5초씩. 사이를 잇는 나레이션은 1~2문장으로 짧게 해서 원본 장면이 자주, 빠르게 나오게 한다.
   예) 나레이션 "그런데 이 말을 들은 시어머니가" → (원본: 뺨 때리며 고함 3초) → 나레이션 "결국 참았던 게 터집니다" → (원본: 오열 4초)
"""

INTRO_RULES = """### 예고편 구성 (가장 중요)
이 영상은 1편보다 먼저 올리는 **예고편**이다. 시청자는 이 드라마를 처음 본다. 이 영상만 보고
"누가 나오고, 어떤 일이 벌어지는 드라마인지" 알게 되고, 1편부터 이어서 보고 싶어져야 한다.
아래 대사는 앞 회차 여러 개(s0=1화, s1=2화 ...)에서 가져왔다. 전부 따라가지 말고 **가장 재미있는 장면만** 골라 엮는다.
1. "story"에는 이 드라마 전체를 끌고 가는 핵심 갈등 한 문장(누가 누구에게 무엇을)을 적는다.
2. 흐름: ①훅(이 드라마에서 가장 충격적인 설정 한 방) → ②주요 인물 3~4명 소개 → ③큰 줄거리(갈등이 어디로 향하는지)
   → ④가장 자극적인 장면 몰아보기 → ⑤내 의견 + 질문 + "1편에서 처음부터 풀어드립니다" 같은 1편 유도
3. 인물 소개: 인물마다 나레이션 한 항목("이 여자가 ○○입니다. 겉으로는 ~, 사실은 ~")을 쓰고,
   그 화면(from)은 **그 인물이 말하는 대사**에서 고른다. 소개 직후 그 인물의 성격이 드러나는 원본 대사를 짧게 붙이면 좋다.
4. 몰아보기: 뺨·고성·오열·배신이 드러나는 순간 등 ⚡음량이 높은 원본 대사 2~4개를 짧게(2~5초) 이어 붙인다.
   각 원본 앞뒤에는 "그리고 이 사람이 무너집니다" 같은 짧은 연결 나레이션만 둔다.
5. 회차와 시간 순서는 지키지 않아도 된다. 재미있는 순서로 배치한다.
6. 결말과 반전의 답은 말하지 않는다. "과연 ~일까요?"처럼 궁금증만 남긴다. 대사에 없는 사건을 지어내지 않는다.
"""


PROMPT = """너는 중국 막장 드라마 숏폼(틱톡·네이버 클립) 해설 채널의 작가다.
드라마: {drama}
{info}드라마에 대해 아는 정보는 활용하되, 아래 대사와 어긋나면 대사를 따른다.

{style}

## 원본 대사 (음성 인식 결과라 오타가 있을 수 있음)
형식: 대사ID [s소스번호 시작-끝 ⚡음량(0~1, 높을수록 고성·싸움)] 중국어
{lines}

## 할 일
{min_s:.0f}~{max_s:.0f}초 분량의 세로 숏폼 한 편을 구성하라.
타임라인 항목 하나 = 원본 영상의 한 구간(from 대사 시작 ~ to 대사 끝).

{compose}
### 썸네일 (틱톡·클립은 첫 화면이 썸네일이 된다)
- "cover"에 이 회차에서 **누군가 깜짝 놀라는 얼굴**이 나올 장면의 대사ID를 고른다 (가장 우선).
  비밀·배신·정체가 폭로되는 대사, 충격적인 말을 들은 직후의 반응 대사가 좋다 (그 말을 들은 사람이 놀라는 순간).
  그런 장면이 없으면 뺨 때리기, 오열, 고성 대립 등 가장 자극적인 장면 (⚡음량이 높은 대사가 후보).
  첫 항목(훅)은 이 장면 위에 나레이션이 깔린다. 화면은 이 대사 앞뒤에서 놀란 표정이 가장 큰 프레임으로 자동으로 고른다.
- overlay_title(상단 고정 제목)은 썸네일 제목이다. 스크롤을 멈추게 하는 충격·분노·반전 키워드로 짧고 세게.
  예) ["남편과 가정부가 숨긴", "소름 돋는 진짜 관계"], ["남편의 두 집 살림", "아내가 다 알고 있었다"]
- title(업로드 제목)도 같은 세기로: 예) "남편과 가정부가 숨긴 소름 돋는 진짜 관계"
  밋밋한 요약("남편의 거짓말", "아내의 반격 시작")이 아니라, 무엇이 충격적인지 구체적으로 드러내되 결말은 숨긴다.

### 해설 채널로서의 기준 (공정 이용)
줄거리를 옮기는 영상이 아니라 **내 해석·비평·감상이 중심인 해설 영상**이어야 한다.
- 나레이션 항목마다 role을 붙인다: "설명"(상황·관계 소개) 또는 "해석"(인물 속마음 추측, 전개·연출 평가,
  현실이라면 어떨지, 시청자에게 던지는 질문 등 내 관점이 들어간 말).
- "해석" 나레이션은 {min_analysis}개 이상. 원본 장면 직전에 질문을 던지고 장면 뒤에 내 해석으로 답하는 방식이 좋다.
  예) "이 장면에서 아내는 왜 웃었을까요?" → (원본 장면) → "이미 증거를 다 쥐고 있었거든요."

### 형식
6. 항목 종류는 두 가지다.
   - 나레이션 항목: narration에 해설. 원본 소리는 작게 깔리고 나레이션만 들린다.
   - 원본 항목: narration은 빈 문자열. 원본 대사가 그대로 들린다. (한국어 번역은 다음 단계에서 따로 하니 쓰지 않는다)
7. 첫 항목은 반드시 나레이션 훅, 마지막 항목은 반드시 나레이션(의견 + 댓글 유도 질문).
8. 나레이션 항목의 길이 = 나레이션 글자수(공백 제외) ÷ {cps} + 0.5초. 화면은 from 지점부터 그 길이만큼 재생된다.
9. 전체 길이 {min_s:.0f}~{max_s:.0f}초. 원음이 그대로 나오는 원본 항목은 전체의 {orig_lo}~{orig_hi}%,
   나머지는 나레이션 항목(원음은 작게 깔림).
   예) 나레이션 7개 × 평균 6초 = 42초 + 원본 6개 × 평균 4초 = 24초 → 66초 (원본 36%)
   원본 항목은 가장 격한 대사 4~6개를 골라 짧게 쓴다.
10. 원본 항목 하나는 2~6초. 대사가 완결되는 지점에서 끊는다(문장 중간에서 자르지 않기).
11. from/to는 위 목록에 있는 대사ID만, 같은 소스(s번호) 안에서, from이 to보다 앞이어야 한다.

{series}{hot}JSON만 출력하라:
{{
 "story": "이 영상이 따라가는 핵심 갈등 한 문장",
 "cover": "썸네일로 쓸 가장 자극적인 장면의 대사ID (예: L240)",
 "recap": "이번 편까지의 전체 줄거리 2문장 (다음 편 작성용)",
 "cliff": "마지막에 남긴 다음 편 떡밥 한 문장",
 "title": "업로드 제목 (30자 이내, 궁금증 유발)",
 "overlay_title": ["영상 상단 고정 문구 1줄 (12자 이내)", "2줄 (12자 이내, 선택)"],
 "timeline": [
  {{"src": 0, "from": "L12", "to": "L15", "narration": "나레이션 문장", "role": "설명"}},
  {{"src": 0, "from": "L40", "to": "L42", "narration": ""}}
 ],
 "opinion": "이 영상에서 말한 내 의견 한 줄 요약",
 "caption": "업로드 본문 (2~3문장, 마지막에 질문)",
 "hashtags": ["#중드", "#작품명", "#이 영상에 가장 알맞은 태그 1개 (예: #복수극, #막장드라마)"]
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
        return tot > config.TARGET_MAX_SEC or (tot and nar / tot < config.NARR_RATIO_MIN)

    def too_little_orig(tot, nar):
        return tot > 0 and (tot - nar) / tot < orig_floor

    orig_floor = (1 - config.NARR_RATIO_MAX) - 0.03          # 원본 구간을 이 아래(약 27%)로는 줄이지 않는다
    tot, nar = stats()
    # 긴 원본 클립부터 하나씩, 기준을 맞출 때까지만 줄인다 (한꺼번에 다 줄이면 원본이 너무 적어져 다시 요청하게 됨)
    for it in sorted((x for x in tl if not _is_narr(x)), key=lambda x: -item_seconds(x, by_id)[0]):
        if not bad(tot, nar):
            break
        keep = (it["from"], it["to"], dict(it.get("subs") or {}))
        if not auto_trim(it, by_id, 4.5):
            continue
        t2, n2 = stats()
        if too_little_orig(t2, n2):                         # 줄이면 원본이 기준 아래로 → 되돌린다
            it["from"], it["to"] = keep[0], keep[1]
            if it.get("subs") is not None:
                it["subs"] = keep[2]
            continue
        notes.append(f"원본 클립 {it['from']}~{it['to']}로 축소")
        tot, nar = t2, n2
    while bad(tot, nar):
        cands = [it for it in tl[1:-1] if not _is_narr(it)]
        if not cands:
            break
        worst = min(cands, key=lambda it: _energy(it, by_id))
        sec = item_seconds(worst, by_id)[0]
        if tot - sec < config.TARGET_MIN_SEC:
            break
        if too_little_orig(tot - sec, nar):
            break                                             # 더 빼면 원본이 너무 적어짐 → 나레이션을 줄이게 한다
        tl.remove(worst)
        notes.append(f"원본 클립 {worst['from']}~{worst['to']} 제외")
        tot, nar = stats()
    return notes


def _too_few_lines(by_id):
    """대사가 아주 적은 구간(음악·무대사 장면이 많음)이면 원본 대사 비중 기준을 강제하지 않는다."""
    return len(by_id) < 40


def make_validator(by_id, ordered=True):
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
            auto_trim(it, by_id, limit=20.0 if narr else 8.0)
            sec, nsec = item_seconds(it, by_id)
            durs.append(f"{i}번 {sec:.1f}초{'(나레이션)' if narr else ''}")
            total += sec
            narr_total += nsec
        if not (tl[0].get("narration") or "").strip():
            errs.append("첫 항목은 나레이션 훅이어야 함")
        if p.get("cover") and p["cover"] not in by_id:
            errs.append(f"cover 대사ID({p['cover']})가 목록에 없음")
        if not (tl[-1].get("narration") or "").strip():
            errs.append("마지막 항목은 나레이션(의견)이어야 함")
        if not errs and ordered:
            keys = [(by_id[it["from"]]["src"], by_id[it["from"]]["start"]) for it in tl[1:]]
            back = [i + 2 for i in range(1, len(keys))
                    if keys[i][0] < keys[i - 1][0] or (keys[i][0] == keys[i - 1][0] and keys[i][1] < keys[i - 1][1] - 1)]
            if back:
                errs.append(f"{', '.join(map(str, back))}번 항목이 앞 항목보다 이른 장면임 → 훅을 뺀 항목은 원본 시간 순서대로")
        if not errs:
            notes = auto_fit(tl, by_id)
            if notes:
                print("  · 길이 자동 조정: " + ", ".join(notes))
            s = [item_seconds(it, by_id) for it in tl]
            total, narr_total = sum(x[0] for x in s), sum(x[1] for x in s)
            durs = [f"{i}번 {x[0]:.1f}초{'(나레이션)' if x[1] else ''}" for i, x in enumerate(s, 1)]
            if total > config.MAX_SEC - 1:
                over = total - config.TARGET_MAX_SEC
                errs.append(f"예상 길이 {total:.1f}초로 {over:.0f}초 깁니다 → 원본 항목은 그대로 두고 나레이션을 줄일 것 "
                            f"(나레이션 항목을 1~2개 빼거나 문장을 짧게, 나레이션은 초당 {NARR_CPS}자로 계산. "
                            f"현재 항목별: {', '.join(durs)})")
            elif total < config.TARGET_MIN_SEC - 2:
                errs.append(f"예상 길이 {total:.1f}초 → {config.TARGET_MIN_SEC:.0f}~{config.TARGET_MAX_SEC:.0f}초로 늘릴 것 "
                            f"(현재 항목별: {', '.join(durs)})")
            if total and narr_total / total < config.NARR_RATIO_MIN - 0.05:
                errs.append(f"원본 항목 비중 {1 - narr_total / total:.0%} → {1 - config.NARR_RATIO_MIN:.0%} 이하가 되게 "
                            f"원본 항목을 줄이거나 나레이션 항목을 늘릴 것")
            if total and narr_total / total > config.NARR_RATIO_MAX + 0.05 and not _too_few_lines(by_id):
                # 원본이 적은 건 저작권 위험이 아니라 스타일 문제라 다시 요청하지 않고(한 번에 수 분) 알리기만 한다
                print(f"  ! 원본 항목 비중 {1 - narr_total / total:.0%} (목표 {1 - config.NARR_RATIO_MAX:.0%} 이상) "
                      f"— 그대로 진행합니다. 원본 장면을 늘리려면 대시보드에서 대본을 고치세요.")
            n_an = sum(1 for it in tl if (it.get("narration") or "").strip() and it.get("role") == "해석")
            if n_an < config.MIN_ANALYSIS:
                errs.append(f"'해석' 나레이션이 {n_an}개 → {config.MIN_ANALYSIS}개 이상 필요 "
                            f"(인물 속마음 추측, 전개 평가, 질문 등 내 관점)")
        p["_est_total"], p["_est_narr"] = round(total, 1), round(narr_total, 1)
        return errs
    return validate


TRANSLATE_PROMPT = """중국 드라마 대사를 한국어 자막으로 번역하라.
드라마: {drama}
{info}이 영상의 줄거리: {story}

아래는 음성 인식으로 받아쓴 대사다(오타가 있을 수 있으니 문맥으로 바로잡아라).
'▶' 표시가 붙은 대사만 번역한다. 표시 없는 대사는 앞뒤 문맥 참고용이다.

{lines}

규칙:
- 대사ID 하나에 그 대사의 번역 하나. 다른 대사의 내용을 끌어오거나 순서를 섞지 않는다.
- 자연스러운 한국어 구어체. 가능하면 18자 이내로 짧게 의역한다.
- 인물 관계에 맞는 말투(부부·친구는 반말, 윗사람에게는 존댓말).
- 인물 이름은 한국식 한자음 대신 관계나 호칭으로 바꿔도 된다(예: 老公 → 여보).

JSON 객체만 출력: {{"L12": "번역", "L13": "번역"}}"""


def translate(plan, by_id, engine, drama, info):
    """원본 항목 대사만 앞뒤 문맥과 함께 따로 번역한다 (대사 하나 = 번역 하나)."""
    want = []
    for it in plan["timeline"]:
        if not (it.get("narration") or "").strip():
            want += [f"L{n}" for n in range(int(it["from"][1:]), int(it["to"][1:]) + 1) if f"L{n}" in by_id]
    if not want:
        return
    ctx = set()
    for lid in want:
        n = int(lid[1:])
        ctx |= {f"L{k}" for k in range(n - 3, n + 4) if f"L{k}" in by_id}
    order = sorted(ctx, key=lambda x: int(x[1:]))
    text = "\n".join(f"{'▶ ' if x in want else '  '}{x} {by_id[x]['text']}" for x in order)
    prompt = TRANSLATE_PROMPT.format(drama=drama, info=info_text(info), story=plan.get("story", ""), lines=text)

    def validate(d):
        if not isinstance(d, dict):
            return ["JSON 객체여야 함"]
        miss = [x for x in want if not str(d.get(x) or "").strip()]
        return [f"번역 빠짐: {', '.join(miss)}"] if miss else []

    print(f"  · 대사 {len(want)}개 번역 (문맥 포함)")
    subs, _ = llm.generate_json(prompt, engine=engine, validate=validate, label="대사 번역 중")
    for it in plan["timeline"]:
        if not (it.get("narration") or "").strip():
            ids = [f"L{n}" for n in range(int(it["from"][1:]), int(it["to"][1:]) + 1)]
            it["subs"] = {x: str(subs[x]).strip() for x in ids if x in subs}
        else:
            it["subs"] = {}


TITLE_PROMPT = """너는 조회수가 잘 나오는 중국 드라마 해설 쇼츠의 썸네일 제목 담당이다.
드라마: {drama}
{info}이번 편 줄거리: {story}
이번 편 나레이션:
{narr}
{hot}
썸네일 상단 2줄 제목 후보 5개를 만들어라. 스크롤하던 손가락을 멈추게 해야 한다.
- 1줄: 누가/무엇이 (구체적 대상: 남편, 보모, 시어머니, 절친, 서녀 등), 2줄: 충격 포인트 (정체·배신·반전·두 얼굴)
- 각 줄 12자 이내, 감정을 건드리는 단어를 쓴다: 소름, 진짜 정체, 두 얼굴, 배신, 미쳤다, 경악, 몰래, 결국 등
- 결말은 숨기고 궁금하게. 과장은 해도 되지만 이번 편에 실제로 나오는 사실만 쓴다 (거짓 낚시 금지)
- 밋밋한 요약(예: "5주년 꽃다발 속 / 남편의 호텔 사진")은 안 된다
  좋은 예) ["남편과 가정부가 숨긴", "소름 돋는 진짜 관계"], ["5년 결혼이 전부 사기", "남편의 두 얼굴"]
- title(업로드 제목)도 같은 세기로 30자 이내

JSON만 출력: {{"options": [{{"lines": ["1줄", "2줄"], "title": "업로드 제목"}}, ... 5개, 가장 자극적인 것부터]}}"""


def title_options(plan, drama, info=None, hot="", engine="claude"):
    """썸네일 상단 제목 후보 5개 (가장 자극적인 것부터)."""
    narr = "\n".join(f"- {it['narration']}" for it in plan.get("timeline", []) if (it.get("narration") or "").strip())
    prompt = TITLE_PROMPT.format(drama=drama, info=info_text(info), story=plan.get("story", ""), narr=narr,
                                 hot=hot or "")

    def validate(d):
        opts = (d or {}).get("options") if isinstance(d, dict) else None
        if not opts:
            return ["options 가 없음"]
        bad = [i for i, o in enumerate(opts, 1) if not (isinstance(o.get("lines"), list) and o["lines"])]
        return [f"{', '.join(map(str, bad))}번 후보에 lines 가 없음"] if bad else []

    d, _ = llm.generate_json(prompt, engine=engine, validate=validate, retries=1, label="썸네일 제목 후보 뽑는 중")
    out = []
    for o in d["options"][:5]:
        lines = [str(x).strip() for x in o["lines"] if str(x).strip()][:2]
        out.append({"lines": lines, "title": str(o.get("title") or " ".join(lines)).strip()})
    return out


def write_review(plan, by_id, path):
    L = [f"# {plan['drama']} — 검토용", "",
         f"- 제목: **{plan.get('title', '')}**",
         f"- 상단 문구: {' / '.join(plan.get('overlay_title') or [])}",
         f"- 대본 작성: {plan['engine']}  |  예상 길이 {plan.get('_est_total')}초 (나레이션 {plan.get('_est_narr')}초)",
         f"- 줄거리: {plan.get('story', '')}",
         f"- 공정 이용 점검: 해설 비중 {plan.get('_est_narr', 0) / max(1, plan.get('_est_total') or 1):.0%} · "
         f"'해석' 나레이션 {sum(1 for it in plan['timeline'] if it.get('role') == '해석')}개 · "
         f"원작 사용 {plan.get('_source_ratio', 0):.1%} (원본 {plan.get('_source_total', 0) / 60:.0f}분 중)",
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


def series_text(series):
    if not series:
        return ""
    n = series["part"]
    if n == 0:
        eps = ", ".join(f"{e}화" for e in series.get("eps") or [])
        return "\n".join([
            "### 시리즈 예고편 (중요)",
            f"이 영상은 1편 앞에 올리는 '예고편'이다. 아래 대사는 {eps}에서 가져왔다.",
            "- 마지막 나레이션: 내 의견 + 질문 다음에, 1편을 보러 가게 만드는 한 마디로 끝낸다.",
            "  예) \"이 집의 비밀, 1편에서 처음부터 풀어드립니다.\"",
            "- \"cliff\"에는 1편으로 넘기는 떡밥 한 문장을 적는다.",
            "- title은 \"[{드라마명} 예고편]\"으로 시작한다.", ""]) + "\n"
    s = [f"### 시리즈 {n}편 (중요)",
         f"이 영상은 '{n}편'이다. 시청자가 다음 편을 기다리게 만드는 연재물로 쓴다."]
    if series.get("split", 1) > 1:
        half = ["전반", "후반"][series["piece"]] if series["split"] == 2 else f"{series['piece'] + 1}번째 부분"
        s.append(f"이번 편은 {series['eps'][0]}화의 {half}만 다룬다 (아래 대사가 그 구간이다). "
                 f"한 회차를 여러 편으로 나눴으니 장면 하나하나를 서두르지 말고 충분히 설명한다.")
    if series.get("prev"):
        s.append("지난 편까지의 내용:")
        s += [f"- {p['part']}편: {p.get('recap') or p.get('story')}"
              + (f" (예고: {p['cliff']})" if p.get("cliff") else "") for p in series["prev"]]
        s += ["- 첫 나레이션(훅)은 \"지난 이야기\"를 한 문장으로 짚고, 바로 이번 편의 충격적인 사실로 넘어간다.",
              "  예) \"지난 편, 아내는 남편의 거짓말을 처음 알아챘습니다. 그런데 이번엔 더 큰 비밀이 드러납니다.\"",
              "- 지난 편에서 이미 설명한 사실과 장면은 반복하지 않는다. 지난 편 예고가 있으면 이번 편에서 그 떡밥을 풀어준다."]
    else:
        s.append("- 1편이므로 인물 관계와 기본 설정을 처음 보는 사람도 알 수 있게 소개한다.")
    s += ["- 마지막 나레이션: 내 의견 + 질문 다음에, 다음 편이 궁금해지는 떡밥을 던지고 \"다음 편에서 계속됩니다\"로 끝낸다.",
          f"- title은 \"[{{드라마명}} {n}편]\"으로 시작한다.", ""]
    return "\n".join(s) + "\n"


def normalize_tags(tags, drama, n=None):
    """해시태그는 n개(기본 3)만: #중드 #작품명 + 대본이 고른 가장 알맞은 태그 하나."""
    n = n or config.MAX_HASHTAGS
    base = ["#중드", "#" + drama.replace(" ", "")]
    rest = [t if t.startswith("#") else "#" + t for t in (tags or [])]
    rest = [t.replace(" ", "") for t in rest if t.replace(" ", "") not in base
            and not re.fullmatch(r"#(중국드라마|중드|숏폼드라마|숏폼|쇼츠|드라마해설)", t.replace(" ", ""))
            and not re.search(r"\d+편$", t)
            and not (re.search(r"[一-鿿]", t) and not re.search(r"[가-힣]", t))]   # 중국어 원제 태그 제외
    return list(dict.fromkeys(base + rest))[:n]


def info_text(info):
    if not info:
        return ""
    rows = [("원제", info.get("title_zh")), ("플랫폼", info.get("platform")), ("장르", info.get("genre")),
            ("쇼츠 포인트", info.get("hook"))]
    return "".join(f"{k}: {v}\n" for k, v in rows if v)


def run(drama, videos, engine="auto", name=None, tts_engine=None, info=None, series=None, windows=None,
        hot=""):
    videos = [str(Path(v).resolve()) for v in videos]
    for v in videos:
        if not Path(v).exists():
            raise FileNotFoundError(v)
    wdir = config.WORK / slugify(name or drama)
    wdir.mkdir(parents=True, exist_ok=True)

    src_total = sum((windows[i][1] - windows[i][0]) if windows and i in windows else media.duration(v)
                    for i, v in enumerate(videos))
    allowed = src_total * config.MAX_SOURCE_RATIO
    if allowed < config.TARGET_MIN_SEC:
        need = config.TARGET_MIN_SEC / config.MAX_SOURCE_RATIO / 60
        raise RuntimeError(f"원본이 {src_total / 60:.1f}분뿐이라 원작 사용 {config.MAX_SOURCE_RATIO:.0%} 기준"
                           f"({allowed:.0f}초)으로는 1분 영상을 만들 수 없습니다. 회차를 더 넣어주세요 "
                           f"(합계 {need:.0f}분 이상).")

    print(f"[1/3] 대사 추출 ({len(videos)}개 영상)")
    lines = load_lines(videos, windows)
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
    intro = bool(series and series.get("part") == 0)
    prompt = PROMPT.format(compose=INTRO_RULES if intro else STORY_RULES, drama=drama, info=info_text(info), style=style, lines=text, cps=NARR_CPS,
                           series=series_text(series), hot=hot or "",
                           min_s=config.TARGET_MIN_SEC, max_s=config.TARGET_MAX_SEC,
                           orig_lo=round((1 - config.NARR_RATIO_MAX) * 100),
                           orig_hi=round((1 - config.NARR_RATIO_MIN) * 100), min_analysis=config.MIN_ANALYSIS)
    plan, used = llm.generate_json(prompt, engine=eng, validate=make_validator(by_id, ordered=not intro),
                                   label="하이라이트 고르고 대본 쓰는 중")
    print(f"  · 줄거리: {plan.get('story', '')}")
    translate(plan, by_id, used, drama, info)
    try:
        opts = title_options(plan, drama, info, hot, engine=used)
        if opts:
            plan["title_options"] = opts
            plan["overlay_title"] = opts[0]["lines"]          # 가장 자극적인 후보를 기본으로
            if opts[0].get("title"):
                plan["title"] = re.sub(r"^\[[^\]]*\]\s*", "", opts[0]["title"])   # 편 번호는 아래에서 붙인다
            print(f"  · 썸네일 제목: {' / '.join(opts[0]['lines'])} (후보 {len(opts)}개)")
    except Exception as e:  # noqa: BLE001
        print(f"  ! 제목 후보 뽑기 실패 (대본 제목 사용): {e}")
    if series:
        plan["series"] = {k: series[k] for k in ("base", "part", "eps", "piece", "split") if k in series}
        tag = f"[{drama} {'예고편' if intro else str(series['part']) + '편'}]"
        if not plan.get("title", "").startswith("["):
            plan["title"] = f"{tag} {plan.get('title', '')}".strip()
    plan["hashtags"] = normalize_tags(plan.get("hashtags"), drama)
    plan.update({"drama": drama, "engine": used, "sources": videos, "_source_total": round(src_total, 1),
                 "_source_ratio": round(plan["_est_total"] / src_total, 4),
                 "credits": [{"ep": o["ep"], "channel": o["channel"], "url": o["url"]}
                             for o in (info or {}).get("official", [])]})

    print("[3/3] 저장")
    (wdir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    write_review(plan, by_id, wdir / "plan.md")
    print(f"\n완료 → {wdir / 'plan.md'}  (예상 {plan['_est_total']}초, 작성: {used})")
    return wdir
