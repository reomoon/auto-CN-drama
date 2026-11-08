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
TTS_ENGINE = env("TTS_ENGINE", "elevenlabs")   # elevenlabs | typecast | gemini | google | edge(무료) | auto
ELEVEN_KEY = env("ELEVENLABS_API_KEY")
ELEVEN_VOICE = env("ELEVENLABS_VOICE_ID")
ELEVEN_MODEL = env("ELEVENLABS_MODEL", "eleven_v4_turbo")
ELEVEN_SPEED = env("ELEVENLABS_SPEED")         # 0.7~1.2, 비우면 기본
EDGE_VOICE = env("EDGE_VOICE", "ko-KR-InJoonNeural")
# 아래 목소리 목록·설정은 auto-insta(youtube/build_youtube_video.py, youtube/gemini_tts.py)와 같다.
# Typecast
TYPECAST_KEY = env("TYPECAST_API_KEY")
TYPECAST_VOICES = {"seohyun": ("서현", "tc_69f2e455ea79fd197aa0476f"),
                   "hanyoung": ("한영", "tc_6731b3ac075b04a944644234"),
                   "gangil": ("강일", "tc_68d4b115f0486108a7eefb37")}
TYPECAST_VOICE = env("TYPECAST_VOICE", "gangil")                 # 위 목록의 키
# Gemini
GEMINI_KEY = env("GEMINI_API_KEY")
GEMINI_TTS_MODEL = env("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts")
GEMINI_VOICES = {"female": ("여성 Kore", "Kore"), "male": ("남성 Charon", "Charon")}
GEMINI_VOICE = env("GEMINI_VOICE", "male")
GEMINI_STYLE = env("GEMINI_STYLE", "중국 드라마 해설 쇼츠 진행자, 또박또박한 합쇼체, 긴장감 있게 리듬감 있게, 조금 빠른 템포, 과장된 연기 없이")
# Google Cloud TTS: (목소리 이름, pitch 세미톤)
GOOGLE_TTS_KEY = env("GOOGLE_TTS_API_KEY")
GOOGLE_TTS_VOICES = {"female": ("여성 Neural2-A", ("ko-KR-Neural2-A", -5.0)),
                     "male": ("남성 Neural2-C", ("ko-KR-Neural2-C", 1.0))}
GOOGLE_TTS_VOICE = env("GOOGLE_TTS_VOICE", "male")
GOOGLE_TTS_RATE = 1.0
NARRATION_SPEED = 1.05   # Typecast·Gemini 나레이션은 auto-insta 처럼 1.05배 빠르게 (음높이는 그대로)

# --- 영상 ---
MAX_SEC = float(env("MAX_SEC", "90"))
TARGET_MIN_SEC = float(env("TARGET_MIN_SEC", "62"))   # 틱톡 리워드는 1분 초과 필요
TARGET_MAX_SEC = float(env("TARGET_MAX_SEC", "86"))
TEMPLATE = env("TEMPLATE", "bold")                   # bold(굵은 제목 + 큰 화면) | classic(가운데 화면 + 위아래 여백)
TITLE_FONT = env("TITLE_FONT", "Black Han Sans")     # bold 템플릿 글꼴 (fonts 폴더에 포함: Black Han Sans, Pretendard Black)
BOLD_ZOOM = float(env("BOLD_ZOOM", "1.8"))            # bold 템플릿 최소 확대 (클수록 양옆을 더 자르고 크게)
BOLD_SHIFT = int(env("BOLD_SHIFT", "180"))            # bold 템플릿 제목·영상을 아래로 내리는 정도(px). 틱톡·클립 상단 UI(프로필·탭)에 제목이 가리지 않게
BOLD_FILL = float(env("BOLD_FILL", "0.75"))           # 제목 아래 영역을 이 비율 이상 채우도록 자동 확대
BOLD_MAX_ZOOM = float(env("BOLD_MAX_ZOOM", "2.2"))    # 자동 확대 상한 (너무 키우면 흐려짐)
BOLD_TITLE1 = env("BOLD_TITLE1", "neon")              # bold 제목 1줄 색 (노랑)
BOLD_TITLE2 = env("BOLD_TITLE2", "green")             # bold 제목 2줄 색 (형광 초록)
BOLD_NARR = env("BOLD_NARR", "neon")                  # bold 해설 자막 색
DUCK_FADE = float(env("DUCK_FADE", "0.35"))           # 나레이션 시작·끝에 원음이 줄었다 커지는 시간(초)
COVER_SHOCK_WEIGHT = float(env("COVER_SHOCK_WEIGHT", "5"))  # 썸네일: 놀란 표정 가중치 (0 = 표정 무시, 클수록 놀란 얼굴 우선)
COVER_SECONDS = float(env("COVER_SECONDS", "1.0"))   # 맨 앞 표지(썸네일 장면 + 제목만, 자막 없음) 길이 — 틱톡·클립 썸네일이 된다
NARR_FONT_SIZE = int(env("NARR_FONT_SIZE", "104"))    # bold 템플릿 노란 해설 자막 크기
DIALOG_FONT_SIZE = int(env("DIALOG_FONT_SIZE", "74")) # bold 템플릿 흰 대사 자막 크기
FG_ZOOM = float(env("FG_ZOOM", "1.5"))                # classic 템플릿 가로 영상 확대 비율
BG_STYLE = env("BG_STYLE", "black")                   # black(검은 여백) | blur(흐린 배경)
TITLE2_COLOR = env("TITLE2_COLOR", "pink")            # 상단 제목 2줄 색: pink | yellow | #RRGGBB
NARR_COLOR = env("NARR_COLOR", "yellow")              # 나레이션 자막 색
FONT = env("FONT", "Malgun Gothic")
SUB_MARGIN_V = int(env("SUB_MARGIN_V", "470"))        # 자막 아래 여백(px). 틱톡 하단 UI 피하기
NARR_ORIG_VOLUME = float(env("NARR_ORIG_VOLUME", "0.15"))  # 나레이션 중 원본 볼륨

