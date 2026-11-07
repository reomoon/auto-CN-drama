"""중국 드라마 해설 숏폼 파이프라인

  python main.py web [--lan]                    # 대시보드 http://127.0.0.1:8700
  python main.py doctor                         # 환경 점검
  python main.py voices                         # ElevenLabs 목소리 목록
  python main.py rank [--short] [--n 20]        # 인기 순위
  python main.py inbox [--short] [--top 10]     # 순위 → input\드라마별 폴더
  python main.py catalog                        # 공식 채널에 회차가 올라온 드라마 목록
  python main.py pick "锦心似玉" [--eps 1-2]      # 카탈로그 작품 → 폴더 추가 + 회차 받기
  python main.py fetch [--only 폴더] [--eps 1-2] # 공식 채널에서 회차 받기
  python main.py series D05_미인여 [--render]     # 시리즈 다음 편 (2편 = 2화, 없으면 공식 채널에서 받음)
  python main.py batch                          # 영상 들어온 폴더 → 대본(plan.md)
  python main.py batch --render                 # 대본 확인 끝난 폴더 → 영상

  python main.py plan "드라마명" 영상1.mp4 [...] [--llm auto|claude|gpt] [--name 폴더명]
  python main.py render work\\폴더명 [--tts elevenlabs|edge]
  python main.py upload work\\폴더명 --to tiktok|clip [--private] [--dry-run]   # auto-insta 계정으로 올리기
  python main.py make "드라마명" 영상1.mp4 ...      # plan + render 한 번에
"""
import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from pipeline import batch, plan, rank, render  # noqa: E402

TTS = ["elevenlabs", "edge"]
LLM = ["auto", "claude", "gpt"]


def main():
    ap = argparse.ArgumentParser(description="중국 드라마 해설 숏폼 파이프라인")
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("web", help="대시보드 (순위·미리보기·대본 수정)")
    w.add_argument("--port", type=int, default=8700)
    w.add_argument("--lan", action="store_true", help="같은 Wi-Fi의 폰에서 접속 허용")
    w.add_argument("--remote", action="store_true", help="AWS(auto-cn.hot-news.app)로 역방향 터널 연결")

    sub.add_parser("doctor", help="환경 점검")
    sub.add_parser("voices", help="ElevenLabs 목소리 목록")

    r = sub.add_parser("rank", help="인기 순위 수집")
    r.add_argument("--n", type=int, default=20)
    r.add_argument("--short", action="store_true", help="숏드라마(微短剧) 순위")

    i = sub.add_parser("inbox", help="순위 → input 폴더 생성")
    i.add_argument("--short", action="store_true")
    i.add_argument("--top", type=int, default=10)
    i.add_argument("--file", help="사용할 순위 JSON (기본: 가장 최근)")

    f = sub.add_parser("fetch", help="공식 채널에서 회차 영상 받기")
    f.add_argument("--only", help="폴더 이름 (없으면 input 의 모든 폴더)")
    f.add_argument("--eps", default="1-2", help="회차 (예: 1-2, 1,3)")

    sub.add_parser("catalog", help="공식 채널에 회차가 올라와 있는 드라마 목록")
    k = sub.add_parser("pick", help="카탈로그 작품을 폴더로 추가하고 회차 받기")
    k.add_argument("title_zh")
    k.add_argument("--eps", default="1-2")

    se = sub.add_parser("series", help="시리즈 다음 편 만들기 (N편 = N화)")
    se.add_argument("base", help="input 폴더 이름 (예: D05_미인여)")
    se.add_argument("--part", type=int, help="만들 편 번호 (기본: 다음 편)")
    se.add_argument("--llm", default="auto", choices=LLM)
    se.add_argument("--tts", choices=TTS)
    se.add_argument("--render", action="store_true", help="대본 다음 바로 렌더")

    b = sub.add_parser("batch", help="input 폴더 일괄 처리")
    b.add_argument("--render", action="store_true", help="대본 대신 영상 렌더")
    b.add_argument("--only", help="폴더 이름에 이 글자가 들어간 것만")
    b.add_argument("--llm", default="auto", choices=LLM)
    b.add_argument("--tts", choices=TTS)

    for name in ("plan", "make"):
        p = sub.add_parser(name, help="하이라이트+대본 생성" if name == "plan" else "plan + render")
        p.add_argument("drama")
        p.add_argument("videos", nargs="+")
        p.add_argument("--llm", default="auto", choices=LLM)
        p.add_argument("--name", help="작업 폴더 이름 (같은 드라마로 여러 편 만들 때)")
        p.add_argument("--tts", choices=TTS)

    u = sub.add_parser("upload", help="완성 영상을 틱톡·네이버 클립에 올리기 (auto-insta 계정)")
    u.add_argument("workdir")
    u.add_argument("--to", required=True, choices=["tiktok", "clip"])
    u.add_argument("--private", action="store_true", help="틱톡 '나만 보기'로 올리기")
    u.add_argument("--dry-run", action="store_true", help="틱톡: 게시 버튼 직전까지만")

    rs = sub.add_parser("research", help="다른 사람들이 많이 본 장면 조사 (유튜브 조회수 + 웨이보·더우인 화제)")
    rs.add_argument("base", help="input 폴더 이름")

    c = sub.add_parser("cleanup", help="다 올린 지 오래된 원본 회차 지우기")
    c.add_argument("--dry-run", action="store_true", help="지울 대상만 보여주기")

    d = sub.add_parser("render", help="plan.json → 영상")
    d.add_argument("workdir")
    d.add_argument("--tts", choices=TTS)

    a = ap.parse_args()
    if a.cmd == "web":
        from pipeline import web
        web.run(port=a.port, lan=a.lan, remote=a.remote)
    elif a.cmd == "doctor":
        batch.doctor()
    elif a.cmd == "voices":
        batch.voices()
    elif a.cmd == "rank":
        rank.run(n=a.n, short=a.short)
    elif a.cmd == "inbox":
        batch.inbox(rank_file=a.file, short=a.short, top=a.top)
    elif a.cmd == "fetch":
        from pipeline import fetch
        fetch.fetch_many(only=a.only, eps=a.eps)
    elif a.cmd == "catalog":
        from pipeline import fetch
        fetch.catalog()
    elif a.cmd == "pick":
        from pipeline import fetch
        fetch.pick(a.title_zh, eps=a.eps)
    elif a.cmd == "series":
        from pipeline import series
        wdir = series.make(a.base, part=a.part, engine=a.llm, tts_engine=a.tts)
        if a.render:
            render.run(wdir, tts_engine=a.tts)
    elif a.cmd == "batch":
        batch.batch(do_render=a.render, engine=a.llm, tts_engine=a.tts, only=a.only)
    elif a.cmd == "plan":
        plan.run(a.drama, a.videos, engine=a.llm, name=a.name, tts_engine=a.tts)
    elif a.cmd == "upload":
        from pipeline import upload
        upload.run(a.workdir, a.to, private=a.private, dry_run=a.dry_run)
    elif a.cmd == "research":
        from pipeline import research
        print(research.prompt_text(research.get(a.base, force=True)))
    elif a.cmd == "cleanup":
        from pipeline import cleanup
        cleanup.run(dry_run=a.dry_run)
    elif a.cmd == "render":
        render.run(a.workdir, tts_engine=a.tts)
    elif a.cmd == "make":
        render.run(plan.run(a.drama, a.videos, engine=a.llm, name=a.name, tts_engine=a.tts), tts_engine=a.tts)


if __name__ == "__main__":
    main()
