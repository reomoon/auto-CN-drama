"""나레이션 음성 생성. 같은 문장은 캐시해서 크레딧을 아낀다."""
import asyncio
import hashlib
import json
from pathlib import Path

import requests

from . import config


def _eleven(text, out):
    if not (config.ELEVEN_KEY and config.ELEVEN_VOICE):
        raise RuntimeError(".env 에 ELEVENLABS_API_KEY 와 ELEVENLABS_VOICE_ID 를 넣어주세요. "
                           "(무료 미리보기는 --tts edge)")
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


def _edge(text, out):
    import edge_tts
    asyncio.run(edge_tts.Communicate(text, config.EDGE_VOICE, rate="+12%").save(str(out)))


STATS = config.WORK / ".tts_stats.json"


def _chars(text):
    return len("".join(text.split()))


def record(text, sec, engine=None):
    """실제 TTS 길이를 기록해서 다음 대본의 길이 추정에 쓴다."""
    engine = engine or config.TTS_ENGINE
    data = json.loads(STATS.read_text(encoding="utf-8")) if STATS.exists() else {}
    data[engine] = (data.get(engine, []) + [[_chars(text), round(sec, 2)]])[-60:]
    STATS.parent.mkdir(parents=True, exist_ok=True)
    STATS.write_text(json.dumps(data), encoding="utf-8")


def chars_per_sec(engine=None, default=5.0):
    engine = engine or config.TTS_ENGINE
    if not STATS.exists():
        return default
    rows = json.loads(STATS.read_text(encoding="utf-8")).get(engine, [])
    vals = sorted(c / max(0.5, s - 0.1) for c, s in rows if c >= 8)
    return round(vals[len(vals) // 2], 2) if len(vals) >= 3 else default


def synth(text, cache_dir, engine=None):
    engine = engine or config.TTS_ENGINE
    voice = config.ELEVEN_VOICE if engine == "elevenlabs" else config.EDGE_VOICE
    key = hashlib.sha1(f"{engine}|{voice}|{config.ELEVEN_MODEL}|{config.ELEVEN_SPEED}|{text}".encode()).hexdigest()[:16]
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{engine}_{key}.mp3"
    if not out.exists() or out.stat().st_size == 0:
        (_eleven if engine == "elevenlabs" else _edge)(text, out)
    return out
