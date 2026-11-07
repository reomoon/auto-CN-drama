"""다른 사람들이 올려서 반응이 좋았던 장면을 조사한다 (대본 작성 참고용).

- 유튜브: 그 드라마의 명장면·쇼츠·클립을 검색해 조회수 순으로 (공식 채널 하이라이트 포함)
- Claude 웹 검색: 웨이보·더우인·더우반에서 화제가 된 장면(名场面·热搜)

영상은 받지 않는다 — 어떤 장면이 떴는지만 참고하고, 화면은 공식 원본에서 직접 자른다.
결과는 input/<작품>/research.json 에 저장하고 3일마다 새로 조사한다.
"""
import datetime as dt
import json
import re

from . import config, llm
from .batch import INPUT

REFRESH_DAYS = 3

HOT_PROMPT = """웹 검색으로 중국 드라마 「{zh}」(한국어 제목: {ko})에서 시청자 반응이 가장 뜨거웠던 장면을 조사하라.
참고할 곳: 웨이보 열검색(热搜)·화제 해시태그, 더우인 인기 클립, 더우반 리뷰, 바이두, 국내 커뮤니티·쇼츠.
'名场面', '高能', '爽点', '热搜' 같은 키워드로 찾는다. 결말 스포일러는 회차만 적고 자세히 쓰지 않는다.

JSON 배열만 출력 (많이 화제가 된 순, 최대 10개):
[{{"scene": "어떤 장면인지 한국어 한 줄 (누가 누구에게 무엇을)", "episode": "회차(모르면 빈 문자열)",
  "why": "왜 반응이 좋았는지 한 줄 (사이다·소름·반전·명대사 등)", "keywords_zh": ["장면을 찾을 중국어 키워드", "..."],
  "source": "근거 URL 1개"}}]"""


def _youtube(zh, ko):
    import yt_dlp

    from .fetch import _norm, is_official
    queries = [f"{zh} 名场面", f"{zh} 片段", f"{zh} shorts", f"{ko} 쇼츠", f"{ko} 중드 명장면"]
    seen, rows = set(), []
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as y:
        for q in queries:
            try:
                ents = y.extract_info(f"ytsearch20:{q}", download=False).get("entries") or []
            except Exception:  # noqa: BLE001
                continue
            for e in ents:
                if not e or e.get("id") in seen:
                    continue
                t = e.get("title") or ""
                if _norm(zh) not in _norm(t) and (not ko or ko.replace(" ", "") not in t.replace(" ", "")):
                    continue
                d = e.get("duration") or 0
                if d > 20 * 60:          # 전편·몰아보기는 장면 신호가 아니다
                    continue
                seen.add(e["id"])
                rows.append({"title": t, "views": e.get("view_count") or 0, "seconds": d,
                             "channel": e.get("channel") or "", "official": is_official(e),
                             "url": e.get("url") or f"https://www.youtube.com/watch?v={e['id']}"})
    rows.sort(key=lambda r: -r["views"])
    return rows[:15]


def get(base, info=None, force=False):
    d = INPUT / base
    info = info or (json.loads((d / "info.json").read_text(encoding="utf-8")) if (d / "info.json").exists() else {})
    zh, ko = info.get("title_zh") or "", info.get("title_ko") or ""
    if not zh:
        return None
    path = d / "research.json"
    if path.exists() and not force:
        cached = json.loads(path.read_text(encoding="utf-8"))
        age = dt.datetime.now() - dt.datetime.fromisoformat(cached.get("at", "2000-01-01"))
        if age.days < REFRESH_DAYS:
            return cached
    print(f"  · 인기 장면 조사: {ko or zh} (유튜브 조회수 + 웨이보·더우인 화제)", flush=True)
    clips = _youtube(zh, ko)
    print(f"    유튜브 관련 영상 {len(clips)}개", flush=True)
    hot = []
    try:
        data, _ = llm.generate_json(HOT_PROMPT.format(zh=zh, ko=ko or zh), engine="claude", web=True, retries=1,
                                    validate=lambda x: [] if isinstance(x, list) else ["배열이어야 함"],
                                    label="웨이보·더우인 화제 장면 찾는 중")
        hot = [h for h in data if isinstance(h, dict) and h.get("scene")][:10]
    except Exception as e:  # noqa: BLE001
        print(f"    ! 화제 장면 검색 실패 (유튜브 결과만 사용): {e}")
    print(f"    화제 장면 {len(hot)}개", flush=True)
    res = {"at": dt.datetime.now().isoformat(timespec="seconds"), "clips": clips, "hot": hot}
    path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def _views(n):
    return f"{n / 10000:.0f}만" if n >= 10000 else str(n)


def prompt_text(res):
    """대본 프롬프트에 넣을 '인기 장면 참고' 블록."""
    if not res or not (res.get("clips") or res.get("hot")):
        return ""
    s = ["## 다른 사람들이 많이 본 장면 (참고)",
         "아래는 이 드라마에서 시청자 반응이 좋았던 장면과, 다른 채널 영상의 제목·조회수다."]
    for h in res.get("hot") or []:
        ep = f"[{h['episode']}] " if h.get("episode") else ""
        kw = ", ".join(h.get("keywords_zh") or [])
        s.append(f"- 화제 장면 {ep}{h['scene']} — {h.get('why', '')}" + (f" (키워드: {kw})" if kw else ""))
    for c in (res.get("clips") or [])[:10]:
        s.append(f"- 영상 \"{c['title'][:70]}\" 조회수 {_views(c['views'])}" + (" (공식 하이라이트)" if c["official"] else ""))
    s += ["활용 방법:",
          "- 이번 편 대사 안에 위 장면과 같은 장면이 있으면 우선 고르고, 그 장면이 왜 반응이 좋았는지를 살려 해설한다.",
          "- 썸네일(cover)과 제목도 반응 좋은 장면·키워드를 참고하되, 남의 제목·문구를 그대로 베끼지 말고 새로 쓴다.",
          "- 위 장면이 이번 편 구간에 없으면 억지로 넣지 않는다.", ""]
    return "\n".join(s) + "\n"
