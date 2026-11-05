"""중국 드라마 해설 숏폼 파이프라인

  python main.py rank [--short] [--n 20]
  python main.py plan "드라마명" 영상1.mp4 [영상2.mp4 ...] [--llm auto|claude|gpt] [--name 폴더명]
  python main.py render work/폴더명 [--tts elevenlabs|edge]
  python main.py make "드라마명" 영상1.mp4 ...      # plan + render 한 번에
"""
import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from pipeline import plan, rank, render  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="중국 드라마 해설 숏폼 파이프라인")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("rank", help="인기 순위 수집")
    r.add_argument("--n", type=int, default=20)
    r.add_argument("--short", action="store_true", help="숏드라마(微短剧) 순위")

    for name in ("plan", "make"):
        p = sub.add_parser(name, help="하이라이트+대본 생성" if name == "plan" else "plan + render")
        p.add_argument("drama")
        p.add_argument("videos", nargs="+")
        p.add_argument("--llm", default="auto", choices=["auto", "claude", "gpt"])
        p.add_argument("--name", help="작업 폴더 이름 (같은 드라마로 여러 편 만들 때)")
        if name == "make":
            p.add_argument("--tts", choices=["elevenlabs", "edge"])

    d = sub.add_parser("render", help="plan.json → 영상")
    d.add_argument("workdir")
    d.add_argument("--tts", choices=["elevenlabs", "edge"])

    a = ap.parse_args()
    if a.cmd == "rank":
        rank.run(n=a.n, short=a.short)
    elif a.cmd == "plan":
        plan.run(a.drama, a.videos, engine=a.llm, name=a.name)
    elif a.cmd == "render":
        render.run(a.workdir, tts_engine=a.tts)
    elif a.cmd == "make":
        render.run(plan.run(a.drama, a.videos, engine=a.llm, name=a.name, tts_engine=a.tts), tts_engine=a.tts)


if __name__ == "__main__":
    main()
