import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

WORK = ROOT / "work"
OUT = ROOT / "out"
DATA = ROOT / "data"
PROMPTS = ROOT / "prompts"


def env(key, default=None):
    v = os.getenv(key)
    return v if v not in (None, "") else default


# --- LLM (구독 CLI) ---
CLAUDE_PATH = env("CLAUDE_PATH")          # 비우면 자동 탐색
CLAUDE_MODEL = env("CLAUDE_MODEL")        # 예: sonnet / opus (비우면 기본값)
CODEX_PATH = env("CODEX_PATH")            # 비우면 자동 탐색
CODEX_MODEL = env("CODEX_MODEL", "gpt-6-luna")
CODEX_EFFORT = env("CODEX_EFFORT", "medium")

# --- 음성 인식 ---
WHISPER_MODEL = env("WHISPER_MODEL", "large-v3")

# --- TTS ---
TTS_ENGINE = env("TTS_ENGINE", "elevenlabs")   # elevenlabs | edge(무료 미리보기용)
ELEVEN_KEY = env("ELEVENLABS_API_KEY")
ELEVEN_VOICE = env("ELEVENLABS_VOICE_ID")
ELEVEN_MODEL = env("ELEVENLABS_MODEL", "eleven_multilingual_v2")
ELEVEN_SPEED = env("ELEVENLABS_SPEED")         # 0.7~1.2, 비우면 기본
EDGE_VOICE = env("EDGE_VOICE", "ko-KR-InJoonNeural")

# --- 영상 ---
MAX_SEC = float(env("MAX_SEC", "90"))
TARGET_MIN_SEC = float(env("TARGET_MIN_SEC", "62"))   # 틱톡 리워드는 1분 초과 필요
TARGET_MAX_SEC = float(env("TARGET_MAX_SEC", "86"))
FG_ZOOM = float(env("FG_ZOOM", "1.5"))                # 가로 영상 확대 비율 (클수록 양옆을 더 자르고 크게)
BG_STYLE = env("BG_STYLE", "black")                   # black(검은 여백) | blur(흐린 배경)
TITLE2_COLOR = env("TITLE2_COLOR", "pink")            # 상단 제목 2줄 색: pink | yellow | #RRGGBB
NARR_COLOR = env("NARR_COLOR", "yellow")              # 나레이션 자막 색
FONT = env("FONT", "Malgun Gothic")
SUB_MARGIN_V = int(env("SUB_MARGIN_V", "470"))        # 자막 아래 여백(px). 틱톡 하단 UI 피하기
NARR_ORIG_VOLUME = float(env("NARR_ORIG_VOLUME", "0.15"))  # 나레이션 중 원본 볼륨

# --- 공정 이용 가이드라인 (법적 기준이 아닌 내부 안전 기준) ---
NARR_RATIO_MIN = float(env("NARR_RATIO_MIN", "0.5"))      # 해설(나레이션) 비중 하한
MAX_SOURCE_RATIO = float(env("MAX_SOURCE_RATIO", "0.10"))  # 원작 사용량 상한 (쓴 원본 길이 ÷ 원본 회차 전체 길이)
MIN_ANALYSIS = int(env("MIN_ANALYSIS", "2"))               # 해석·비평·감상 나레이션 최소 개수
