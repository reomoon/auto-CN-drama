"""ffmpeg/ffprobe 헬퍼, 음성 인식, 음량 분석."""
import json
import os
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
    segs, info = _load_model().transcribe(str(src), language="zh", vad_filter=True, beam_size=5,
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
    cache.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"    {len(out)}개 대사")
    return out


# ---------------- 본편 구간 (오프닝·엔딩 크레딧 제외) ----------------
def story_range(src):
    """(본편 시작, 본편 끝) 초. 회차 맨 끝(엔딩 크레딧) 화면과 색감이 같은 구간을 끝과 처음에서 찾아 뺀다.
    결과는 영상 옆에 캐시한다."""
    src = Path(src)
    cache = config.WORK / "_transcripts" / f"{src.stem}_{src.stat().st_size}.range.json"
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
