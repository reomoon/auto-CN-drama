"""중국 드라마 해설 숏폼 파이프라인

  python main.py web [--lan]                    # 대시보드 http://127.0.0.1:8700
  python main.py doctor                         # 환경 점검
  python main.py voices                         # ElevenLabs 목소리 목록
  python main.py rank [--short] [--n 20]        # 인기 순위
  python main.py inbox [--short] [--top 10]     # 순위 → input\드라마별 폴더
  python main.py batch                          # 영상 들어온 폴더 → 대본(plan.md)
  python main.py batch --render                 # 대본 확인 끝난 폴더 → 영상

  python main.py plan "드라마명" 영상1.mp4 [...] [--llm auto|claude|gpt] [--name 폴더명]
  python main.py render work\\폴더명 [--tts elevenlabs|edge]
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

    sub.add_parser("doctor", help="환경 점검")
    sub.add_parser("voices", help="ElevenLabs 목소리 목록")

    r = sub.add_parser("rank", help="인기 순위 수집")
    r.add_argument("--n", type=int, default=20)
    r.add_argument("--short", action="store_true", help="숏드라마(微短剧) 순위")

    i = sub.add_parser("inbox", help="순위 → input 폴더 생성")
    i.add_argument("--short", action="store_true")
    i.add_argument("--top", type=int, default=10)
    i.add_argument("--file", help="사용할 순위 JSON (기본: 가장 최근)")

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

    d = sub.add_parser("render", help="plan.json → 영상")
    d.add_argument("workdir")
    d.add_argument("--tts", choices=TTS)

    a = ap.parse_args()
    if a.cmd == "web":
        from pipeline import web
        web.run(port=a.port, lan=a.lan)
    elif a.cmd == "doctor":
        batch.doctor()
    elif a.cmd == "voices":
        batch.voices()
    elif a.cmd == "rank":
        rank.run(n=a.n, short=a.short)
    elif a.cmd == "inbox":
        batch.inbox(rank_file=a.file, short=a.short, top=a.top)
    elif a.cmd == "batch":
        batch.batch(do_render=a.render, engine=a.llm, tts_engine=a.tts, only=a.only)
    elif a.cmd == "plan":
        plan.run(a.drama, a.videos, engine=a.llm, name=a.name, tts_engine=a.tts)
    elif a.cmd == "render":
        render.run(a.workdir, tts_engine=a.tts)
    elif a.cmd == "make":
        render.run(plan.run(a.drama, a.videos, engine=a.llm, name=a.name, tts_engine=a.tts), tts_engine=a.tts)


if __name__ == "__main__":
    main()
