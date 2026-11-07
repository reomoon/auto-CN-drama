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


def make(base, part=None, engine="auto", tts_engine=None, eps=None):
    d = INPUT / base
    if not d.is_dir():
        raise FileNotFoundError(f"input\\{base} 폴더가 없습니다.")
    info = json.loads((d / "info.json").read_text(encoding="utf-8")) if (d / "info.json").exists() else {}
    done = parts(base)
    part = part or (max([p for p, _ in done], default=0) + 1)
    eps = eps or [part]

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
    print(f"=== {drama} {part}편 ({', '.join(f.name for f in files)}) ===")
    return plan.run(drama, [str(f) for f in files], engine=engine, name=part_dir(base, part).name,
                    tts_engine=tts_engine, info=info,
                    series={"base": base, "part": part, "eps": eps, "prev": prev})
