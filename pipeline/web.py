"""로컬 대시보드: 순위 보기, 영상 미리보기, 대본 수정, 대본 생성/렌더 실행."""
import datetime as dt
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, request, send_file, session

from . import config, tts
from . import plan as planmod
from .batch import INPUT, VIDEO_EXT, _videos

app = Flask(__name__, static_folder=None)
WEB = config.ROOT / "web"
LOGS = config.WORK / "_jobs"


# ---------------- 로그인 (원격 접속 보호) ----------------
def _secret_key():
    p = config.WORK / ".web_secret"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(secrets.token_hex(32), encoding="utf-8")
    return p.read_text(encoding="utf-8").strip()


app.secret_key = _secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  PERMANENT_SESSION_LIFETIME=dt.timedelta(days=30))
_fails = {}   # ip → [실패 시각들]


def _is_local():
    """이 PC에서 직접 연 경우만 로그인 생략. 터널(Cloudflare)·같은 Wi-Fi 접속은 로그인 필요."""
    host = (request.host or "").split(":")[0].lower()
    proxied = any(request.headers.get(h) for h in ("Cf-Connecting-Ip", "X-Real-Ip", "X-Forwarded-For"))
    return host in ("127.0.0.1", "localhost") and not proxied and request.remote_addr in ("127.0.0.1", "::1")


def _client_ip():
    return (request.headers.get("Cf-Connecting-Ip") or request.headers.get("X-Real-Ip")
            or request.remote_addr or "?")


@app.before_request
def _guard():
    if request.path == "/login" or _is_local() or session.get("ok"):
        return None
    if not config.DASHBOARD_PASSWORD:
        abort(403, "원격 접속이 잠겨 있습니다. .env 에 DASHBOARD_PASSWORD 를 설정하세요.")
    if request.path.startswith(("/api/", "/media/")):
        abort(401)
    return redirect("/login")


