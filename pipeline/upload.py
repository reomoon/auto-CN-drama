"""완성 쇼츠를 틱톡·네이버 클립에 올린다 (auto-insta 의 업로드 코드와 로그인 계정을 그대로 사용).

  python main.py upload work\\D05_미인여 --to tiktok [--private] [--dry-run]
  python main.py upload work\\D05_미인여 --to clip
"""
import json
import subprocess
import tempfile
import time
from pathlib import Path

from . import config

BRIDGE = Path(__file__).with_name("upload_bridge.py")
NAMES = {"tiktok": "틱톡", "clip": "네이버 클립"}


def _auto_insta():
    d = Path(config.AUTO_INSTA_DIR)
    py = d / ".venv" / "Scripts" / "python.exe"
    if not (d / "tools" / "tiktok_upload.py").exists() or not py.exists():
        raise RuntimeError(f"auto-insta 를 찾지 못했습니다: {d} (.env 의 AUTO_INSTA_DIR 확인)")
    return d, py


def clip_tags(tags):
    """네이버 클립 전용 해시태그(CLIP_TAGS)를 뒤에 붙인다 (이미 있으면 그대로)."""
    extra = [t for t in config.CLIP_TAGS.split() if t and t not in tags]
    return tags + extra


def upload_text(plan, to=None):
    """설명: 제목 + 캡션 + 출처 + 해시태그. 네이버 클립이면 클립 전용 태그를 더한다."""
    credits = plan.get("credits") or []
    src = ""
    if credits:
        ch = credits[0]["channel"].split(" - Get")[0].split("-Get")[0].strip()
        src = f"\n원본: 〈{plan['drama']}〉 {ch} 공식 채널"
    from .plan import normalize_tags
    tags = normalize_tags(plan.get("hashtags"), plan["drama"])
    tags = " ".join(clip_tags(tags) if to == "clip" else tags)
    return f"{plan.get('title', '').strip()}\n{plan.get('caption', '').strip()}{src}\n\n{tags}".strip()


def run(wdir, to, private=False, dry_run=False):
    wdir = Path(wdir)
    name = wdir.name
    video = config.OUT / name / f"{name}.mp4"
    plan = json.loads((wdir / "plan.json").read_text(encoding="utf-8"))
    if not video.exists():
        raise FileNotFoundError(f"완성 영상이 없습니다: {video} (먼저 렌더하세요)")
    if (status := _status(name)).get("claim"):
        raise RuntimeError("저작권 신고가 체크된 작품입니다. 확인 후 대시보드에서 체크를 풀고 올려주세요.")
    d, py = _auto_insta()

    tmp = Path(tempfile.mkdtemp(prefix="cn_upload_"))
    req = {"platform": to, "video": str(video.resolve()), "text": upload_text(plan, to), "private": private,
           "dry_run": dry_run, "ai_label": config.UPLOAD_AI_LABEL, "clip_account": config.CLIP_ACCOUNT,
           "clip_category": config.CLIP_CATEGORY, "result": str(tmp / "result.json")}
    (tmp / "req.json").write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")

    print(f"{NAMES[to]}에 올립니다: {plan.get('title', name)}" + (" ('나만 보기')" if private else "")
          + (" (시험: 게시 직전까지)" if dry_run else ""))
    print("이 PC에 업로드 창이 새로 열립니다. 로봇 확인이 뜨면 그 창에서 직접 풀어 주세요.")
    # 새 콘솔 창으로 띄워서 퍼즐·확인 질문에 직접 답할 수 있게 한다
    proc = subprocess.Popen([str(py), str(BRIDGE), str(tmp / "req.json")], cwd=d,
                            creationflags=subprocess.CREATE_NEW_CONSOLE)
    started = time.time()
    while proc.poll() is None:
        time.sleep(5)
        if int(time.time() - started) % 60 < 5:
            print(f"  업로드 진행 중... ({int(time.time() - started) // 60}분)", flush=True)
    res = json.loads((tmp / "result.json").read_text(encoding="utf-8")) if (tmp / "result.json").exists() \
        else {"posted": False, "error": "업로드 창이 결과 없이 닫혔습니다."}

    if res.get("posted") and not dry_run:
        status = _status(name)
        status[to] = time.strftime("%Y-%m-%d %H:%M")
        status.setdefault("uploads", []).append({"to": to, "at": status[to], "private": private})
        (config.OUT / name / "status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        print(f"\n{NAMES[to]}에 게시됐습니다. 업로드 완료로 기록했습니다.")
        return True
    print(f"\n게시하지 않았습니다: {res.get('error') or '확인되지 않음'}")
    if not dry_run:
        raise SystemExit(1)
    return False


def _status(name):
    p = config.OUT / name / "status.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
