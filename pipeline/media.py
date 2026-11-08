"""ffmpeg/ffprobe 헬퍼, 음성 인식, 음량 분석."""
import json
import os
import re
import site
import subprocess
from pathlib import Path

import numpy as np

from . import config


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration:stream=codec_type,width,height",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    info = json.loads(r.stdout)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    return {"duration": float(info["format"]["duration"]),
            "width": v.get("width") or 1920, "height": v.get("height") or 1080,
            "has_audio": any(s.get("codec_type") == "audio" for s in info.get("streams", []))}


def letterbox(src):
    """원본에 박혀 있는 위아래 검은 띠(레터박스)를 찾는다 → (그림 시작 y, 그림 높이), 없으면 None.
    회차 20%·50%·80% 지점에서 ffmpeg cropdetect 로 재고, 위아래가 대칭인 띠만 인정한다. 결과는 캐시."""
    src = Path(src)
    cache = config.WORK / "_transcripts" / f"{src.stem}_{src.stat().st_size}.bars.json"
    if cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        return (d["y"], d["h"]) if d.get("h") else None
    info = probe(src)
    W, H, dur = info["width"], info["height"], info["duration"]
    votes = {}
    for k in (0.2, 0.5, 0.8):
        r = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{dur * k:.1f}", "-i", str(src), "-t", "6",
                            "-vf", "cropdetect=24:2:0", "-an", "-f", "null", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        for m in re.finditer(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr):
            w, h, _, y = map(int, m.groups())
            votes[(w, h, y)] = votes.get((w, h, y), 0) + 1
    res = None
    if votes:
        w, h, y = max(votes, key=votes.get)
        bottom = H - h - y
        if w >= W * 0.97 and y >= 8 and abs(y - bottom) <= 8 and h >= H * 0.5:
            res = (y // 2 * 2, h // 2 * 2)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"y": res[0], "h": res[1]} if res else {"h": None}), encoding="utf-8")
    return res


def duration(path):
    return probe(path)["duration"]


def ffmpeg(args, cwd=None):
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error", *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg 오류:\n" + r.stderr[-2000:])


# ---------------- 음성 인식 ----------------
_model = None


def _add_cuda_dlls():
    for sp in site.getsitepackages():
        for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/cuda_nvrtc/bin"):
            d = Path(sp) / sub
            if d.is_dir():
                os.add_dll_directory(str(d))
                os.environ["PATH"] = str(d) + os.pathsep + os.environ["PATH"]


def _load_model():
    global _model
    if _model is None:
        _add_cuda_dlls()
        from faster_whisper import WhisperModel
        try:
            _model = WhisperModel(config.WHISPER_MODEL, device="cuda", compute_type="float16")
        except Exception as e:  # noqa: BLE001
            print(f"  ! GPU 로드 실패, CPU로 전환 (느림): {e}")
            _model = WhisperModel("medium", device="cpu", compute_type="int8")
    return _model


def transcribe(src, cache):
    """중국어 대사 → [{start,end,text}] (cache JSON 재사용)."""
    cache = Path(cache)
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    print(f"  · 음성 인식: {Path(src).name} (처음 한 번만, 회차당 2~5분)", flush=True)
    # condition_on_previous_text=False: 한 번 지어낸 문장(예: '中文字幕志愿者')을 계속 반복하는 것을 막는다
    segs, info = _load_model().transcribe(str(src), language="zh", vad_filter=True, beam_size=5,
                                          condition_on_previous_text=False,
                                          initial_prompt="以下是普通话的句子。")
    total = getattr(info, "duration", 0) or duration(src)
    out, shown = [], 0
    for s in segs:
        t = s.text.strip()
        if t:
            out.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": t})
        pct = int(s.end * 100 / total) if total else 0
        if pct >= shown + 10:
            shown = pct // 10 * 10
            print(f"    음성 인식 {shown}% ({int(s.end // 60)}:{int(s.end % 60):02d} / "
                  f"{int(total // 60)}:{int(total % 60):02d}) · 대사 {len(out)}개", flush=True)
    before = len(out)
    out = drop_hallucinations(out)
    cache.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"    {len(out)}개 대사" + (f" (지어낸 문장·반복 {before - len(out)}개 제외)" if before - len(out) else ""))
    return out


# Whisper 가 음악·무음 구간에서 지어내는 흔한 문구
HALLUCINATIONS = re.compile(r"字幕志愿者|字幕由|字幕提供|字幕组|请不吝点赞|点赞订阅|订阅|转发|打赏|明镜与点点|"
                            r"感谢观看|謝謝觀看|谢谢大家|优优独播剧场|YoYo Television|Amara\.org|本节目")


def drop_hallucinations(segs):
    """지어낸 문구와, 같은 문장이 3번 이상 연달아 나오는 반복을 뺀다."""
    out = [s for s in segs if not HALLUCINATIONS.search(s["text"])]
    cleaned, run = [], []
    for s in out + [None]:
        if s is not None and run and s["text"] == run[-1]["text"]:
            run.append(s)
            continue
        if len(run) < 3:
            cleaned += run
        run = [s] if s is not None else []
    return cleaned


