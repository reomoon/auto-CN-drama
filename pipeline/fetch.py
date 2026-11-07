"""공식 채널(인증 배지 + 플랫폼 공식명)에 올라온 회차만 찾아서 받는다.

재업로드·요약(一口气看完)·몰아보기 영상은 받지 않는다.
"""
import json
import re
from pathlib import Path

from opencc import OpenCC

from . import config

INPUT = config.ROOT / "input"
OFFICIAL = ["腾讯视频", "騰訊視頻", "WeTV", "爱奇艺", "愛奇藝", "iQIYI", "优酷", "優酷", "YOUKU", "Youku",
            "芒果TV", "MangoTV", "Mango TV", "CCTV", "央视", "央視"]
EXCLUDE = ["一口气", "一口氣", "合集", "全集", "解说", "解說", "recap", "预告", "預告", "花絮", "trailer",
           "reaction", "cut", "精彩片段", "抢先看", "搶先看", "幕后", "幕後", "shorts", "#shorts"]
_t2s = OpenCC("t2s")


def _norm(s):
    return _t2s.convert(s or "").lower()


def _ep_match(title, ep):
    t = _norm(title)
    return bool(re.search(rf"(?:ep|e)\s*0*{ep}(?!\d)|第\s*0*{ep}\s*[集话話]", t))


def is_official(entry):
    ch = entry.get("channel") or entry.get("uploader") or ""
    return bool(entry.get("channel_is_verified")) and any(k.lower() in ch.lower() for k in OFFICIAL)


def find(title_zh, ep, min_minutes=15):
    """(entry, 이유). 조건을 모두 통과한 첫 결과를 돌려준다."""
    import yt_dlp
    want = _norm(title_zh)
    seen, rejects = set(), []
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as ydl:
        for q in (f"{title_zh} EP{ep:02d}", f"{title_zh} 第{ep}集"):
            for e in (ydl.extract_info(f"ytsearch15:{q}", download=False).get("entries") or []):
                if not e or e.get("id") in seen:
                    continue
                seen.add(e["id"])
                title = e.get("title") or ""
                if want not in _norm(title):
                    continue
                if not is_official(e):
                    rejects.append(f"비공식 채널: {e.get('channel')}")
                    continue
                if any(x.lower() in title.lower() for x in EXCLUDE):
                    continue
                if not _ep_match(title, ep):
                    continue
                if (e.get("duration") or 0) < min_minutes * 60:
                    continue
                return e, None
        # 2차: 카탈로그에 있는 작품이면 그 공식 채널 안에서 직접 검색
        cid, cname = _catalog_channel(title_zh, ydl)
        if cid:
            url = f"https://www.youtube.com/channel/{cid}/search?query={title_zh}"
            for e in (ydl.extract_info(url, download=False).get("entries") or []):
                title = (e or {}).get("title") or ""
                if (want in _norm(title) and _ep_match(title, ep)
                        and not any(x.lower() in title.lower() for x in EXCLUDE)
                        and (e.get("duration") or 0) >= min_minutes * 60):
                    e.setdefault("channel", cname)
                    return e, None
    return None, (f"공식 채널 영상 없음 (확인한 비공식 업로드 {len(rejects)}개는 제외)" if rejects
                  else "검색 결과 없음")


def _catalog_channel(title_zh, ydl):
    cats = sorted(config.DATA.glob("official_*.json"))
    if not cats:
        return None, None
    hit = next((x for x in json.loads(cats[-1].read_text(encoding="utf-8"))
                if _norm(x["title_zh"]) == _norm(title_zh)), None)
    if not hit:
        return None, None
    cid = hit.get("channel_id")
    if not cid:
        cid = ydl.extract_info(hit["sample_url"], download=False, process=False).get("channel_id")
    return cid, hit["channel"]


