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

### 대시보드 (가장 편한 방법)

```powershell
.\.venv\Scripts\python main.py web          # http://127.0.0.1:8700
.\.venv\Scripts\python main.py web --lan    # 같은 Wi-Fi의 폰에서도 접속 (세로 영상 확인용)
```

- **순위**: 숏드라마/장편 순위 카드, 막장 지수, 폴더별 진행 상태를 보여줍니다. 순위 새로고침과 폴더 만들기를 여기서 실행합니다.
- **작업**: 9:16 플레이어로 완성본을 확인합니다. 회차 영상을 끌어다 놓으면 해당 폴더에 업로드되고, 대본 만들기와 렌더(미리보기 무료 음성 / 최종 ElevenLabs)도 여기서 실행합니다.
- **대본 편집**: 나레이션과 번역 자막, 제목, 상단 문구, 캡션, 해시태그를 고칠 수 있고 항목을 빼거나 되살릴 수도 있습니다. 예상 길이가 실시간으로 바뀌고, 저장한 뒤 다시 렌더하면 영상에 반영됩니다. 렌더한 영상은 타임라인의 시간을 누르면 그 지점부터 재생됩니다.
- **업로드 문구**: 제목과 캡션+해시태그를 복사 버튼으로 가져갑니다. 틱톡·네이버 클립 업로드 완료를 체크하면 날짜가 기록됩니다.
- 작업은 GPU 하나를 쓰기 때문에 한 번에 하나씩 순서대로 실행됩니다. 오른쪽 위 버튼을 누르면 실시간 로그가 열립니다.
- `--lan`은 같은 네트워크의 누구나 접속할 수 있으므로 집 Wi-Fi에서만 사용하세요.

### 시리즈 (1편 = 1화, 2편 = 2화 ...)

```powershell
.\.venv\Scripts\python main.py series D05_미인여 --render   # 다음 편 (회차가 없으면 공식 채널에서 받음)
.\.venv\Scripts\python main.py series D05_미인여 --part 3    # 특정 편 다시 만들기
```

- 2편부터는 첫 나레이션에서 "지난 이야기"를 한 문장으로 짚고, 앞 편 줄거리와 예고를 이어받습니다. 같은 장면은 반복하지 않습니다.
- 매 편 마지막은 의견 + 질문 + 다음 편 떡밥 + "다음 편에서 계속됩니다"로 끝납니다.
- 업로드 제목은 `[미인여 2편] ...`, 화면 라벨은 `〈미인여〉 2편`으로 들어갑니다.
- 대시보드에서는 '다음 편 만들기' 버튼으로 실행합니다. 작업 폴더는 `work\D05_미인여`(1편), `work\D05_미인여_2편` ...

### 외부 접속 (auto-cn.hot-news.app)

```powershell
.\.venv\Scripts\python main.py web --remote
```

```
외부 ──HTTPS──▶ AWS Lightsail nginx (auto-cn.hot-news.app, Let's Encrypt)
                    │ 127.0.0.1:8701
                    ▼ SSH 역방향 터널 (PC가 연결, 끊기면 자동 재연결)
               PC 대시보드 127.0.0.1:8700
```

- AWS는 입구 역할만 합니다. 음성 인식, 대본, 렌더는 PC에서 실행되므로 **PC가 켜져 있어야** 접속됩니다.
- `.env`의 `DASHBOARD_PASSWORD`로 로그인합니다. 이 PC에서 `127.0.0.1`로 열 때만 비밀번호 없이 열립니다.
- `.env` 설정: `REMOTE_SSH_HOST`, `REMOTE_SSH_USER`, `REMOTE_SSH_KEY`, `REMOTE_PORT`(기본 8701)
- 서버 nginx 설정: `/etc/nginx/sites-available/auto-cn.hot-news.app` (업로드 최대 2GB)

### 매일 돌리는 흐름 (명령어)

```powershell
.\.venv\Scripts\python main.py doctor           # 환경 점검 (CLI 로그인, GPU, ElevenLabs 잔여 글자)
.\.venv\Scripts\python main.py voices           # ElevenLabs 목소리 목록 → .env 에 Voice ID 입력

.\.venv\Scripts\python main.py rank --short     # 1) 인기 순위 수집
.\.venv\Scripts\python main.py inbox --short    # 2) input\S01_드라마명\ ... 폴더 생성
#    → 원하는 폴더에 회차 영상 1~3개를 넣는다 (1화.mp4, 2화.mp4 ...)
.\.venv\Scripts\python main.py batch            # 3) 영상이 들어온 폴더만 대본 생성 → work\<폴더>\plan.md 확인
.\.venv\Scripts\python main.py batch --render   # 4) 대본이 있고 아직 영상이 없는 폴더만 렌더 → out\<폴더>\
```