# ---------------- 본편 구간 (오프닝·엔딩 크레딧 제외) ----------------
def story_range(src):
    """(본편 시작, 본편 끝) 초. 회차 맨 끝(엔딩 크레딧) 화면과 색감이 같은 구간을 끝과 처음에서 찾아 뺀다.
    결과는 영상 옆에 캐시한다."""
    src = Path(src)
    # range2: 화면 색으로 크레딧을 못 가리는 드라마(본편도 크레딧과 색감이 비슷함)는 대사로 판단
    cache = config.WORK / "_transcripts" / f"{src.stem}_{src.stat().st_size}.range2.json"
    if cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        return d["start"], d["end"]
    import cv2
    dur = duration(src)
    cap = cv2.VideoCapture(str(src))

    def hist(t):
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000)
        ok, f = cap.read()
        if not ok:
            return None
        hsv = cv2.cvtColor(cv2.resize(f, (160, 90)), cv2.COLOR_BGR2HSV)
        h = cv2.calcHist([hsv], [0, 1], None, [18, 8], [0, 180, 0, 256])
        return cv2.normalize(h, h).flatten()

    refs = [h for h in (hist(dur - k) for k in (3, 8, 13, 18)) if h is not None]
    ref = np.mean(refs, axis=0).astype("float32") if refs else None

    def is_credit(t):
        h = hist(t)
        return h is not None and ref is not None and cv2.compareHist(ref, h, cv2.HISTCMP_CORREL) > 0.6

    # 회차 한가운데(25~75%) 화면도 크레딧과 비슷하게 나오면 색으로는 구분이 안 된다 → 대사로 판단
    mid = [is_credit(dur * (0.25 + 0.5 * k / 19)) for k in range(20)] if ref is not None else []
    if mid and sum(mid) / len(mid) >= 0.7:
        cap.release()
        start, end = _range_by_dialogue(src, dur)
        print(f"    본편 구간(대사 기준, 화면 색으로는 크레딧 구분 불가): {start:.0f}~{end:.0f}초 / {dur:.0f}초")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"start": round(start, 1), "end": round(end, 1), "duration": round(dur, 1),
                                     "by": "dialogue"}), encoding="utf-8")
        return start, end
    end = dur
    if ref is not None and is_credit(dur - 25):          # 끝이 크레딧이면 크레딧 시작점을 찾는다 (최대 6분)
        t, miss = dur - 25, 0
        while t > dur - 360:
            t -= 4
            if is_credit(t):
                end, miss = t, 0
            else:
                miss += 1
                if miss >= 3:
                    break
    start = 0.0
    if ref is not None:                                  # 처음 4분 안의 같은 그림(오프닝 곡)
        t = 0.0
        while t < 240:
            if is_credit(t):
                start = t + 4
            elif start and t - start > 12:
                break
            t += 4
    cap.release()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"start": round(start, 1), "end": round(end, 1), "duration": round(dur, 1)}),
                     encoding="utf-8")
    return start, end


SONG_CPS = 3.0     # 초당 글자 수가 이보다 느리면 노래 가사로 본다 (대사는 보통 4~6자)


def _cps(x):
    n = len(re.sub(r"[\s，。,.!?！？、…]", "", x.get("text") or ""))
    return n / max(0.3, x["end"] - x["start"])


def _song_run(lines):
    """lines 맨 앞부터 이어지는 노래 가사(느린 줄) 개수. 중간에 빠른 줄 하나는 잘못 받아쓴 가사로 보고 넘어간다.
    노래는 쉬지 않고 이어지므로, 20초 넘게 비면 거기서 노래가 끝난 것으로 본다."""
    n = slip = 0
    for i, x in enumerate(lines):
        if i and max(x["start"] - lines[i - 1]["end"], lines[i - 1]["start"] - x["end"]) > 20:
            break
        if _cps(x) < SONG_CPS:
            n, slip = i + 1, 0
        elif slip or i + 1 >= len(lines) or _cps(lines[i + 1]) >= SONG_CPS:
            break
        else:
            slip = 1
    return n


def _range_by_dialogue(src, dur):
    """대사로 본편 구간 추정. 오프닝·엔딩 곡 가사는 대사보다 훨씬 느리게 받아써진다(초당 1~2.5자).
    - 시작: 처음 4분 안, 앞쪽 노래 가사 줄들을 건너뛴 첫 대사
    - 끝: 마지막 6분 안, 끝에서부터 이어지는 노래 가사 앞의 마지막 대사 + 5초 (가사가 시작되기 전까지만)"""
    from .plan import transcript
    lines = [x for x in transcript(src) if (x.get("text") or "").strip()]
    if not lines:
        return 0.0, dur
    head = [x for x in lines if x["start"] < 240]
    k = _song_run(head)
    first = lines[k]["start"] if k < len(lines) else lines[0]["start"]
    start = max(0.0, first - 0.5) if first < 240 else 0.0
    tail = [x for x in lines if x["start"] > max(start, dur - 360)]
    k = _song_run(tail[::-1])
    if k >= len(tail):              # 마지막 6분이 전부 가사로 보이면 판단하지 않는다
        return start, dur
    last = tail[-k - 1]
    song_at = tail[-k]["start"] if k else dur
    return start, min(dur, last["end"] + 5.0, song_at)


# ---------------- 음량 분석 ----------------
def energy_db(src, hop=0.5):
    if not probe(src)["has_audio"]:
        return np.zeros(1), hop
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    n = int(16000 * hop)
    frames = max(1, len(a) // n)
    a = a[:frames * n].reshape(frames, n)
    return 20 * np.log10(np.sqrt((a ** 2).mean(1)) + 1e-6), hop


def add_energy(lines, src):
    """각 대사에 0~1 음량 점수(파일 내 백분위)를 붙인다. 고성·싸움 장면 찾기용."""
    if not lines:
        return lines
    db, hop = energy_db(src)
    vals = []
    for ln in lines:
        a, b = int(ln["start"] / hop), max(int(ln["start"] / hop) + 1, int(ln["end"] / hop))
        seg = db[a:b]
        vals.append(float(seg.mean()) if len(seg) else -90.0)
    order = np.argsort(np.argsort(vals))
    for ln, rnk in zip(lines, order):
        ln["energy"] = round(float(rnk) / max(1, len(vals) - 1), 2)
    return lines