def _parse_eps(spec):
    out = []
    for part in str(spec).split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def fetch(folder, eps="1-2", height=1080):
    import yt_dlp
    d = INPUT / folder
    info_p = d / "info.json"
    info = json.loads(info_p.read_text(encoding="utf-8")) if info_p.exists() else {}
    zh = info.get("title_zh")
    if not zh:
        raise RuntimeError(f"{folder}: info.json 에 중국어 원제(title_zh)가 없어 검색할 수 없습니다.")
    print(f"== {folder} ({zh}) ==")
    official = {o["ep"]: o for o in info.get("official", [])}
    got = 0
    for ep in _parse_eps(eps):
        out_base = d / f"EP{ep:02d}"
        if any((d / f"EP{ep:02d}{ext}").exists() for ext in (".mp4", ".mkv", ".webm")):
            print(f"  · {ep}화: 이미 있음")
            continue
        e, why = find(zh, ep)
        if not e:
            print(f"  ! {ep}화: {why}")
            continue
        url = e.get("url") or f"https://www.youtube.com/watch?v={e['id']}"
        print(f"  · {ep}화: [{e.get('channel')}] {e.get('title')[:60]}")
        last = {"p": -10}

        def hook(s):
            if s.get("status") == "downloading" and s.get("total_bytes"):
                p = int(s["downloaded_bytes"] * 100 / s["total_bytes"])
                if p >= last["p"] + 20:
                    last["p"] = p
                    print(f"    다운로드 {p}%")

        for junk in d.glob(f"EP{ep:02d}.f*"):   # 이전에 끊긴 조각 정리
            junk.unlink()
        opts = {"quiet": True, "no_warnings": True, "noprogress": True, "progress_hooks": [hook],
                "legacy_server_connect": True, "retries": 10, "fragment_retries": 10,
                "format": (f"bv*[height<={height}][vcodec^=avc1]+ba[ext=m4a]/"
                           f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/b[height<={height}]"),
                "merge_output_format": "mp4", "outtmpl": str(out_base) + ".%(ext)s"}
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        official[ep] = {"ep": ep, "channel": e.get("channel"), "title": e.get("title"), "url": url}
        got += 1
        print(f"    완료 → {out_base.name}.mp4")
    info["official"] = sorted(official.values(), key=lambda o: o["ep"])
    info_p.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return got


# ---------------- 공식 채널 카탈로그 ----------------
CHANNEL_QUERIES = ["腾讯视频 EP01 MULTI SUB", "WeTV EP01 古装", "WeTV EP01 都市", "腾讯视频 悬疑 EP01",
                   "iQIYI EP01 MULTI SUB", "爱奇艺 古装 EP01", "iQIYI 悬疑 EP01", "爱奇艺华语剧场 EP01",
                   "YOUKU EP01 MULTI SUB", "优酷 古装 EP01", "YOUKU ROMANCE EP01", "优酷华语剧场 EP01",
                   "芒果TV 电视剧 EP01", "MangoTV drama EP01"]
SKIP_CHANNEL = ["SHOW", "ANIMATION", "Indonesia", "Thailand", "Philippines", "Arabic", "Malaysia",
                "Vietnam", "English"]
TAG_JUNK = re.compile(r"FULL|SUB|DUB|Multi|会员|會員|EP\d|限免|4K|精编|抢先|試看|试看|Kiwi|独播|獨播", re.I)
CJK = re.compile(r"[一-鿿]")


def _drama_name(title):
    """《미인여》 → 美人余. 【Multi-Sub|FULL】 같은 태그는 건너뛴다."""
    for pat in (r"《([^》]{1,30})》", r"【([^】]{1,30})】", r"「([^」]{1,30})」"):
        for m in re.finditer(pat, title):
            s = m.group(1).strip()
            if TAG_JUNK.search(s) or len(CJK.findall(s)) < 2:
                continue
            lead = re.match(r"[一-鿿·：:0-9 ]+", s)
            return _t2s.convert((lead.group(0) if lead else s).strip(" ：:"))
    rest = re.sub(r"[【\[「(（][^】\]」)）]*[】\]」)）]", " ", title)   # 태그 지우고 맨 앞 한자 제목
    m = re.match(r"\s*([一-鿿·]{2,15})(?=[\s｜|:：A-Za-z]|$)", rest)
    if m and not TAG_JUNK.search(m.group(1)):
        return _t2s.convert(m.group(1))
    return None


def catalog(per_channel=150, min_minutes=15):
    """공식 채널의 최근 업로드에서 '지금 회차가 올라와 있는 드라마' 목록을 만든다."""
    import datetime as dt

    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True}
    channels = {}
    print("공식 채널 찾는 중...")
    with yt_dlp.YoutubeDL(opts) as y:
        for q in CHANNEL_QUERIES:
            for e in y.extract_info(f"ytsearch20:{q}", download=False).get("entries") or []:
                ch = (e or {}).get("channel") or ""
                if e and is_official(e) and e.get("channel_id") and not any(s in ch for s in SKIP_CHANNEL):
                    channels[e["channel_id"]] = ch
    print(f"  {len(channels)}개: " + ", ".join(c.split(" - Get")[0].split("-Get")[0] for c in channels.values()))

    dramas = {}
    with yt_dlp.YoutubeDL({**opts, "playlistend": per_channel}) as y:
        for order, (cid, cname) in enumerate(channels.items()):
            try:
                ents = y.extract_info(f"https://www.youtube.com/channel/{cid}/videos", download=False).get("entries") or []
            except Exception as ex:  # noqa: BLE001
                print(f"  ! {cname}: {ex}")
                continue
            for rank, e in enumerate(ents):
                t = e.get("title") or ""
                name = _drama_name(t)
                ep = re.search(r"(?:EP|E)\s*0*(\d{1,3})(?!\d)|第\s*0*(\d{1,3})\s*[集话話]", t, re.I)
                if not name or not ep or (e.get("duration") or 0) < min_minutes * 60:
                    continue
                if any(x.lower() in t.lower() for x in EXCLUDE):
                    continue
                n = int(ep.group(1) or ep.group(2))
                d = dramas.setdefault(name, {"title_zh": name, "channel": cname, "channel_id": cid,
                                             "eps": set(), "recent": rank,
                                             "sample_title": t, "sample_url": f"https://www.youtube.com/watch?v={e['id']}"})
                d["eps"].add(n)
                if rank < d["recent"]:
                    d["recent"] = rank
    rows = sorted(dramas.values(), key=lambda d: (d["recent"], -len(d["eps"])))
    for d in rows:
        d["eps"] = sorted(d["eps"])
    _enrich(rows)
    config.DATA.mkdir(exist_ok=True)
    path = config.DATA / f"official_{dt.date.today().isoformat()}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n회차가 올라와 있는 드라마 {len(rows)}개 (최근 업로드 순) → {path}\n")
    for i, d in enumerate(rows, 1):
        e = d["eps"]
        print(f"{i:>2}. {d['title_zh']:<12} {d['channel'].split(' - Get')[0].split('-Get')[0][:20]:<20} "
              f"회차 {len(e)}개 (EP{e[0]}~{e[-1]})")
    return path