- 폴더 하나가 숏폼 한 편입니다. 이미 대본이나 완성본이 있는 폴더는 건너뜁니다.
- 순위 정보(원제, 플랫폼, 쇼츠 포인트)는 `info.json`에 저장되어 대본 작성에 참고 자료로 들어갑니다.
- 한 편이 실패해도 나머지는 계속 진행하고, 마지막에 결과를 요약합니다.
- 숏드라마는 `S`, 장편은 `D`로 폴더 이름이 시작합니다.

### 개별 실행

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

## 화면 템플릿

| | `bold` (기본) | `classic` |
|---|---|---|
| 제목 | 상단 검은 띠, Black Han Sans 굵은 글씨, 1줄 노랑·2줄 형광초록 | 흰색·분홍, 작품명 \| ep 라벨 |
| 영상 | 제목 바로 아래부터 크게 (자동 확대, 얼굴 위치를 따라 양옆 자르기) | 가운데, 위아래 검은 여백 |
| 해설 자막 | 영상 아래쪽에 굵은 노란 글씨 | 영상 아래 여백에 노란 글씨 |
| 대사 자막 | 같은 자리에 흰 글씨 | 영상 안쪽 아래 흰 글씨 |

- `.env`의 `TEMPLATE=bold|classic`으로 고릅니다. 색과 확대 비율도 `.env`에서 바꿀 수 있습니다.
- 나레이션이 시작되면 원음이 부드럽게 15%까지 줄었다가, 원본 장면 직전에 다시 커집니다 (`NARR_ORIG_VOLUME`, `DUCK_FADE`).
- 글꼴 [Black Han Sans](fonts/OFL.txt)(SIL OFL)와 얼굴 검출 모델 YuNet(OpenCV Zoo, MIT)이 저장소에 들어 있습니다.

## 원본 영상 구하기

```powershell
.\.venv\Scripts\python main.py catalog                 # 공식 채널에 회차가 올라온 드라마 목록 (data\official_날짜.json)
.\.venv\Scripts\python main.py pick "美人余" --eps 1-2   # 폴더 추가 + 회차 받기
.\.venv\Scripts\python main.py fetch --only D05_미인여    # 이미 있는 폴더에 회차 받기
```

- **인증 배지가 있는 플랫폼 공식 채널**(텐센트/WeTV, 아이치이, 유쿠, 망고TV, CCTV)의 회차만 받습니다. 재업로드, 요약(一口气看完), 몰아보기, 예고편은 받지 않습니다.
- 업로드 문구의 출처 표기에 공식 채널과 링크가 자동으로 들어갑니다.
- 홍궈 등 숏드라마 독점작은 공식 유튜브가 없어서 자동으로 받을 수 없습니다.
- 대시보드 순위 탭의 **'공식 영상 있음'**에서 '1~2화 가져오기' 버튼으로도 받을 수 있습니다.

## 공정 이용 점검 (내부 기준)

| 항목 | 기준 | 설정 |
|---|---|---|
| 원음 그대로 나오는 원본 구간 | 20~30% (나머지는 나레이션, 원음은 작게) | `NARR_RATIO_MIN`, `NARR_RATIO_MAX` |
| 해석·비평·감상 나레이션 | 2개 이상 | `MIN_ANALYSIS` |
| 원작 사용량 (쓴 길이 ÷ 원본 회차 전체) | 10% 이하 | `MAX_SOURCE_RATIO` |

- 대본을 만들 때 위 기준을 자동으로 검사하고, `plan.md`와 대시보드에 결과가 표시됩니다.
- 원본이 너무 짧아 10% 기준으로 1분을 못 채우면 대본을 만들지 않습니다.
- **10%는 법적 기준이 아닙니다.** 한국 공정이용(저작권법 제35조의5)은 목적, 사용량, 시장 영향을 종합해서 판단하며, 이 기준을 지켜도 권리자 신고가 들어올 수 있습니다.
- 신고를 받으면 대시보드에서 **'저작권 신고 받음'**을 체크하세요. 즉시 비공개 처리 안내가 표시됩니다. 이의제기는 신중하게 하세요.

## 주의

원본 드라마 영상의 저작권은 제작사에 있습니다. 해설 비중을 충분히 유지하고, 가능하면 제작사·플랫폼의 공식 클립 제휴 프로그램을 이용하세요. 권리자 신고 위험은 업로더가 집니다.
