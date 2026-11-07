"""plan.json → 1080x1920 숏폼 (나레이션 + 자막) + 업로드 문구.

템플릿
  bold    : 상단 검은 띠에 굵은 제목(노랑/형광초록), 영상은 제목 아래부터 크게, 굵은 노란 해설 자막 (기본)
  classic : 가운데 영상 + 위아래 검은 여백, 제목 흰색/분홍, 나레이션은 아래 여백
"""
import json
import re
import shutil
from pathlib import Path

from . import config, media, tts

FONTS = config.ROOT / "fonts"


# ---------------- 자막(ASS) ----------------
def ass_time(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def ass_text(s):
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def wrap(s, width=13):
    """한 줄이 길면 가운데에 가까운 띄어쓰기에서 두 줄로 나눈다."""
    s = s.strip()
    if len(s) <= width or " " not in s:
        return s
    mid = len(s) / 2
    cut = min((i for i, c in enumerate(s) if c == " "), key=lambda i: abs(i - mid))
    return s[:cut] + r"\N" + s[cut + 1:]


def chunk_narration(text, maxlen=24):
    parts = [p for p in re.split(r"(?<=[.?!…~,])\s+", text.strip()) if p]
    out = []
    for p in parts:
        if len(p) <= maxlen:
            out.append(p)
            continue
        cur = ""
        for w in p.split(" "):
            if cur and len(cur) + 1 + len(w) > maxlen:
                out.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            out.append(cur)
    return out


COLORS = {"pink": "FF4F8B", "yellow": "FFD93B", "white": "FFFFFF", "red": "FF3B3B", "mint": "3BFFC8",
          "neon": "FFF200", "green": "3DFF2E"}


def ass_color(name):
    """'pink' / '#FF4F8B' → ASS 색(&H00BBGGRR)."""
    h = COLORS.get(str(name).lower(), str(name).lstrip("#"))
    h = h if re.fullmatch(r"[0-9A-Fa-f]{6}", h) else COLORS["yellow"]
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


# ---------------- 레이아웃 ----------------
BOLD_TOP = 450   # bold 템플릿: 제목 띠 높이


def layout(width, height):
    """템플릿별 원본 화면 위치와 자막 위치."""
    vertical = height / width > 1.2
    if config.TEMPLATE == "bold":
        # 제목 아래 영역의 BOLD_FILL 이상을 채우도록 확대 (극장 비율 원본도 아래 여백이 너무 남지 않게)
        need = config.BOLD_FILL * (1920 - BOLD_TOP) * width / (1080 * height)
        z = 1.0 if vertical else min(config.BOLD_MAX_ZOOM, max(config.BOLD_ZOOM, need))
        fg_h = min(1920 - BOLD_TOP, 1080 * z * height / width)
        bottom = BOLD_TOP + fg_h
        dlg = min(bottom - 28, 1540)              # 대사 자막: 영상 맨 아래 한 줄 고정
        narr = dlg - 150                          # 해설 자막: 대사 자리보다 위
        return {"template": "bold", "zoom": z, "pad_y": BOLD_TOP, "max_h": 1920 - BOLD_TOP,
                "title_y": (218, 362), "label_y": BOLD_TOP - 14,   # 제목 2줄, 〈작품명〉 ep 라벨
                "dialog_v": int(1920 - dlg), "narr_align": 2, "narr_v": int(1920 - narr)}

    z = 1.0 if vertical else config.FG_ZOOM
    fg_h = min(1920, 1080 * z * height / width)
    top, bottom = 960 - fg_h / 2, 960 + fg_h / 2
    if bottom >= 1700:   # 세로 원본: 화면을 꽉 채우므로 자막을 아래쪽에 겹쳐 배치
        return {"template": "classic", "zoom": z, "pad_y": None, "max_h": 1920, "title_y": (138, 250),
                "label_y": None, "dialog_v": 700, "narr_align": 2, "narr_v": config.SUB_MARGIN_V}
    ty = max(150, top - 95)
    return {"template": "classic", "zoom": z, "pad_y": None, "max_h": 1920,
            "title_y": (ty - 112, ty),               # 제목 1줄, 2줄 바닥 위치
            "label_y": top - 18,                     # 작품명 | ep 라벨 바닥 위치
            "dialog_v": int(1920 - bottom + 34),     # 대사: 영상 안쪽 아래
            "narr_align": 8, "narr_v": int(bottom + 45)}   # 나레이션: 영상 바로 아래 검은 여백부터


def episode_label(plan, bold=False):
    if bold:
        base = episode_label(plan)
        name, _, ep = base.partition("  | ")
        return f"〈{name}〉" + (f" {ep}" if ep else "")
    eps = sorted({c["ep"] for c in plan.get("credits") or []})
    if not eps:
        for s in plan.get("sources", []):
            m = re.search(r"(?:EP|E|第)\s*0*(\d{1,3})|(\d{1,3})\s*[화集]", Path(s).stem, re.I)
            if m:
                eps.append(int(m.group(1) or m.group(2)))
        eps = sorted(set(eps))
    if not eps:
        return plan["drama"]
    return f"{plan['drama']}  | ep.{eps[0]}" + (f"-{eps[-1]}" if len(eps) > 1 else "")


def _title_size(text, base, width=1000):
    """한 줄이 화면 폭(약 1000px)에 들어가도록 글자 크기를 줄인다."""
    n = sum(0.55 if c in " '’‘\".,!?~" or c.isascii() else 1.0 for c in text) or 1
    return int(min(base, width / (n * 0.98)))


def build_ass(events, overlay, total, path, lay=None, label=None):
    lay = lay or layout(1920, 1080)
    dlg_v, narr_v = lay["dialog_v"], lay["narr_v"]
    if lay["template"] == "bold":
        f = config.TITLE_FONT
        t1, t2 = ass_color(config.BOLD_TITLE1), ass_color(config.BOLD_TITLE2)
        narr_c = ass_color(config.BOLD_NARR)
        styles = (
            f"Style: Title1,{f},136,{t1},{t1},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,5,0,2,20,20,0,1\n"
            f"Style: Title2,{f},136,{t2},{t2},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,5,0,2,20,20,0,1\n"
            f"Style: Label,{f},50,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,3,0,1,44,44,0,1\n"
            f"Style: Narr,{f},92,{narr_c},{narr_c},&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,9,3,2,130,130,{narr_v},1\n"
            f"Style: Dialog,{f},62,&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,0,0,0,0,100,100,0,0,1,5,2,2,60,60,{dlg_v},1\n")
        wrap_w = {"Narr": 9, "Dialog": 18}
        base = 136
    else:
        f = config.FONT
        narr_c, t2_c = ass_color(config.NARR_COLOR), ass_color(config.TITLE2_COLOR)
        styles = (
            f"Style: Title1,{f},96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,-2,0,1,6,0,2,40,40,0,1\n"
            f"Style: Title2,{f},96,{t2_c},{t2_c},&H00000000,&H00000000,-1,0,0,0,100,100,-2,0,1,6,0,2,40,40,0,1\n"
            f"Style: Label,{f},48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,0,1,60,60,0,1\n"
            f"Style: Narr,{f},66,{narr_c},{narr_c},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,5,0,{lay['narr_align']},90,90,{narr_v},1\n"
            f"Style: Dialog,{f},60,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,1,4,2,2,90,90,{dlg_v},1\n")
        wrap_w = {"Narr": 13, "Dialog": 13}
        base = 96
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n"
            "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding\n" + styles +
            "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    rows = []
    lines = [ass_text(x) for x in (overlay or []) if x and x.strip()][:2]
    y1, y2 = lay["title_y"]
    ys = [y1, y2] if len(lines) == 2 else [y2 if lay["template"] == "classic" else (y1 + y2) // 2]
    for k, (txt, y) in enumerate(zip(lines, ys)):
        style = "Title2" if (len(lines) == 2 and k == 1) else "Title1"
        rows.append(f"Dialogue: 2,{ass_time(0)},{ass_time(total)},{style},,0,0,0,,"
                    f"{{\\an2\\pos(540,{y:.0f})\\fs{_title_size(txt, base)}}}{txt}")
    if label and lay.get("label_y"):
        rows.append(f"Dialogue: 2,{ass_time(0)},{ass_time(total)},Label,,0,0,0,,"
                    f"{{\\an1\\pos(60,{lay['label_y']:.0f})}}{ass_text(label)}")
    for st, en, style, text in events:
        if en - st > 0.05:
            rows.append(f"Dialogue: 0,{ass_time(st)},{ass_time(en)},{style},,0,0,0,,"
                        f"{wrap(ass_text(text), wrap_w[style])}")
    Path(path).write_text(head + "\n".join(rows) + "\n", encoding="utf-8")


# ---------------- 영상 ----------------
FACE_MODEL = config.ROOT / "models" / "face_detection_yunet_2023mar.onnx"
_detector = None


def face_center(src, start, seg):
    """구간 안 프레임 3장에서 얼굴을 찾아 가로 중심(0~1)을 돌려준다. 못 찾으면 0.5(가운데)."""
    global _detector
    if not FACE_MODEL.exists():
        return 0.5
    import cv2
    cap = cv2.VideoCapture(src)
    pts = []
    for k in (0.15, 0.5, 0.85):
        cap.set(cv2.CAP_PROP_POS_MSEC, (start + seg * k) * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        W, H = 640, max(1, int(h * 640 / w))
        frame = cv2.resize(frame, (W, H))
        if _detector is None:
            _detector = cv2.FaceDetectorYN.create(str(FACE_MODEL), "", (W, H), 0.7)
        _detector.setInputSize((W, H))
        _, faces = _detector.detect(frame)
        if faces is None or not len(faces):
            continue
        faces = sorted(faces, key=lambda r: -r[2] * r[3])
        big = faces[0]
        cx = (big[0] + big[2] / 2) / W
        # 가장 큰 얼굴과 가까이 있는 얼굴들까지 묶어서 그 무리의 가운데로
        group = [r for r in faces if abs((r[0] + r[2] / 2) / W - cx) < 0.22 and r[2] * r[3] > 0.25 * big[2] * big[3]]
        area = sum(r[2] * r[3] for r in group)
        pts.append((sum((r[0] + r[2] / 2) / W * r[2] * r[3] for r in group) / area, area))
    cap.release()
    if not pts:
        return 0.5
    return float(sum(x * a for x, a in pts) / sum(a for _, a in pts))


def _duck_expr(seg, ramp_in, ramp_out):
    """나레이션 동안 원음을 줄인다. 원본 구간과 맞닿는 쪽은 부드럽게 줄였다 키운다."""
    v, f = config.NARR_ORIG_VOLUME, min(config.DUCK_FADE, seg / 3)
    expr = f"{v}"
    if ramp_out:
        expr = f"if(gt(t,{seg - f:.3f}),{v}+(1-{v})*(t-{seg - f:.3f})/{f:.3f},{expr})"
    if ramp_in:
        expr = f"if(lt(t,{f:.3f}),1-(1-{v})*t/{f:.3f},{expr})"
    return f"volume='{expr}':eval=frame"


def render_segment(src, has_audio, start, seg, narr_mp3, out, lay, ramp_in=False, ramp_out=False):
    z = lay["zoom"]
    args = ["-ss", f"{start:.3f}", "-t", f"{seg:.3f}", "-i", src]
    a_orig = "0:a"
    idx = 1
    if not has_audio:
        args += ["-f", "lavfi", "-t", f"{seg:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        a_orig, idx = "1:a", 2
    ws = int(1080 * z) // 2 * 2
    crop_x = "(iw-ow)/2"
    if ws > 1080 + 8:   # 양옆을 자를 때는 얼굴이 가운데 오도록
        cx = face_center(src, start, seg)
        crop_x = str(int(min(ws - 1080, max(0, cx * ws - 540))))
    fg = f"scale={ws}:-2,crop='min(iw,1080)':'min(ih,{lay['max_h']})':{crop_x}:'(ih-oh)/2'"
    if config.BG_STYLE == "blur" and lay["template"] == "classic":
        fc = (f"[0:v]fps=30,split=2[v1][v2];"
              f"[v1]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=40,"
              f"eq=brightness=-0.12[bg];[v2]{fg}[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v];")
    else:   # 검은 여백
        py = lay["pad_y"] if lay["pad_y"] is not None else "(oh-ih)/2"
        fc = f"[0:v]fps=30,{fg},pad=1080:1920:(ow-iw)/2:{py}:black,setsar=1,format=yuv420p[v];"
    fc += f"[{a_orig}]aresample=48000,aformat=channel_layouts=stereo"
    if narr_mp3:
        fc += f",{_duck_expr(seg, ramp_in, ramp_out)}[oa]"
        args += ["-i", narr_mp3]
        fc += (f";[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,adelay=150|150,volume=1.5[na];"
               f"[oa][na]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        fc += "[a]"
    media.ffmpeg([*args, "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{seg:.3f}",
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", "30",
                  "-c:a", "aac", "-b:a", "192k", "-ar", "48000", out])


def run(wdir, tts_engine=None):
    wdir = Path(wdir)
    plan = json.loads((wdir / "plan.json").read_text(encoding="utf-8"))
    by_id = {x["id"]: x for x in json.loads((wdir / "lines.json").read_text(encoding="utf-8"))}
    sources = plan["sources"]
    info = {s: media.probe(s) for s in sources}
    first = info[sources[0]]
    lay = layout(first["width"], first["height"])
    rdir = wdir / "render"
    rdir.mkdir(exist_ok=True)
    for old in rdir.glob("seg_*.mp4"):
        old.unlink()
    if FONTS.is_dir():   # 굵은 제목체(Black Han Sans)를 자막 합성에 쓰도록 복사
        shutil.copytree(FONTS, rdir / "fonts", dirs_exist_ok=True)
    print(f"  · 템플릿: {lay['template']}")

    events, segs, marks, T = [], [], [], 0.0
    tl = plan["timeline"]
    is_narr = [bool((it.get("narration") or "").strip()) for it in tl]
    for i, it in enumerate(tl, 1):
        a, b = by_id[it["from"]], by_id[it["to"]]
        src = sources[a["src"]]
        sdur = info[src]["duration"]
        start = max(0.0, a["start"] - 0.15)
        clip = min(sdur, b["end"] + 0.25) - start
        narr = (it.get("narration") or "").strip()
        mp3, nd = None, 0.0
        if narr:
            print(f"  · [{i}/{len(tl)}] 나레이션 생성")
            mp3 = tts.synth(narr, wdir / "tts", engine=tts_engine)
            nd = media.duration(mp3)
            tts.record(narr, nd, engine=tts_engine)
        seg = nd + 0.5 if narr else clip
        if start + seg > sdur:
            start = max(0.0, sdur - seg)
            seg = min(seg, sdur)

        print(f"  · [{i}/{len(tl)}] 구간 편집 {seg:.1f}초")
        out = rdir / f"seg_{i:02d}.mp4"
        render_segment(src, info[src]["has_audio"], start, seg, str(mp3) if mp3 else None, str(out), lay,
                       ramp_in=i > 1 and not is_narr[i - 2], ramp_out=i < len(tl) and not is_narr[i])
        segs.append(out.name)
        marks.append([round(T, 2), round(seg, 2)])

        if narr:
            chunks = chunk_narration(narr, maxlen=18 if lay["template"] == "bold" else 24)
            total_chars = sum(len(c) for c in chunks) or 1
            t = T + 0.15
            for c in chunks:
                d = nd * len(c) / total_chars
                events.append((t, t + d, "Narr", c))
                t += d
        else:
            n_from, n_to = int(it["from"][1:]), int(it["to"][1:])
            for n in range(n_from, n_to + 1):
                ln = by_id.get(f"L{n}")
                ko = (it.get("subs") or {}).get(f"L{n}")
                if ln and ko:
                    st = max(T, ln["start"] - start + T)
                    en = min(T + seg, ln["end"] - start + T + 0.2)
                    events.append((st, en, "Dialog", ko))
        T += seg

    # 90초 초과 시 8%까지는 살짝 빠르게, 그 이상은 plan 수정 요청
    speed = 1.0
    if T > config.MAX_SEC:
        speed = T / (config.MAX_SEC - 0.5)
        if speed > 1.08:
            raise RuntimeError(f"완성 길이가 {T:.1f}초로 {config.MAX_SEC:.0f}초를 넘습니다. "
                               f"plan.json에서 항목을 줄이거나 나레이션을 짧게 고쳐주세요.")
        print(f"  · {T:.1f}초 → {speed:.3f}배속으로 {T / speed:.1f}초에 맞춤")
    final_len = T / speed
    events = [(s / speed, e / speed, st, tx) for s, e, st, tx in events]
    build_ass(events, plan.get("overlay_title"), final_len, rdir / "subs.ass",
              lay=lay, label=episode_label(plan, bold=lay["template"] == "bold"))
    (rdir / "list.txt").write_text("".join(f"file '{s}'\n" for s in segs), encoding="utf-8")

    print("  · 자막 합성 + 음량 정규화")
    slug = wdir.name
    odir = config.OUT / slug
    odir.mkdir(parents=True, exist_ok=True)
    video = odir / f"{slug}.mp4"
    subs = "subtitles=subs.ass" + (":fontsdir=fonts" if (rdir / "fonts").is_dir() else "")
    vf = (f"setpts=PTS/{speed:.5f}," if speed != 1 else "") + subs
    af = (f"atempo={speed:.5f}," if speed != 1 else "") + "loudnorm=I=-14:TP=-1.5:LRA=11"
    media.ffmpeg(["-f", "concat", "-safe", "0", "-i", "list.txt", "-vf", vf, "-af", af,
                  "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", "30",
                  "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
                  str(video.resolve())], cwd=rdir)

    tags = " ".join(plan.get("hashtags") or [])
    post = (f"[제목]\n{plan.get('title', '')}\n\n[틱톡 캡션]\n{plan.get('caption', '')}\n\n{tags}\n\n"
            f"[네이버 클립 설명]\n{plan.get('title', '')}\n{plan.get('caption', '')}\n\n{tags}\n\n"
            f"[출처 표기]\n드라마: {plan['drama']}\n" +
            "".join(f"원본 {c['ep']}화: {c['channel']} 공식 채널 {c['url']}\n" for c in plan.get("credits") or []))
    (odir / "post.txt").write_text(post, encoding="utf-8")
    (odir / "timeline.json").write_text(json.dumps(
        {"duration": round(final_len, 2), "speed": round(speed, 4),
         "items": [[round(s / speed, 2), round(d / speed, 2)] for s, d in marks]}), encoding="utf-8")

    if final_len < 60.5:
        print(f"  ! {final_len:.1f}초 — 틱톡 크리에이터 리워드는 1분 초과 영상만 해당됩니다.")
    print(f"\n완료 → {video}  ({final_len:.1f}초)\n업로드 문구 → {odir / 'post.txt'}")
    return video