ENRICH_PROMPT = """아래는 지금 중국 플랫폼 공식 유튜브에 회차가 올라와 있는 작품들의 중국어 제목이다.
웹 검색으로 확인해서 각 작품의 정보를 채워라. 드라마가 아닌 것(예능·다큐·애니)은 kind를 "기타"로.
막장 지수(makjang)는 불륜·복수·출생의 비밀·뺨 때리기·신분 반전 같은 자극적 전개 정도 1~5.

{titles}

JSON 배열만 출력:
[{{"title_zh":"원래 제목 그대로","title_ko":"한국어 제목(국내 방영명 있으면 그것)","kind":"드라마|기타",
"genre":"장르","makjang":3,"hook":"쇼츠로 만들 만한 포인트 한 줄"}}]"""


def _enrich(rows):
    if not rows:
        return
    from . import llm
    print("작품 정보 붙이는 중 (Claude 웹 검색)...")
    try:
        data, _ = llm.generate_json(ENRICH_PROMPT.format(titles="\n".join(d["title_zh"] for d in rows)),
                                    engine="claude", web=True, retries=1,
                                    validate=lambda d: [] if isinstance(d, list) else ["배열이어야 함"])
    except Exception as e:  # noqa: BLE001
        print(f"  ! 정보 붙이기 실패 (제목만 표시): {e}")
        return
    by = {_norm(x.get("title_zh")): x for x in data if isinstance(x, dict)}
    for d in rows:
        x = by.get(_norm(d["title_zh"]))
        if x:
            d.update({k: x.get(k) for k in ("title_ko", "kind", "genre", "makjang", "hook")})


def add_from_catalog(title_zh):
    """카탈로그 작품을 input 폴더로 추가 (O01_ 접두사)."""
    from . import plan
    d0 = next((d for d in INPUT.glob("*") if d.is_dir() and (d / "info.json").exists() and
               _norm(json.loads((d / "info.json").read_text(encoding="utf-8")).get("title_zh")) == _norm(title_zh)), None)
    if d0:
        return d0.name
    info = {"title_zh": title_zh}
    cats = sorted(config.DATA.glob("official_*.json"))
    if cats:
        hit = next((x for x in json.loads(cats[-1].read_text(encoding="utf-8"))
                    if _norm(x["title_zh"]) == _norm(title_zh)), None)
        if hit:
            info.update({k: hit.get(k) for k in ("title_ko", "genre", "makjang", "hook") if hit.get(k)})
            info["platform"] = hit["channel"].split(" - Get")[0].split("-Get")[0]
    n = 1 + max([int(m.group(1)) for d in INPUT.glob("O*_*") if (m := re.match(r"O(\d+)_", d.name))] or [0])
    INPUT.mkdir(exist_ok=True)
    d = INPUT / f"O{n:02d}_{plan.slugify(info.get('title_ko') or title_zh)}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return d.name


def pick(title_zh, eps="1-2"):
    name = add_from_catalog(title_zh)
    print(f"폴더: {name}")
    fetch(name, eps=eps)
    return name


def fetch_many(only=None, eps="1-2"):
    folders = sorted(p.name for p in INPUT.iterdir() if p.is_dir()) if INPUT.exists() else []
    if only:
        folders = [f for f in folders if (f == only if (INPUT / only).is_dir() else only in f)]
    total = 0
    for f in folders:
        try:
            total += fetch(f, eps=eps)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {f}: {e}")
    print(f"\n새로 받은 회차: {total}개")
