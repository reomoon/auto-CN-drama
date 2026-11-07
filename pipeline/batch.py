"""순위 → input 폴더 만들기, 폴더 단위 일괄 처리, 환경 점검."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import requests

from . import config, llm, plan, render

INPUT = config.ROOT / "input"
VIDEO_EXT = {".mp4", ".mkv", ".ts", ".mov", ".webm", ".flv", ".m4v"}


def _natural(p):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.name)]


def _videos(d):
    return sorted((f for f in d.iterdir() if f.suffix.lower() in VIDEO_EXT), key=_natural)


def _latest_rank(short):
    files = sorted(config.DATA.glob("rank_short_*.json" if short else "rank_2*.json"))
    if not files:
        raise FileNotFoundError("순위 파일이 없습니다. 먼저 `python main.py rank` 를 실행하세요.")
    return files[-1]


# ---------------- inbox ----------------
def inbox(rank_file=None, short=False, top=10):
    path = Path(rank_file) if rank_file else _latest_rank(short)
    data = json.loads(path.read_text(encoding="utf-8"))[:top]
    prefix = "S" if (short or "short" in path.name) else "D"
    INPUT.mkdir(exist_ok=True)
    print(f"{path.name} 기준으로 input 폴더 생성\n")
    for i, x in enumerate(data, 1):
        d = INPUT / f"{prefix}{i:02d}_{plan.slugify(x.get('title_ko') or x['title_zh'])}"
        d.mkdir(exist_ok=True)
        (d / "info.json").write_text(json.dumps(x, ensure_ascii=False, indent=2), encoding="utf-8")
        n = len(_videos(d))
        print(f"  {d.name:<40} 영상 {n}개" + ("" if n else "  ← 여기에 회차 영상을 넣으세요"))
    print("\n영상을 넣은 뒤 `python main.py batch` 로 대본을 만듭니다. (회차 1~3개 권장)")


# ---------------- batch ----------------
def batch(do_render=False, engine="auto", tts_engine=None, only=None):
    if not INPUT.exists():
        print("input 폴더가 없습니다. `python main.py inbox` 로 만드세요.")
        return
    done, skipped, failed = [], [], []
    for d in sorted(p for p in INPUT.iterdir() if p.is_dir()):
        if only and only not in d.name:
            continue
        wdir = config.WORK / plan.slugify(d.name)
        video = config.OUT / wdir.name / f"{wdir.name}.mp4"
        has_plan = (wdir / "plan.json").exists()
        try:
            if do_render:
                if not has_plan:
                    skipped.append(f"{d.name} (대본 없음)")
                elif video.exists():
                    skipped.append(f"{d.name} (이미 완성)")
                else:
                    print(f"\n=== 렌더: {d.name} ===")
                    render.run(wdir, tts_engine=tts_engine)
                    done.append(d.name)
                continue
            vids = _videos(d)
            if not vids:
                skipped.append(f"{d.name} (영상 없음)")
            elif has_plan:
                skipped.append(f"{d.name} (대본 있음)")
            else:
                info_p = d / "info.json"
                info = json.loads(info_p.read_text(encoding="utf-8")) if info_p.exists() else {}
                drama = info.get("title_ko") or re.sub(r"^[SD]\d+_", "", d.name).replace("_", " ")
                print(f"\n=== 대본: {d.name} ({len(vids)}개 영상) ===")
                plan.run(drama, vids, engine=engine, name=d.name, tts_engine=tts_engine, info=info)
                done.append(d.name)
        except Exception as e:  # noqa: BLE001  한 편이 실패해도 나머지는 계속
            failed.append(f"{d.name}: {e}")
            print(f"  ! 실패: {e}")

    print(f"\n완료 {len(done)} · 건너뜀 {len(skipped)} · 실패 {len(failed)}")
    for s in skipped:
        print(f"  - {s}")
    for f in failed:
        print(f"  ! {f}")
    if done and not do_render:
        print("\nwork\\<폴더>\\plan.md 를 확인한 뒤 `python main.py batch --render` 로 영상을 만드세요.")


# ---------------- ElevenLabs ----------------
def _eleven_get(path):
    if not config.ELEVEN_KEY:
        raise RuntimeError(".env 에 ELEVENLABS_API_KEY 가 없습니다.")
    r = requests.get(f"https://api.elevenlabs.io{path}", headers={"xi-api-key": config.ELEVEN_KEY}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
    return r.json()


def voices():
    data = _eleven_get("/v1/voices")["voices"]
    print(f"{'Voice ID':<24} {'이름':<24} 특징")
    for v in sorted(data, key=lambda v: v.get("category") != "cloned"):
        lab = v.get("labels") or {}
        tags = ", ".join(str(x) for x in (v.get("category"), lab.get("gender"), lab.get("age"),
                                          lab.get("accent"), lab.get("description")) if x)
        print(f"{v['voice_id']:<24} {v['name'][:22]:<24} {tags}")
    print("\n마음에 드는 Voice ID를 .env 의 ELEVENLABS_VOICE_ID 에 넣으세요.")


# ---------------- doctor ----------------
def doctor():
    ok = lambda b: "OK " if b else "!! "  # noqa: E731
    print(ok(shutil.which("ffmpeg")) + "ffmpeg")
    try:
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                             capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        gpu = ""
    print(ok(gpu) + f"GPU: {gpu or '없음 (음성 인식이 CPU로 느리게 동작)'}")
    print(ok(Path(r"C:\Windows\Fonts\malgun.ttf").exists()) + f"자막 폰트: {config.FONT}")

    for eng in ("claude", "gpt"):
        try:
            text, _ = llm.generate("OK라고만 답해.", engine=eng, fallback=False)
            print(ok(True) + f"{eng} 구독 CLI 응답: {text[:20]}")
        except Exception as e:  # noqa: BLE001
            print(ok(False) + f"{eng}: {str(e)[:150]}")

    try:
        sub = _eleven_get("/v1/user/subscription")
        left = sub.get("character_limit", 0) - sub.get("character_count", 0)
        print(ok(True) + f"ElevenLabs: {sub.get('tier')} 요금제, 남은 글자 {left:,}자 (영상 1편 ≈ 250자)")
        if config.ELEVEN_VOICE:
            v = _eleven_get(f"/v1/voices/{config.ELEVEN_VOICE}")
            print(ok(True) + f"목소리: {v.get('name')}")
        else:
            print(ok(False) + "ELEVENLABS_VOICE_ID 없음 → `python main.py voices` 로 고르세요")
    except Exception as e:  # noqa: BLE001
        print(ok(False) + f"ElevenLabs: {e}  (무료 미리보기는 --tts edge)")

    n_in = sum(len(_videos(d)) for d in INPUT.iterdir() if d.is_dir()) if INPUT.exists() else 0
    print(ok(n_in) + f"input 폴더 영상: {n_in}개")
