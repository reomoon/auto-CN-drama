"""로컬 대시보드: 순위 보기, 영상 미리보기, 대본 수정, 대본 생성/렌더 실행."""
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file

from . import config, tts
from . import plan as planmod
from .batch import INPUT, VIDEO_EXT, _videos

app = Flask(__name__, static_folder=None)
WEB = config.ROOT / "web"
LOGS = config.WORK / "_jobs"


def _read(p, default=None):
    p = Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _name(name):
    if not name or name.startswith((".", "_")) or re.search(r'[\\/:*?"<>|]', name) or ".." in name:
        abort(400, "잘못된 폴더 이름")
    return name


# ---------------- 작업 큐 (GPU 하나라 한 번에 하나씩) ----------------
class Jobs:
    def __init__(self):
        self.jobs, self.cv = [], threading.Condition()
        LOGS.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._worker, daemon=True).start()

    def add(self, label, args, target=None, before=None):
        job = {"id": uuid.uuid4().hex[:8], "label": label, "args": args, "target": target,
               "status": "queued", "created": time.time(), "started": None, "ended": None}
        job["log"] = str(LOGS / f"{job['id']}.log")
        with self.cv:
            self.jobs.append((job, before))
            self.jobs = self.jobs[-40:]
            self.cv.notify()
        return job

    def _worker(self):
        while True:
            with self.cv:
                nxt = None
                while nxt is None:
                    nxt = next(((j, b) for j, b in self.jobs if j["status"] == "queued"), None)
                    if nxt is None:
                        self.cv.wait()
            job, before = nxt
            job["status"], job["started"] = "running", time.time()
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
            with open(job["log"], "w", encoding="utf-8") as f:
                try:
                    if before:
                        before()
                    rc = subprocess.run([sys.executable, "-u", str(config.ROOT / "main.py"), *job["args"]],
                                        cwd=config.ROOT, stdout=f, stderr=subprocess.STDOUT, env=env).returncode
                except Exception as e:  # noqa: BLE001
                    f.write(f"\n실행 오류: {e}\n")
                    rc = 1
            job["status"], job["ended"] = ("done" if rc == 0 else "failed"), time.time()

    def busy_targets(self):
        return {j["target"] for j, _ in self.jobs if j["status"] in ("queued", "running") and j["target"]}

    def snapshot(self):
        out = []
        for j, _ in reversed(self.jobs):
            log = Path(j["log"])
            tail = log.read_text(encoding="utf-8", errors="replace")[-3000:] if log.exists() else ""
            tail = "\n".join(x for x in tail.splitlines() if "UserWarning" not in x and "warnings.warn" not in x)
            out.append({k: j[k] for k in ("id", "label", "target", "status", "created", "started", "ended")} |
                       {"log": tail})
        return out


JOBS = Jobs()


# ---------------- 데이터 ----------------
def item(name):
    d_in, wdir, odir = INPUT / name, config.WORK / name, config.OUT / name
    video = odir / f"{name}.mp4"
    info = _read(d_in / "info.json", {})
    p = _read(wdir / "plan.json")
    st = _read(odir / "status.json", {})
    vids = _videos(d_in) if d_in.is_dir() else []
    if st.get("claim"):
        stage = "claimed"
    elif video.exists() and (st.get("tiktok") or st.get("clip")):
        stage = "uploaded"
    elif video.exists():
        stage = "video"
    elif p:
        stage = "plan"
    elif vids:
        stage = "ready"
    else:
        stage = "empty"
    return {
        "name": name,
        "drama": (p or {}).get("drama") or info.get("title_ko") or re.sub(r"^[A-Z]\d+_", "", name).replace("_", " "),
        "info": info,
        "inputs": [{"name": v.name, "mb": round(v.stat().st_size / 2**20)} for v in vids],
        "has_input_dir": d_in.is_dir(),
        "stage": stage,
        "busy": name in JOBS.busy_targets(),
        "plan": {k: p.get(k) for k in ("title", "engine", "_est_total")} if p else None,
        "video": f"/media/{name}?v={int(video.stat().st_mtime)}" if video.exists() else None,
        "post": (odir / "post.txt").read_text(encoding="utf-8") if (odir / "post.txt").exists() else None,
        "status": st,
        "mtime": max([x.stat().st_mtime for x in (d_in, wdir, video) if x.exists()] or [0]),
    }


def all_names():
    names = set()
    for base in (INPUT, config.WORK):
        if base.exists():
            names |= {d.name for d in base.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))}
    return sorted(names)


