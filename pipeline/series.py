"""드라마 하나를 1편, 2편, 3편... 시리즈로 이어서 만든다.

- N편은 기본으로 N화를 쓴다 (없으면 공식 채널에서 받는다).
- 앞 편들의 줄거리와 '다음 편 예고'를 대본 프롬프트에 넣어, 이야기를 이어가고 같은 내용을 반복하지 않는다.
- 작업 폴더: 1편 = work/<폴더>, 2편부터 = work/<폴더>_2편 ...
- 0편 = 예고편(work/<폴더>_예고편): 1편 앞에 올리는 등장인물 소개 + 큰 줄거리 예고. 받은 회차(최대 3개)에서 가장 재미있는 장면만 고른다.
"""
import json

from . import config, plan
from .batch import INPUT, _videos


INTRO = 0                 # 예고편 편 번호
INTRO_MAX_EPS = 3         # 예고편에 쓸 최대 회차 수


def part_name(part):
    return "예고편" if part == INTRO else f"{part}편"


def part_dir(base, part):
    if part == INTRO:
        return config.WORK / f"{base}_예고편"
    return config.WORK / (base if part == 1 else f"{base}_{part}편")


def parts(base):
    """이미 만든 편 목록 [(편, plan)]"""
    out = []
    for d in config.WORK.glob(f"{base}*"):
        p = d / "plan.json"
        if not p.exists():
            continue
        pl = json.loads(p.read_text(encoding="utf-8"))
        s = pl.get("series") or {}
        if s.get("base") == base:
            out.append((s["part"], pl))
        elif d.name == base:          # 시리즈 기능 이전에 만든 1편
            out.append((1, pl))
    return sorted(out, key=lambda x: x[0])


def _episode_file(base, ep):
    d = INPUT / base
    for v in _videos(d):
        stem = v.stem.upper()
        if stem in (f"EP{ep:02d}", f"EP{ep}", f"{ep}화", f"{ep:02d}화"):
            return v
    return None


def split_points(src, n):
    """회차 본편을 n편으로 나눌 경계. 등분 지점 ±8% 안에서 대사가 가장 길게 끊기는 곳(장면 전환).
    단, 나눈 한 편이 원작 사용 상한(MAX_SOURCE_RATIO) 안에서 1분을 채울 길이(need)는 되도록 경계 범위를 좁힌다."""
    from . import media
    st, en = media.story_range(src)
    lines = [x for x in plan.transcript(src) if st <= x["start"] and x["end"] <= en]
    need = config.TARGET_MIN_SEC / config.MAX_SOURCE_RATIO + 1.0
    cuts = [st]
    for k in range(1, n):
        target = st + (en - st) * k / n
        lo, hi = target - (en - st) * 0.08, target + (en - st) * 0.08
        # 앞 편이 need 이상, 남은 편들도 각각 need 이상이 되는 범위 (회차가 짧아 불가능하면 그대로)
        f_lo, f_hi = cuts[-1] + need, en - need * (n - k)
        if f_lo <= f_hi:
            lo, hi = max(lo, f_lo), min(hi, f_hi)
            target = min(max(target, f_lo), f_hi)
        gaps = [((b["start"] - a["end"]), (a["end"] + b["start"]) / 2)
                for a, b in zip(lines, lines[1:]) if lo <= a["end"] and b["start"] <= hi]
        cuts.append(max(gaps)[1] if gaps else target)
    return cuts + [en]


def split_for(base):
    """이 드라마는 회차 하나를 몇 편으로 나눌지. 기본 SERIES_SPLIT 이지만, 회차가 짧아서
    나눈 한 편으로는 원작 사용 상한(MAX_SOURCE_RATIO) 안에서 1분을 못 채우면 덜 나눈다 (20분짜리 회차 → 1회차 = 1편).
    처음 정한 값은 info.json 에 적어 두고 계속 쓴다 (편 번호가 바뀌지 않게)."""
    n = max(1, config.SERIES_SPLIT)
    p = INPUT / base / "info.json"
    info = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    if isinstance(info.get("split"), int) and info["split"] >= 1:
        return info["split"]
    ep = next((e for e in range(1, 200) if _episode_file(base, e)), None)
    if ep is None or n == 1:
        return n                                    # 아직 회차가 없으면 기본값 (저장하지 않음)
    from . import media
    st, en = media.story_range(_episode_file(base, ep))
    need = config.TARGET_MIN_SEC / config.MAX_SOURCE_RATIO      # 한 편에 필요한 원본 길이
    while n > 1 and (en - st) / n < need:
        n -= 1
    if n != config.SERIES_SPLIT:
        print(f"  · 회차 본편이 {(en - st) / 60:.0f}분이라 회차 하나를 {n}편으로 나눕니다 "
              f"(한 편에 원본 {need / 60:.0f}분 이상 필요)")
    if p.exists():
        info["split"] = n
        p.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return n