MAX_HASHTAGS = int(env("MAX_HASHTAGS", "3"))          # 해시태그 개수 (#중드 #작품명 + 1개)
SERIES_SPLIT = int(env("SERIES_SPLIT", "2"))          # 회차 하나를 몇 편으로 나눌지 (2 = 1편 1화 전반, 2편 1화 후반 ...)
CLEAN_AFTER_DAYS = float(env("CLEAN_AFTER_DAYS", "7"))  # 회차로 만든 편이 다 올라가고 이 기간이 지나면 원본 회차 삭제

# --- 업로드 (auto-insta 의 업로드 코드·로그인 계정을 그대로 사용) ---
AUTO_INSTA_DIR = env("AUTO_INSTA_DIR", r"C:\python\auto-insta")
CLIP_ACCOUNT = env("CLIP_ACCOUNT", "docu")             # 네이버 클립 계정: docu(다큐 클립 계정) | main
CLIP_CATEGORY = env("CLIP_CATEGORY", "이슈")           # 클립 카테고리 (클립 업로드 화면의 이름 그대로)
UPLOAD_AI_LABEL = env("UPLOAD_AI_LABEL", "0") not in ("0", "false", "no")   # 틱톡·클립 'AI 생성' 표시 (기본: 켜지 않음)

# --- 대시보드 원격 접속 ---
DASHBOARD_PASSWORD = env("DASHBOARD_PASSWORD")   # 비우면 원격(터널·Wi-Fi) 접속 차단, 이 PC(127.0.0.1)는 항상 허용
REMOTE_SSH_HOST = env("REMOTE_SSH_HOST")          # AWS 서버 주소 (예: 3.36.175.120)
REMOTE_SSH_USER = env("REMOTE_SSH_USER", "ubuntu")
REMOTE_SSH_KEY = env("REMOTE_SSH_KEY")            # SSH 키 파일 경로
REMOTE_PORT = int(env("REMOTE_PORT", "8701"))     # AWS nginx 가 프록시하는 포트
REMOTE_URL = env("REMOTE_URL", "https://auto-cn.hot-news.app")

# --- 공정 이용 가이드라인 (법적 기준이 아닌 내부 안전 기준) ---
NARR_RATIO_MIN = float(env("NARR_RATIO_MIN", "0.7"))      # 해설(나레이션) 비중 하한 → 원음 구간 30% 이하
NARR_RATIO_MAX = float(env("NARR_RATIO_MAX", "0.8"))      # 해설 비중 상한 → 원음 구간 20% 이상
MAX_SOURCE_RATIO = float(env("MAX_SOURCE_RATIO", "0.10"))  # 원작 사용량 상한 (쓴 원본 길이 ÷ 원본 회차 전체 길이)
MIN_ANALYSIS = int(env("MIN_ANALYSIS", "2"))               # 해석·비평·감상 나레이션 최소 개수