LOGIN_HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>중드 숏폼 스튜디오 · 로그인</title>
<style>:root{--bg:#f6f5f2;--panel:#fff;--line:#e6e3dd;--text:#1d1c1a;--muted:#77726a;--accent:#d6402f}
@media (prefers-color-scheme:dark){:root{--bg:#141312;--panel:#1d1c1a;--line:#33312d;--text:#eeebe6;--muted:#9b958b;--accent:#f0624f}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--text);
font:15px/1.5 "Malgun Gothic","Apple SD Gothic Neo",system-ui,sans-serif;padding:16px}
form{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:28px;width:min(360px,100%);display:flex;flex-direction:column;gap:12px}
h1{margin:0 0 4px;font-size:20px}input{font:inherit;color:inherit;border:1px solid var(--line);background:transparent;border-radius:10px;padding:10px 12px}
button{font:inherit;font-weight:700;border:0;border-radius:10px;padding:10px;background:var(--accent);color:#fff;cursor:pointer}
.err{color:var(--accent);font-size:13px;min-height:1em}.muted{color:var(--muted);font-size:13px}</style></head>
<body><form method="post"><h1>중드 숏폼 스튜디오</h1><span class="muted">원격 접속에는 비밀번호가 필요합니다.</span>
<input type="password" name="password" placeholder="비밀번호" autofocus autocomplete="current-password">
<div class="err">{err}</div><button>들어가기</button></form></body></html>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    err = ""
    if request.method == "POST":
        ip, now = _client_ip(), time.time()
        recent = [t for t in _fails.get(ip, []) if now - t < 600]
        if len(recent) >= 8:
            err = "실패가 많아 10분 동안 잠겼습니다."
        elif config.DASHBOARD_PASSWORD and hmac.compare_digest(
                request.form.get("password", "").encode(), config.DASHBOARD_PASSWORD.encode()):
            session.permanent = True
            session["ok"] = True
            _fails.pop(ip, None)
            return redirect("/")
        else:
            time.sleep(1)
            _fails[ip] = recent + [now]
            err = "비밀번호가 맞지 않습니다."
    return LOGIN_HTML.replace("{err}", err)


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


JOBS = Jobs()      # 무거운 작업(GPU·렌더): 한 번에 하나
UPLOADS = Jobs()   # 업로드: 렌더와 따로, 한 번에 하나


def busy_targets():
    return JOBS.busy_targets() | UPLOADS.busy_targets()


# ---------------- 데이터 ----------------
def item(name):
    wdir, odir = config.WORK / name, config.OUT / name
    p = _read(wdir / "plan.json")
    ser = (p or {}).get("series") or {}
    base = ser.get("base") or name
    d_in = INPUT / base
    video = odir / f"{name}.mp4"
    info = _read(d_in / "info.json", {})
    st = _read(odir / "status.json", {})
    vids = _videos(d_in) if d_in.is_dir() else []
    official_eps = _catalog_eps(info.get("title_zh"))
    have = {int(m.group(1)) for v in vids if (m := re.match(r"EP0*(\d+)$", v.stem, re.I))}
    nxt = [e for e in (official_eps or range(1, 200)) if e not in have][:2]
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
        "name": name, "base": base, "part": ser.get("part") or (1 if p else None),
        "drama": (p or {}).get("drama") or info.get("title_ko") or re.sub(r"^[A-Z]\d+_", "", name).replace("_", " "),
        "info": info,
        "inputs": [{"name": v.name, "mb": round(v.stat().st_size / 2**20)} for v in vids],
        "official_eps": official_eps, "have_eps": sorted(have),
        "next_eps": (f"{nxt[0]}-{nxt[-1]}" if len(nxt) > 1 and nxt[-1] == nxt[0] + 1 else ",".join(map(str, nxt))),
        "has_input_dir": d_in.is_dir(),
        "stage": stage,
        "busy": bool({name, base} & busy_targets()),
        "uploading": f"upload:{name}" in UPLOADS.busy_targets(),
        "plan": {k: p.get(k) for k in ("title", "engine", "_est_total")} if p else None,
        "video": f"/media/{name}?v={int(video.stat().st_mtime)}" if video.exists() else None,
        "post": (odir / "post.txt").read_text(encoding="utf-8") if (odir / "post.txt").exists() else None,
        "status": st,
        "mtime": max([x.stat().st_mtime for x in (d_in, wdir, video) if x.exists()] or [0]),
    }


_cat_cache = {"mtime": 0, "map": {}}


def _catalog_eps(title_zh):
    """공식 채널 카탈로그에서 그 작품의 올라와 있는 회차 번호들."""
    if not title_zh:
        return []
    from .fetch import _norm
    cats = sorted(config.DATA.glob("official_*.json"))
    if not cats:
        return []
    m = cats[-1].stat().st_mtime
    if _cat_cache["mtime"] != m:
        _cat_cache.update(mtime=m, map={_norm(x["title_zh"]): x.get("eps") or [] for x in _read(cats[-1], [])})
    return _cat_cache["map"].get(_norm(title_zh), [])


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
    raw = (_read(config.OUT / name / "timeline.json") or {}).get("items") or []
    at_by_idx = {(m[2] if len(m) > 2 else k): m[0] for k, m in enumerate(raw)}
    marks = raw
    rows, t = [], 0.0
    for i, it in enumerate(p["timeline"]):
        a, b = by_id[it["from"]], by_id[it["to"]]
        sec, _ = planmod.item_seconds(it, by_id)
        rng = range(int(it["from"][1:]), int(it["to"][1:]) + 1)
        rows.append({
            "narration": it.get("narration") or "", "role": it.get("role") or "",
            "src": a["src"], "from": it["from"], "to": it["to"],
            "src_start": a["start"], "src_end": b["end"], "est": round(sec, 1),
            "at": at_by_idx.get(i, round(t, 1)), "dropped": bool(marks) and i not in at_by_idx,
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
    r = send_file(WEB / "index.html")
    r.headers["Cache-Control"] = "no-store"   # 서버를 고치면 바로 새 화면이 보이게
    return r


@app.get("/api/config")
def api_config():
    return jsonify({"eleven": bool(config.ELEVEN_KEY and config.ELEVEN_VOICE), "tts": config.TTS_ENGINE,
                    "min": config.TARGET_MIN_SEC, "max": config.MAX_SEC})


@app.get("/api/rank")
def api_rank():
    from .fetch import _norm
    cats = sorted(config.DATA.glob("official_*.json"))
    official = {_norm(x["title_zh"]) for x in _read(cats[-1], [])} if cats else set()
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
            x["official"] = _norm(x.get("title_zh", "")) in official   # 공식 채널 카탈로그에 있으면 받을 수 있음
            if x["folder"] and (INPUT / folder / "info.json").exists():
                x["official"] = x["official"] or bool(_read(INPUT / folder / "info.json", {}).get("official"))
        out[key] = {"date": files[-1].stem.split("_")[-1], "items": data}
    return jsonify(out)


THUMBS = config.DATA / "thumbs"
_thumb_locks = {}


@app.get("/thumb")
def thumb():
    """순위 카드 썸네일. v=유튜브 영상 ID 가 있으면 그 썸네일, 없으면 q=중국어 원제로 검색한 첫 영상 썸네일.
    data/thumbs 에 저장해 두고 다시 쓴다."""
    import hashlib

    import requests
    vid = (request.args.get("v") or "").strip()
    q = (request.args.get("q") or "").strip()[:60]
    if not (vid or q) or (vid and not re.fullmatch(r"[\w-]{6,20}", vid)):
        abort(400)
    key = hashlib.sha1((vid or q).encode()).hexdigest()[:16]
    THUMBS.mkdir(parents=True, exist_ok=True)
    img, none = THUMBS / f"{key}.jpg", THUMBS / f"{key}.none"
    lock = _thumb_locks.setdefault(key, threading.Lock())
    with lock:
        if not img.exists() and not none.exists():
            url = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else None
            try:
                if not url:
                    import yt_dlp
                    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as y:
                        ents = y.extract_info(f"ytsearch5:{q}", download=False).get("entries") or []
                    from .fetch import _norm
                    ent = next((e for e in ents if e and _norm(q) in _norm(e.get("title"))), None) \
                        or next((e for e in ents if e), None)
                    if ent:
                        url = f"https://i.ytimg.com/vi/{ent['id']}/hqdefault.jpg"
                if url:
                    r = requests.get(url, timeout=15)
                    if r.ok and len(r.content) > 2000:
                        img.write_bytes(r.content)
            except Exception:  # noqa: BLE001
                pass
            if not img.exists():
                none.write_text("", encoding="utf-8")   # 못 찾음 — 다시 검색하지 않음
    if not img.exists():
        abort(404)
    r = send_file(img, mimetype="image/jpeg", conditional=True)
    r.headers["Cache-Control"] = "private, max-age=604800"
    return r


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
    elif cmd == "upload":
        name = _name(name)
        to = b.get("to")
        if to not in ("tiktok", "clip"):
            abort(400, "올릴 곳(tiktok/clip)을 고르세요")
        args = ["upload", str(config.WORK / name), "--to", to]
        label = {"tiktok": "틱톡", "clip": "네이버 클립"}[to]
        if to == "tiktok" and b.get("private"):
            args.append("--private")
            label += " (나만 보기)"
        if to == "tiktok" and b.get("dry_run"):
            args.append("--dry-run")
            label += " (시험)"
        job = UPLOADS.add(f"{label}에 올리기 · {name}", args, target=f"upload:{name}")
        return jsonify(job)
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
    elif cmd == "series":
        base = _name(name)
        llm = b.get("llm") if b.get("llm") in ("claude", "gpt") else "auto"
        job = JOBS.add(f"다음 편 만들기 · {base}", ["series", base, "--llm", llm], target=base)
    elif cmd in ("plan", "replan"):
        name = _name(name)
        llm = b.get("llm") if b.get("llm") in ("claude", "gpt") else "auto"
        before = None
        if cmd == "replan":
            def before(w=config.WORK / name):
                if (w / "plan.json").exists():
                    (w / "plan.json").replace(w / "plan_prev.json")
        ser = (_read(config.WORK / name / "plan.json") or {}).get("series")
        if ser:
            args = ["series", ser["base"], "--part", str(ser["part"]), "--llm", llm]
        elif (INPUT / name).is_dir():   # 새 작품도 시리즈 1편으로 (마지막에 다음 편 예고)
            args = ["series", name, "--part", "1", "--llm", llm]
        else:
            args = ["batch", "--only", name, "--llm", llm]
        job = JOBS.add(f"대본 만들기 · {name}", args, target=name, before=before)
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
    jobs = sorted(JOBS.snapshot() + UPLOADS.snapshot(), key=lambda j: -j["created"])
    return jsonify(jobs[:40])


@app.get("/media/<name>")
def media(name):
    name = _name(name)
    p = config.OUT / name / f"{name}.mp4"
    if not p.exists():
        abort(404)
    return send_file(p, mimetype="video/mp4", conditional=True)


def _cleanup_loop():
    """켜질 때, 그리고 6시간마다 다 올린 지 오래된 원본 회차를 지운다."""
    from . import cleanup
    while True:
        try:
            if not busy_targets():          # 작업 중에는 건너뛴다
                cleanup.run(quiet=True)
        except Exception as e:  # noqa: BLE001
            print(f"[정리] 오류: {e}")
        time.sleep(6 * 3600)


def run(port=8700, lan=False, remote=False):
    host = "0.0.0.0" if lan else "127.0.0.1"
    print(f"대시보드: http://127.0.0.1:{port}")
    threading.Thread(target=_cleanup_loop, daemon=True).start()
    if remote:
        if not config.DASHBOARD_PASSWORD:
            raise RuntimeError("원격 접속에는 .env 의 DASHBOARD_PASSWORD 가 필요합니다.")
        from . import remote as remote_mod
        remote_mod.start(port)
    if lan:
        import socket
        try:
            ip = socket.gethostbyname(socket.gethostname())
            print(f"같은 Wi-Fi의 폰에서: http://{ip}:{port}")
        except OSError:
            pass
    app.run(host=host, port=port, threaded=True, debug=False)