def plan_view(name):
    wdir = config.WORK / name
    p = _read(wdir / "plan.json")
    lines = _read(wdir / "lines.json")
    if not p or not lines:
        abort(404)
    by_id = {x["id"]: x for x in lines}
    planmod.NARR_CPS = tts.chars_per_sec()
    marks = (_read(config.OUT / name / "timeline.json") or {}).get("items") or []
    rows, t = [], 0.0
    for i, it in enumerate(p["timeline"]):
        a, b = by_id[it["from"]], by_id[it["to"]]
        sec, _ = planmod.item_seconds(it, by_id)
        rng = range(int(it["from"][1:]), int(it["to"][1:]) + 1)
        rows.append({
            "narration": it.get("narration") or "", "role": it.get("role") or "",
            "src": a["src"], "from": it["from"], "to": it["to"],
            "src_start": a["start"], "src_end": b["end"], "est": round(sec, 1),
            "at": marks[i][0] if i < len(marks) else round(t, 1),
            "lines": [{"id": f"L{n}", "zh": by_id[f"L{n}"]["text"], "ko": (it.get("subs") or {}).get(f"L{n}", ""),
                       "energy": by_id[f"L{n}"].get("energy", 0)} for n in rng if f"L{n}" in by_id],
        })
        t += sec
    return {"title": p.get("title", ""), "overlay_title": p.get("overlay_title") or [],
            "caption": p.get("caption", ""), "hashtags": p.get("hashtags") or [], "opinion": p.get("opinion", ""),
            "engine": p.get("engine"), "est_total": round(t, 1), "cps": planmod.NARR_CPS,
            "story": p.get("story", ""), "source_total": p.get("_source_total"),
            "rules": {"narr_min": config.NARR_RATIO_MIN, "narr_max": config.NARR_RATIO_MAX,
                      "src_max": config.MAX_SOURCE_RATIO,
                      "analysis_min": config.MIN_ANALYSIS},
            "rendered": bool(marks), "timeline": rows}


# ---------------- 라우트 ----------------
@app.get("/")
def index():
    return send_file(WEB / "index.html")


@app.get("/api/config")
def api_config():
    return jsonify({"eleven": bool(config.ELEVEN_KEY and config.ELEVEN_VOICE), "tts": config.TTS_ENGINE,
                    "min": config.TARGET_MIN_SEC, "max": config.MAX_SEC})


@app.get("/api/rank")
def api_rank():
    out = {}
    for key, pat, prefix in (("short", "rank_short_*.json", "S"), ("long", "rank_2*.json", "D")):
        files = sorted(config.DATA.glob(pat))
        if not files:
            out[key] = {"date": None, "items": []}
            continue
        data = _read(files[-1], [])
        for i, x in enumerate(data, 1):
            folder = f"{prefix}{i:02d}_{planmod.slugify(x.get('title_ko') or x.get('title_zh', ''))}"
            x["folder"] = folder if (INPUT / folder).is_dir() else None
            x["stage"] = item(folder)["stage"] if x["folder"] else None
        out[key] = {"date": files[-1].stem.split("_")[-1], "items": data}
    return jsonify(out)


@app.get("/api/catalog")
def api_catalog():
    files = sorted(config.DATA.glob("official_*.json"))
    if not files:
        return jsonify({"date": None, "items": []})
    rows = _read(files[-1], [])
    folders = {}
    for d in (INPUT.iterdir() if INPUT.exists() else []):
        zh = (_read(d / "info.json", {}) or {}).get("title_zh")
        if d.is_dir() and zh:
            folders[zh] = d.name
    from .fetch import _norm
    norm_folders = {_norm(k): v for k, v in folders.items()}
    for x in rows:
        x["folder"] = norm_folders.get(_norm(x["title_zh"]))
        x["stage"] = item(x["folder"])["stage"] if x["folder"] else None
    return jsonify({"date": files[-1].stem.split("_")[-1], "items": rows})


@app.get("/api/items")
def api_items():
    items = [item(n) for n in all_names()]
    return jsonify(sorted(items, key=lambda x: (x["stage"] == "empty", -x["mtime"])))


@app.get("/api/items/<name>")
def api_item(name):
    return jsonify(item(_name(name)))


@app.get("/api/plan/<name>")
def api_plan(name):
    return jsonify(plan_view(_name(name)))


