"""auto-insta 의 업로드 코드(tools/tiktok_upload.py, tools/naver_clip_upload.py)를 그대로 불러 영상 하나를 올린다.

auto-insta 의 가상환경 파이썬으로, auto-insta 폴더를 작업 폴더로 해서 실행된다 (pipeline/upload.py 가 띄움).
같은 Chrome 프로필(로그인 유지)을 쓰므로 계정·로그인·자동 조작 대응이 auto-insta 와 똑같다.

  python upload_bridge.py <요청 JSON 경로>
요청: {"platform": "tiktok"|"clip", "video": 경로, "text": 설명, "private": bool, "dry_run": bool,
       "ai_label": bool, "clip_account": "docu", "clip_category": "이슈", "result": 결과 JSON 경로}
"""
import json
import sys
import traceback
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path.cwd()))   # auto-insta 폴더 (tools 패키지)

from playwright.sync_api import sync_playwright  # noqa: E402


def main():
    req = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    result = {"ok": False, "posted": False, "error": None}
    try:
        with sync_playwright() as p:
            if req["platform"] == "tiktok":
                from tools import tiktok_upload as tt
                context = tt.open_browser(p)
                page = context.pages[0] if context.pages else context.new_page()
                print("틱톡에 올립니다. 로봇 확인(퍼즐)이 뜨면 열린 창에서 직접 풀어 주세요.")
                posted = tt.upload_one(page, Path(req["video"]), req["text"], private=req.get("private", False),
                                       dry_run=req.get("dry_run", False), ai_label=req.get("ai_label", True))
                if req.get("dry_run"):
                    print("(시험) 게시 직전에서 멈췄습니다. 창에서 확인하고 닫으세요.")
                    page.wait_for_event("close", timeout=0)
                result.update(ok=True, posted=bool(posted))
                context.close()
            else:
                from tools import naver_clip_upload as nc
                account = req.get("clip_account", "docu")
                context = nc.open_browser(p, account)
                page = context.pages[0] if context.pages else context.new_page()
                print(f"네이버 클립({account} 계정)에 올립니다.")
                if req.get("dry_run"):
                    raise SystemExit("네이버 클립은 시험 올리기를 지원하지 않습니다.")
                nc.upload_one(page, Path(req["video"]), req["text"], req.get("clip_category", "이슈"),
                              ai_label=req.get("ai_label", True), account=account)
                result.update(ok=True, posted=True)
                context.close()
    except SystemExit as e:
        result["error"] = str(e)
        print(f"\n중단: {e}")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        result["error"] = f"{type(e).__name__}: {e}"
        if "user data directory is already in use" in str(e) or "ProcessSingleton" in str(e):
            result["error"] = "같은 Chrome 프로필이 이미 열려 있습니다(auto-insta 업로드 창 등). 닫고 다시 시도하세요."
    Path(req["result"]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print("\n결과:", "게시됨" if result["posted"] else ("오류: " + str(result["error"]) if result["error"] else "게시 안 함"))
    if not result["posted"]:
        try:
            input("\nEnter 를 누르면 창이 닫힙니다.")
        except EOFError:
            pass


if __name__ == "__main__":
    main()
