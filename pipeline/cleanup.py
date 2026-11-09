"""올린 지 오래된 원본 회차를 지워 디스크를 비운다.

규칙: 그 회차로 만드는 편(드라마마다 SERIES_SPLIT 개 이하, 예: 전반·후반)이 모두 업로드(틱톡 또는 클립)됐고,
그중 마지막 업로드로부터 CLEAN_AFTER_DAYS 일이 지나면 → input 의 원본 회차 파일과 그 편들의 렌더 중간 파일을 지운다.
완성 쇼츠(out), 대본(plan), 나레이션 음성(tts)은 남긴다. 다시 필요하면 '공식 채널에서 찾기'로 받으면 된다.
"""
import datetime as dt
import json
import shutil
import time

from . import config, series
from .batch import INPUT


def _uploaded_at(part_dir_name):
    p = config.OUT / part_dir_name / "status.json"
    if not p.exists():
        return None
    st = json.loads(p.read_text(encoding="utf-8"))
    times = [st.get(k) for k in ("tiktok", "clip") if st.get(k)]
    if not times:
        return None
    return max(dt.datetime.strptime(t, "%Y-%m-%d %H:%M") for t in times)


def plan_cleanup():
    """[(회차 파일, 삭제 가능 여부, 이유, 관련 편 폴더들)]"""
    out = []
    for d in sorted(p for p in INPUT.iterdir() if p.is_dir()) if INPUT.exists() else []:
        done = dict(series.parts(d.name))
        for ep in sorted({e for pl in done.values() for e in (pl.get("series") or {}).get("eps", [1])}):
            f = series._episode_file(d.name, ep)
            if not f:
                continue
            nums = [series.part_num(d.name, ep, k) for k in range(series.ep_split(d.name, ep))]
            if ep in ((done.get(series.INTRO) or {}).get("series") or {}).get("eps", []):
                nums = [series.INTRO] + nums          # 예고편이 이 회차를 쓰면 예고편도 올려야 지운다
            dirs = [series.part_dir(d.name, num).name for num in nums]
            missing = [num for num in nums if num not in done]
            if missing:
                out.append((f, False, f"{', '.join(map(series.part_name, missing))}을 아직 안 만듦", dirs))
                continue
            ups = [_uploaded_at(x) for x in dirs]
            if not all(ups):
                not_up = [num for num, u in zip(nums, ups) if not u]
                out.append((f, False, f"{', '.join(map(series.part_name, not_up))} 아직 안 올림", dirs))
                continue
            last = max(ups)
            left = config.CLEAN_AFTER_DAYS - (dt.datetime.now() - last).total_seconds() / 86400
            if left > 0:
                out.append((f, False, f"마지막 업로드 {last:%m-%d}, {left:.1f}일 뒤 삭제", dirs))
            else:
                out.append((f, True, f"모두 업로드, 마지막 업로드 {last:%m-%d}", dirs))
    return out


def run(dry_run=False, quiet=False):
    freed = 0
    for f, ok, why, dirs in plan_cleanup():
        if not quiet or ok:
            print(f"{'삭제' if ok else '보관'}  {f.parent.name}\\{f.name}  ({why})")
        if not ok or dry_run:
            continue
        size = f.stat().st_size
        f.unlink()
        freed += size
        for x in dirs:
            r = config.WORK / x / "render"
            if r.is_dir():
                shutil.rmtree(r, ignore_errors=True)
        with open(config.DATA / "cleanup.log", "a", encoding="utf-8") as log:
            log.write(f"{time.strftime('%Y-%m-%d %H:%M')} 삭제 {f} ({size / 2**20:.0f}MB) - {why}\n")
    if freed or not quiet:
        print(f"{'(시험) ' if dry_run else ''}비운 용량: {freed / 2**30:.2f}GB")
    return freed