@app.post("/api/plan/<name>")
def api_plan_save(name):
    name = _name(name)
    wdir = config.WORK / name
    p, lines = _read(wdir / "plan.json"), _read(wdir / "lines.json")
    if not p:
        abort(404)
    by_id = {x["id"]: x for x in lines}
    body = request.get_json(force=True)
    for k in ("title", "caption", "opinion"):
        if isinstance(body.get(k), str):
            p[k] = body[k].strip()
    if isinstance(body.get("overlay_title"), list):
        p["overlay_title"] = [s.strip() for s in body["overlay_title"] if s and s.strip()]
    if isinstance(body.get("hashtags"), list):
        p["hashtags"] = [("#" + h.lstrip("#")) for h in (s.strip() for s in body["hashtags"]) if h]
    edits = body.get("timeline")
    if isinstance(edits, list) and len(edits) == len(p["timeline"]):
        new_tl = []
        for it, ed in zip(p["timeline"], edits):
            if ed.get("remove"):
                continue
            if it.get("narration"):
                it["narration"] = (ed.get("narration") or "").strip() or it["narration"]
            elif isinstance(ed.get("subs"), dict):
                it["subs"] = {k: v.strip() for k, v in ed["subs"].items() if v and v.strip()}
            new_tl.append(it)
        if not new_tl:
            abort(400, "항목이 하나도 없습니다")
        p["timeline"] = new_tl
    planmod.NARR_CPS = tts.chars_per_sec()
    s = [planmod.item_seconds(it, by_id) for it in p["timeline"]]
    p["_est_total"], p["_est_narr"] = round(sum(x[0] for x in s), 1), round(sum(x[1] for x in s), 1)
    (wdir / "plan.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    planmod.write_review(p, by_id, wdir / "plan.md")
    return jsonify(plan_view(name))


@app.post("/api/status/<name>")
def api_status(name):
    name = _name(name)
    odir = config.OUT / name
    if not odir.exists():
        abort(404)
    st = _read(odir / "status.json", {})
    for k in ("tiktok", "clip", "claim"):
        if k in (request.get_json(force=True) or {}):
            st[k] = time.strftime("%Y-%m-%d %H:%M") if request.json[k] else None
    (odir / "status.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    return jsonify(item(name))


@app.post("/api/folder")
def api_folder():
    drama = (request.get_json(force=True) or {}).get("drama", "").strip()
    if not drama:
        abort(400, "드라마 이름을 입력하세요")
    n = 1 + max([int(m.group(1)) for d in INPUT.glob("M*_*") if (m := re.match(r"M(\d+)_", d.name))] or [0])
    d = INPUT / f"M{n:02d}_{planmod.slugify(drama)}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "info.json").write_text(json.dumps({"title_ko": drama}, ensure_ascii=False), encoding="utf-8")
    return jsonify(item(d.name))


@app.post("/api/upload/<name>")
def api_upload(name):
    name = _name(name)
    d = INPUT / name
    d.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in request.files.getlist("files"):
        fn = re.sub(r'[\\/:*?"<>|]', "_", Path(f.filename or "").name)
        if Path(fn).suffix.lower() not in VIDEO_EXT:
            continue
        f.save(d / fn)
        saved.append(fn)
    if not saved:
        abort(400, "영상 파일(mp4, mkv 등)만 올릴 수 있습니다")
    return jsonify(item(name))


@app.post("/api/job")
def api_job():
    b = request.get_json(force=True) or {}
    cmd, name, short = b.get("cmd"), b.get("name"), bool(b.get("short"))
    kind = "숏드라마" if short else "장편"
    if cmd == "rank":
        job = JOBS.add(f"{kind} 순위 새로고침", ["rank", "--n", "20"] + (["--short"] if short else []))
    elif cmd == "inbox":
        job = JOBS.add(f"{kind} 폴더 만들기", ["inbox"] + (["--short"] if short else []))
    elif cmd == "catalog":
        job = JOBS.add("공식 영상 목록 새로고침", ["catalog"])
    elif cmd == "pick":
        zh = str(b.get("title_zh") or "").strip()
        if not zh or len(zh) > 40:
            abort(400, "작품 제목이 없습니다")
        eps = b.get("eps") if re.fullmatch(r"[\d,\-]{1,20}", str(b.get("eps", ""))) else "1-2"
        job = JOBS.add(f"공식 영상 가져오기 · {zh}", ["pick", zh, "--eps", eps])
    elif cmd == "fetch":
        name = _name(name)
        eps = b.get("eps") if re.fullmatch(r"[\d,\-]{1,20}", str(b.get("eps", ""))) else "1-2"
        job = JOBS.add(f"공식 영상 찾기 · {name}", ["fetch", "--only", name, "--eps", eps], target=name)
    elif cmd in ("plan", "replan"):
        name = _name(name)
        llm = b.get("llm") if b.get("llm") in ("claude", "gpt") else "auto"
        before = None
        if cmd == "replan":
            def before(w=config.WORK / name):
                if (w / "plan.json").exists():
                    (w / "plan.json").replace(w / "plan_prev.json")
        job = JOBS.add(f"대본 만들기 · {name}", ["batch", "--only", name, "--llm", llm], target=name, before=before)
    elif cmd == "render":
        name = _name(name)
        t = b.get("tts") if b.get("tts") in ("elevenlabs", "edge") else "edge"
        job = JOBS.add(f"{'최종' if t == 'elevenlabs' else '미리보기'} 렌더 · {name}",
                       ["render", str(config.WORK / name), "--tts", t], target=name)
    else:
        abort(400, "알 수 없는 작업")
    return jsonify(job)


@app.get("/api/jobs")
def api_jobs():
    return jsonify(JOBS.snapshot())


@app.get("/media/<name>")
def media(name):
    name = _name(name)
    p = config.OUT / name / f"{name}.mp4"
    if not p.exists():
        abort(404)
    return send_file(p, mimetype="video/mp4", conditional=True)


def run(port=8700, lan=False):
    host = "0.0.0.0" if lan else "127.0.0.1"
    print(f"대시보드: http://127.0.0.1:{port}")
    if lan:
        import socket
        try:
            ip = socket.gethostbyname(socket.gethostname())
            print(f"같은 Wi-Fi의 폰에서: http://{ip}:{port}")
        except OSError:
            pass
    app.run(host=host, port=port, threaded=True, debug=False)
