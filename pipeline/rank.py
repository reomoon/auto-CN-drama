"""인기 중국 드라마 순위 수집 (Claude 웹 검색)."""
import datetime as dt
import json

from . import config, llm

PROMPT = """오늘은 {today}입니다. 웹 검색으로 지금 인기 있는 {kind} 순위를 조사하세요.
참고할 곳: 웨이보 열검색·드라마 화제도, 더우반(豆瓣), 아이치이·텐센트·유쿠·망고TV 열도 순위,
{extra}MyDramaList, 국내(왓챠·티빙·네이버 등) 화제작.
최근 3개월 안에 화제가 된 작품 위주로 상위 {n}개를 고르고, 짧은 영상으로 만들기 좋은
'막장 지수'(불륜·복수·출생의 비밀·뺨 때리기·신분 반전 등)도 1~5점으로 매기세요.

JSON 배열만 출력하세요(설명 금지):
[{{"rank":1,"title_ko":"한국어 제목(없으면 번역)","title_zh":"중국어 원제","platform":"방영 플랫폼",
"genre":"장르","episodes":"화수","makjang":4,"hook":"쇼츠로 만들기 좋은 이유 한 줄",
"source":"근거 URL 1개"}}]"""


def run(n=20, short=False):
    today = dt.date.today().isoformat()
    kind = "중국 숏드라마(微短剧, 세로형 1~2분 회차)" if short else "중국 드라마(장편 시리즈)"
    extra = "홍궈(红果短剧)·더우인 숏드라마 순위, " if short else ""
    prompt = PROMPT.format(today=today, kind=kind, extra=extra, n=n)

    def validate(d):
        if not isinstance(d, list) or not d:
            return ["JSON 배열이어야 합니다"]
        return [f"{i}번 항목에 title_zh 없음" for i, x in enumerate(d) if not x.get("title_zh")]

    print(f"인기 {'숏드라마' if short else '드라마'} 순위 검색 중 (Claude 웹 검색)...")
    data, used = llm.generate_json(prompt, engine="claude", web=True, validate=validate)
    config.DATA.mkdir(exist_ok=True)
    path = config.DATA / f"rank_{'short_' if short else ''}{today}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[{used}] 결과 → {path}\n")
    for x in data:
        print(f"{x.get('rank', '?'):>2}. {x.get('title_ko', '')} ({x.get('title_zh', '')}) "
              f"| {x.get('platform', '')} | 막장 {'★' * int(x.get('makjang') or 0)}")
        print(f"    {x.get('hook', '')}")
    return path
