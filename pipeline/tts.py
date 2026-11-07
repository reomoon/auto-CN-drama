"""나레이션 음성 생성. 같은 문장은 캐시해서 크레딧을 아낀다.

엔진(TTS_ENGINE 또는 --tts): elevenlabs | typecast | gemini | google | edge(무료 미리보기) | auto
auto 는 키가 있는 엔진을 ElevenLabs → Typecast → Gemini → Google 순으로 시도하고, 실패하면 다음으로 넘어간다.
호출 방식은 auto-insta(youtube/build_youtube_video.py, youtube/gemini_tts.py)와 같다.
"""
import asyncio
import base64
import hashlib
import io
import json
import time
import wave
from pathlib import Path

import requests

from . import config

PROVIDERS = ("elevenlabs", "typecast", "gemini", "google", "edge")
AUTO_ORDER = ("elevenlabs", "typecast", "gemini", "google")


def available(provider):
    """그 엔진의 키(와 목소리)가 설정돼 있는지."""
    return {"elevenlabs": bool(config.ELEVEN_KEY and config.ELEVEN_VOICE),
            "typecast": bool(config.TYPECAST_KEY), "gemini": bool(config.GEMINI_KEY),
            "google": bool(config.GOOGLE_TTS_KEY), "edge": True}.get(provider, False)


VOICE_LISTS = {"typecast": config.TYPECAST_VOICES, "gemini": config.GEMINI_VOICES, "google": config.GOOGLE_TTS_VOICES}
DEFAULT_VOICE = {"typecast": config.TYPECAST_VOICE, "gemini": config.GEMINI_VOICE, "google": config.GOOGLE_TTS_VOICE}
_override = {}   # 렌더 한 번 동안 고른 목소리 {엔진: 키}


def set_voice(provider, key):
    """대시보드·--voice 로 고른 목소리를 이번 렌더에 쓴다 (한 영상 안에서는 고정)."""
    if provider in VOICE_LISTS and key:
        if key not in VOICE_LISTS[provider]:
            raise ValueError(f"{provider} 에 없는 목소리: {key} (가능: {', '.join(VOICE_LISTS[provider])})")
        _override[provider] = key


def voice_key(provider):
    key = _override.get(provider) or DEFAULT_VOICE.get(provider)
    lst = VOICE_LISTS.get(provider, {})
    return key if key in lst else next(iter(lst), None)


def voice_value(provider):
    return VOICE_LISTS[provider][voice_key(provider)][1]


def voices():
    """대시보드용 {엔진: [(키, 이름)]}"""
    return {p: [(k, v[0]) for k, v in lst.items()] for p, lst in VOICE_LISTS.items()}


def _voice_of(provider):
    if provider == "elevenlabs":
        return f"{config.ELEVEN_VOICE}:{config.ELEVEN_MODEL}:{config.ELEVEN_SPEED}"
    if provider == "edge":
        return config.EDGE_VOICE
    return f"{voice_key(provider)}:{voice_value(provider)}:{config.GEMINI_TTS_MODEL if provider == 'gemini' else ''}"


def _speed_up(path, speed=None):
    """auto-insta 처럼 음높이는 두고 빠르기만 올린다."""
    import subprocess
    speed = speed or config.NARRATION_SPEED
    if abs(speed - 1) < 1e-3:
        return
    tmp = Path(str(path) + ".fast.wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-af", f"atempo={speed}", str(tmp)], check=True)
    tmp.replace(path)


# ---------------- 엔진별 호출 ----------------
def _via_elevenlabs(text, out):
    if not available("elevenlabs"):
        raise RuntimeError(".env 에 ELEVENLABS_API_KEY 와 ELEVENLABS_VOICE_ID 를 넣어주세요.")
    vs = {"stability": 0.45, "similarity_boost": 0.8, "style": 0.3, "use_speaker_boost": True}
    if config.ELEVEN_SPEED:
        vs["speed"] = float(config.ELEVEN_SPEED)
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVEN_VOICE}?output_format=mp3_44100_128",
        headers={"xi-api-key": config.ELEVEN_KEY, "accept": "audio/mpeg"},
        json={"text": text, "model_id": config.ELEVEN_MODEL, "voice_settings": vs}, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs 오류 {r.status_code}: {r.text[:300]}")
    Path(out).write_bytes(r.content)


def _via_typecast(text, out):
    if not available("typecast"):
        raise RuntimeError(".env 에 TYPECAST_API_KEY 를 넣어주세요.")
    r = requests.post(
        "https://api.typecast.ai/v1/text-to-speech",
        headers={"X-API-KEY": config.TYPECAST_KEY, "Content-Type": "application/json"},
        json={"model": "ssfm-v30", "voice_id": voice_value("typecast"), "text": text, "language": "kor",
              "prompt": {"emotion_type": "smart", "previous_text": "", "next_text": ""},
              "output": {"audio_format": "wav", "audio_pitch": 1, "audio_tempo": 1, "target_lufs": -13}},
        timeout=120)
    if not r.ok:
        raise RuntimeError(f"Typecast 오류 {r.status_code}: {r.text[:300]}")
    ctype = r.headers.get("Content-Type", "")
    data = base64.b64decode(r.json()["audio"]) if "json" in ctype else r.content
    Path(out).write_bytes(data)
    _speed_up(out)