def split_start(base):
    """회차를 나누기 시작하는 회차 (info.json 의 split_start, 기본 1).
    예) 3 이면 1편=1화, 2편=2화 (통째로), 3편=3화 전반, 4편=3화 후반 ... — 이미 회차 단위로 올린 편 번호를 지키려고."""
    p = INPUT / base / "info.json"
    info = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return max(1, int(info.get("split_start") or 1))


def ep_split(base, ep):
    """그 회차를 몇 편으로 나누는지."""
    return 1 if ep < split_start(base) else split_for(base)


def part_num(base, ep, k):
    """(회차, 회차 안의 몇 번째 조각) → 편 번호."""
    start = split_start(base)
    if ep < start:
        return ep
    return (start - 1) + (ep - start) * split_for(base) + k + 1


def episode_part(part, n=None, start=1):
    """N편 → (회차, 회차 안의 몇 번째 조각 0부터). start 앞 회차는 1회차 = 1편."""
    n = max(1, n or config.SERIES_SPLIT)
    if part < start:
        return part, 0
    q = part - (start - 1)
    return start + (q - 1) // n, (q - 1) % n


def make(base, part=None, engine="auto", tts_engine=None, eps=None):
    d = INPUT / base
    if not d.is_dir():
        raise FileNotFoundError(f"input\\{base} 폴더가 없습니다.")
    info = json.loads((d / "info.json").read_text(encoding="utf-8")) if (d / "info.json").exists() else {}
    done = parts(base)
    if part is None:
        part = max([p for p, _ in done], default=0) + 1
    if part == INTRO:
        piece = 0
        if not eps:
            have = sorted(e for e in range(1, 200) if _episode_file(base, e))
            eps = have[:INTRO_MAX_EPS] or [1, 2]
    else:
        ep, piece = episode_part(part, split_for(base), split_start(base))
        eps = eps or [ep]

    files = []
    for ep in eps:
        f = _episode_file(base, ep)
        if not f and info.get("title_zh"):
            from . import fetch
            print(f"  · {ep}화가 없어 공식 채널에서 받습니다")
            fetch.fetch(base, eps=str(ep))
            info = json.loads((d / "info.json").read_text(encoding="utf-8"))
            f = _episode_file(base, ep)
        if not f:
            raise RuntimeError(f"{ep}화 영상이 없습니다. input\\{base}\\EP{ep:02d}.mp4 로 넣어주세요.")
        files.append(f)

    prev = [{"part": p, "story": pl.get("story", ""), "recap": pl.get("recap", ""), "cliff": pl.get("cliff", "")}
            for p, pl in done if INTRO < p < part]      # 예고편은 이어받지 않는다
    drama = info.get("title_ko") or base
    windows, where = None, ""
    n = ep_split(base, eps[0]) if part != INTRO else 1
    if n > 1 and len(files) == 1 and part != INTRO:
        cuts = split_points(files[0], n)
        windows = {0: (cuts[piece], cuts[piece + 1])}
        where = f" {['전반', '후반'][piece] if n == 2 else f'{piece + 1}/{n}'} {plan.fmt(cuts[piece])}~{plan.fmt(cuts[piece + 1])}"
    print(f"=== {drama} {part_name(part)} ({', '.join(f.name for f in files)}{where}) ===")
    hot = ""
    try:
        from . import research
        hot = research.prompt_text(research.get(base, info))
    except Exception as e:  # noqa: BLE001
        print(f"  ! 인기 장면 조사 실패 (없이 진행): {e}")
    return plan.run(drama, [str(f) for f in files], engine=engine, name=part_dir(base, part).name,
                    tts_engine=tts_engine, info=info, windows=windows, hot=hot,
                    series={"base": base, "part": part, "eps": eps, "piece": piece, "split": n, "prev": prev})
