# auto-CN-drama

중국 드라마 원본 영상에서 막장 하이라이트를 골라 **나레이션 해설이 들어간 60~90초 세로 숏폼**을 만드는 파이프라인입니다. 틱톡과 네이버 클립 업로드용입니다.

대본은 **Claude·ChatGPT 구독 계정**(Claude Code CLI, Codex CLI)을 영상마다 번갈아 호출해서 씁니다. API 키는 필요 없습니다. 나레이션은 ElevenLabs로 만듭니다.

```
rank    인기 순위 수집 (웨이보·더우반·플랫폼 열도·홍궈 숏드라마 등, Claude 웹 검색)
plan    음성 인식(Whisper) → 하이라이트 선정 + 나레이션 대본 → plan.md (검토용)
render  ElevenLabs 나레이션 → 1080x1920 편집 + 자막 → mp4 + 업로드 문구
```

샘플(테스트 화면 + 합성 음성): [samples/](samples/)

## 설치

필요한 것: Windows, Python 3.11, ffmpeg(PATH), NVIDIA GPU(없으면 CPU로 느리게 동작), Claude Code와 Codex에 구독 계정으로 로그인.

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # ElevenLabs 키와 Voice ID 입력
```

## 사용법

```powershell
# 1) 인기 순위
.\.venv\Scripts\python main.py rank            # 장편 드라마
.\.venv\Scripts\python main.py rank --short    # 숏드라마(微短剧)

# 2) 하이라이트 + 대본 → work\<이름>\plan.md 확인
.\.venv\Scripts\python main.py plan "미인여" input\ep1.mp4 input\ep2.mp4

# 3) plan.md를 보고 필요하면 plan.json을 고친 뒤 렌더
.\.venv\Scripts\python main.py render work\미인여
#    → out\미인여\미인여.mp4, out\미인여\post.txt (제목·캡션·해시태그)

# 한 번에 하기 / 무료 음성으로 미리보기
.\.venv\Scripts\python main.py make "미인여" input\ep1.mp4 --tts edge
```

옵션
- `--llm claude|gpt|auto`: 대본 작성 모델. 기본 `auto`는 영상마다 번갈아 사용하고, 한쪽이 실패하면 다른 쪽으로 넘어갑니다.
- `--name`: 같은 드라마로 여러 편 만들 때 작업 폴더 이름을 따로 지정합니다.
- `--tts edge`: ElevenLabs 크레딧을 쓰지 않는 무료 미리보기 음성입니다.

채널 말투와 훅 스타일은 [prompts/style.txt](prompts/style.txt)에서 수정합니다.

## 동작 방식

- **하이라이트**: 대사 텍스트와 음량(고성·싸움 장면)을 모델에 주고 구간을 고르게 합니다. 대사가 많으면 음량이 높고 대사가 몰린 구간 위주로 추려서 넘깁니다.
- **길이 맞추기**: 나레이션 비중 40% 이상, 62~86초를 목표로 합니다. 초과하면 코드가 원본 클립을 가장 격한 7초로 줄이거나 덜 격한 클립을 뺍니다. 그래도 90초를 넘으면 최대 8%까지 배속합니다.
- **나레이션 속도 보정**: 렌더할 때마다 실제 TTS 속도를 `work/.tts_stats.json`에 기록하고, 다음 대본의 길이 추정에 씁니다.
- **편집**: 흐린 배경 위에 원본을 배치합니다. 나레이션 중에는 원본 소리를 15%로 줄입니다. 원본 대사는 흰 자막, 나레이션은 노란 자막으로 표시하고, 상단 제목은 고정합니다. 음량은 -14 LUFS로 맞춥니다.

## 주의

원본 드라마 영상의 저작권은 제작사에 있습니다. 해설 비중을 충분히 유지하고, 가능하면 제작사·플랫폼의 공식 클립 제휴 프로그램을 이용하세요. 권리자 신고 위험은 업로더가 집니다.