def _pcm_to_wav(pcm, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _via_gemini(text, out):
    if not available("gemini"):
        raise RuntimeError(".env 에 GEMINI_API_KEY 를 넣어주세요.")
    body = {"model": config.GEMINI_TTS_MODEL,
            "input": [{"type": "user_input", "content": [{"type": "text", "text": text,
                                                          "annotations": [{"type": "speech_metadata",
                                                                           "style": config.GEMINI_STYLE}]}]}],
            "response_format": {"type": "audio", "mime_type": "audio/wav"},
            "generation_config": {"speech_config": [{"voice": voice_value("gemini")}]}}
    last = ""
    for attempt in range(3):
        try:
            r = requests.post("https://generativelanguage.googleapis.com/v1beta/interactions",
                              headers={"x-goog-api-key": config.GEMINI_KEY, "Content-Type": "application/json"},
                              json=body, timeout=300)
        except requests.RequestException as e:
            last = type(e).__name__
        else:
            if r.status_code == 200:
                audio = [c for step in r.json().get("steps", []) if step.get("type") == "model_output"
                         for c in step.get("content", []) if c.get("type") == "audio"]
                if not audio:
                    raise RuntimeError("Gemini 응답에 소리가 없습니다.")
                raw = base64.b64decode(audio[-1]["data"])
                Path(out).write_bytes(raw if raw[:4] == b"RIFF" else _pcm_to_wav(raw))
                _speed_up(out)
                return
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Gemini 오류: {last}")


def _via_google(text, out):
    if not available("google"):
        raise RuntimeError(".env 에 GOOGLE_TTS_API_KEY 를 넣어주세요.")
    r = requests.post("https://texttospeech.googleapis.com/v1/text:synthesize",
                      params={"key": config.GOOGLE_TTS_KEY},
                      json={"input": {"text": text},
                            "voice": {"languageCode": "ko-KR", "name": voice_value("google")[0]},
                            "audioConfig": {"audioEncoding": "LINEAR16", "pitch": voice_value("google")[1],
                                            "speakingRate": config.GOOGLE_TTS_RATE}},
                      timeout=60)
    if not r.ok:
        raise RuntimeError(f"Google TTS 오류 {r.status_code}: {r.text[:300]}")
    Path(out).write_bytes(base64.b64decode(r.json()["audioContent"]))


def _via_edge(text, out):
    import edge_tts
    asyncio.run(edge_tts.Communicate(text, config.EDGE_VOICE, rate="+12%").save(str(out)))


_RUN = {"elevenlabs": (_via_elevenlabs, "mp3"), "typecast": (_via_typecast, "wav"), "gemini": (_via_gemini, "wav"),
        "google": (_via_google, "wav"), "edge": (_via_edge, "mp3")}


def _cached(provider, text, cache_dir):
    fn, ext = _RUN[provider]
    key = hashlib.sha1(f"{provider}|{_voice_of(provider)}|{text}".encode()).hexdigest()[:16]
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{provider}_{key}.{ext}"
    if not out.exists() or out.stat().st_size == 0:
        fn(text, out)
    return out


def synth(text, cache_dir, engine=None):
    """나레이션 한 문장 → 음성 파일. 엔진을 지정하면 그 엔진만 쓴다(실패를 다른 엔진으로 숨기지 않게)."""
    provider = (engine or config.TTS_ENGINE or "").strip().lower()
    if provider == "elevenlabs":
        return _cached("elevenlabs", text, cache_dir)
    if provider == "typecast":
        return _cached("typecast", text, cache_dir)
    if provider == "gemini":
        return _cached("gemini", text, cache_dir)
    if provider == "google":
        return _cached("google", text, cache_dir)
    if provider == "edge":
        return _cached("edge", text, cache_dir)
    if provider not in (None, "", "auto"):
        raise ValueError(f"지원하지 않는 음성 엔진: {provider}")

    errors = []
    for p in AUTO_ORDER:   # auto: 키가 있는 엔진부터 차례로, 실패하면 다음 엔진
        if not available(p):
            continue
        try:
            path = _cached(p, text, cache_dir)
            if errors:
                print(f"    ({' / '.join(errors)} → {p} 로 대체)", flush=True)
            return path
        except Exception as e:  # noqa: BLE001
            errors.append(f"{p} 실패: {str(e)[:80]}")
    raise RuntimeError("쓸 수 있는 음성 엔진이 없습니다: " + ("; ".join(errors) or "키가 설정된 엔진 없음"))


# ---------------- 말하기 속도 기록 (길이 예측용) ----------------
STATS = config.WORK / ".tts_stats.json"


def _chars(text):
    return len("".join(text.split()))


def _stat_key(engine):
    """목소리·모델마다 말하기 속도가 달라서 따로 기록한다."""
    provider = (engine or config.TTS_ENGINE or "auto").lower()
    if provider == "auto":
        provider = next((p for p in AUTO_ORDER if available(p)), "edge")
    if provider not in PROVIDERS:
        return provider
    return f"{provider}:{_voice_of(provider)}"


def record(text, sec, engine=None):
    """실제 TTS 길이를 기록해서 다음 대본의 길이 추정에 쓴다."""
    key = _stat_key(engine)
    data = json.loads(STATS.read_text(encoding="utf-8")) if STATS.exists() else {}
    data[key] = (data.get(key, []) + [[_chars(text), round(sec, 2)]])[-60:]
    STATS.parent.mkdir(parents=True, exist_ok=True)
    STATS.write_text(json.dumps(data), encoding="utf-8")


def chars_per_sec(engine=None, default=4.6):
    if not STATS.exists():
        return default
    rows = json.loads(STATS.read_text(encoding="utf-8")).get(_stat_key(engine), [])
    vals = sorted(c / max(0.5, s - 0.1) for c, s in rows if c >= 8)
    return round(vals[len(vals) // 2], 2) if len(vals) >= 3 else default
