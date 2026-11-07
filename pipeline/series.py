"""드라마 하나를 1편, 2편, 3편... 시리즈로 이어서 만든다.

- N편은 기본으로 N화를 쓴다 (없으면 공식 채널에서 받는다).
- 앞 편들의 줄거리와 '다음 편 예고'를 대본 프롬프트에 넣어, 이야기를 이어가고 같은 내용을 반복하지 않는다.
- 작업 폴더: 1편 = work/<폴더>, 2편부터 = work/<폴더>_2편 ...
"""
import json

from . import config, plan
from .batch import INPUT, _videos


def part_dir(base, part):
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
    """회차 본편을 n편으로 나눌 경계. 등분 지점 ±8% 안에서 대사가 가장 길게 끊기는 곳(장면 전환)."""
    from . import media
    st, en = media.story_range(src)
    lines = [x for x in plan.transcript(src) if st <= x["start"] and x["end"] <= en]
    cuts = [st]
    for k in range(1, n):
        target = st + (en - st) * k / n
        lo, hi = target - (en - st) * 0.08, target + (en - st) * 0.08
        gaps = [((b["start"] - a["end"]), (a["end"] + b["start"]) / 2)
                for a, b in zip(lines, lines[1:]) if lo <= a["end"] and b["start"] <= hi]
        cuts.append(max(gaps)[1] if gaps else target)
    return cuts + [en]


def episode_part(part):
    """N편 → (회차, 회차 안의 몇 번째 조각 0부터)."""
    n = max(1, config.SERIES_SPLIT)
    return (part - 1) // n + 1, (part - 1) % n


def make(base, part=None, engine="auto", tts_engine=None, eps=None):
    d = INPUT / base
    if not d.is_dir():
        raise FileNotFoundError(f"input\\{base} 폴더가 없습니다.")
    info = json.loads((d / "info.json").read_text(encoding="utf-8")) if (d / "info.json").exists() else {}
    done = parts(base)
    part = part or (max([p for p, _ in done], default=0) + 1)
    ep, piece = episode_part(part)
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
            for p, pl in done if p < part]
    drama = info.get("title_ko") or base
    windows, where = None, ""
    n = max(1, config.SERIES_SPLIT)
    if n > 1 and len(files) == 1:
        cuts = split_points(files[0], n)
        windows = {0: (cuts[piece], cuts[piece + 1])}
        where = f" {['전반', '후반'][piece] if n == 2 else f'{piece + 1}/{n}'} {plan.fmt(cuts[piece])}~{plan.fmt(cuts[piece + 1])}"
    print(f"=== {drama} {part}편 ({', '.join(f.name for f in files)}{where}) ===")
    hot = ""
    try:
        from . import research
        hot = research.prompt_text(research.get(base, info))
    except Exception as e:  # noqa: BLE001
        print(f"  ! 인기 장면 조사 실패 (없이 진행): {e}")
    return plan.run(drama, [str(f) for f in files], engine=engine, name=part_dir(base, part).name,
                    tts_engine=tts_engine, info=info, windows=windows, hot=hot,
                    series={"base": base, "part": part, "eps": eps, "piece": piece, "split": n, "prev": prev})
