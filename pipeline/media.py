"""ffmpeg/ffprobe 헬퍼, 음성 인식, 음량 분석."""
import json
import os
import site
import subprocess
from pathlib import Path

import numpy as np

from . import config


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    info = json.loads(r.stdout)
    return {"duration": float(info["format"]["duration"]),
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
    print(f"  · 음성 인식: {Path(src).name}")
    segs, _ = _load_model().transcribe(str(src), language="zh", vad_filter=True, beam_size=5,
                                       initial_prompt="以下是普通话的句子。")
    out = []
    for s in segs:
        t = s.text.strip()
        if t:
            out.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": t})
    cache.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"    {len(out)}개 대사")
    return out


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
