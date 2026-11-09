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
BOLD_TOP = 450 + config.BOLD_SHIFT   # bold 템플릿: 제목 띠 높이 (앱 상단 UI만큼 내림)


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
        narr = dlg                                # 해설 자막: 대사 자막과 같은 자리 (둘은 동시에 안 나옴)
        return {"template": "bold", "zoom": z, "pad_y": BOLD_TOP, "max_h": 1920 - BOLD_TOP,
                "title_y": (218 + config.BOLD_SHIFT, 362 + config.BOLD_SHIFT), "label_y": BOLD_TOP - 14,   # 제목 2줄, 〈작품명〉 ep 라벨
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
        part = (plan.get("series") or {}).get("part")
        if part is not None:
            return f"〈{plan['drama']}〉 {'예고편' if part == 0 else f'{part}편'}"
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


# 흰 대사 자막 뒤 반투명 회색 둥근 상자 (auto-insta 쇼츠 자막의 gray 스타일과 같은 색·여백·모서리)
BOX_RGB, BOX_ALPHA = (90, 90, 90), 200            # 불투명도 200/255
BOX_PAD_X, BOX_PAD_Y, BOX_RADIUS = 28, 16, 18
_fonts = {}


def _font_for(name, size):
    """ASS 글자 크기(줄 높이 기준) → 같은 크기의 PIL 글꼴. fonts 폴더에 없는 글꼴이면 None."""
    from PIL import ImageFont
    files = {"Black Han Sans": "BlackHanSans-Regular.ttf", "Pretendard Black": "Pretendard-Black.otf"}
    path = config.ROOT / "fonts" / files.get(name, "-")
    if name not in files or not path.exists():
        return None
    key = (name, size)
    if key not in _fonts:
        probe = ImageFont.truetype(str(path), size)
        asc, desc = probe.getmetrics()
        _fonts[key] = ImageFont.truetype(str(path), max(1, round(size * size / (asc + desc))))
    return _fonts[key]


def _round_rect(w, h, r):
    """ASS 그림 명령: (0,0)~(w,h) 둥근 사각형."""
    k = r * 0.45
    return (f"m {r} 0 l {w - r} 0 b {w - k:.1f} 0 {w} {k:.1f} {w} {r} l {w} {h - r} "
            f"b {w} {h - k:.1f} {w - k:.1f} {h} {w - r} {h} l {r} {h} b {k:.1f} {h} 0 {h - k:.1f} 0 {h - r} "
            f"l 0 {r} b 0 {k:.1f} {k:.1f} 0 {r} 0")


def dialog_box(text, font_name, size, bottom, outline=2):
    """대사 한 컷(줄바꿈 포함) 뒤에 깔 둥근 상자 이벤트 텍스트. 글꼴을 못 재면 None.
    bottom: 자막 맨 아랫줄 바닥 y (an2 기준점)."""
    font = _font_for(font_name, size)
    if font is None:
        return None
    rows = text.split("\\N")
    tw = max(font.getlength(r) for r in rows) + outline * 2
    th = size * len(rows)
    w, h = round(tw + BOX_PAD_X * 2), round(th + BOX_PAD_Y * 2 - size * 0.18)
    x0, y0 = round(540 - w / 2), round(bottom - th + size * 0.1 - BOX_PAD_Y)
    a = f"{255 - BOX_ALPHA:02X}"
    c = f"{BOX_RGB[2]:02X}{BOX_RGB[1]:02X}{BOX_RGB[0]:02X}"
    return ("{\\an7\\pos(%d,%d)\\bord0\\shad0\\1c&H%s&\\1a&H%s&\\p1}%s{\\p0}"
            % (x0, y0, c, a, _round_rect(w, h, BOX_RADIUS)))


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
            f"Style: Narr,{f},{config.NARR_FONT_SIZE},{narr_c},{narr_c},&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,10,3,2,70,70,{narr_v},1\n"
            + (f"Style: Dialog,{f},{config.DIALOG_FONT_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,50,50,{dlg_v},1\n"
               if config.DIALOG_BOX == "gray" else   # 상자가 있으면 테두리는 얇게
               f"Style: Dialog,{f},{config.DIALOG_FONT_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,0,0,0,0,100,100,0,0,1,6,2,2,50,50,{dlg_v},1\n"))
        wrap_w = {"Narr": 9, "Dialog": 13}
        base = 136
        dlg_font, dlg_size = f, config.DIALOG_FONT_SIZE
    else:
        f = config.FONT
        narr_c, t2_c = ass_color(config.NARR_COLOR), ass_color(config.TITLE2_COLOR)
        styles = (
            f"Style: Title1,{f},96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,-2,0,1,6,0,2,40,40,0,1\n"
            f"Style: Title2,{f},96,{t2_c},{t2_c},&H00000000,&H00000000,-1,0,0,0,100,100,-2,0,1,6,0,2,40,40,0,1\n"
            f"Style: Label,{f},48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,0,1,60,60,0,1\n"
            f"Style: Narr,{f},76,{narr_c},{narr_c},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,6,0,{lay['narr_align']},70,70,{narr_v},1\n"
            f"Style: Dialog,{f},70,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,1,5,2,2,70,70,{dlg_v},1\n")
        wrap_w = {"Narr": 12, "Dialog": 13}
        base = 96
        dlg_font, dlg_size = f, 70
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
            body = wrap(ass_text(text), wrap_w[style])
            box = (dialog_box(body, dlg_font, dlg_size, 1920 - dlg_v)
                   if style == "Dialog" and config.DIALOG_BOX == "gray" else None)
            if box:                                   # 상자(아래 층) + 글자(위 층)
                rows.append(f"Dialogue: 0,{ass_time(st)},{ass_time(en)},Dialog,,0,0,0,,{box}")
            rows.append(f"Dialogue: 1,{ass_time(st)},{ass_time(en)},{style},,0,0,0,,{body}")
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


FER_MODEL = config.ROOT / "models" / "facial_expression_recognition_mobilefacenet_2022july.onnx"
FER_LABELS = ["angry", "disgust", "fearful", "happy", "neutral", "sad", "surprised"]
_FER_STD = None
_fer = None


def face_shock(frame, face, scale=1.0):
    """얼굴 하나가 놀란 표정인 정도 0~1 (놀람 + 겁먹음 절반). 표정 모델이 없으면 0.
    face: YuNet 결과 한 줄(상자 4 + 눈·코·입꼬리 5점), scale: 검출한 작은 프레임 → frame 배율."""
    global _fer, _FER_STD
    if not FER_MODEL.exists():
        return 0.0
    import cv2
    import numpy as np
    if _fer is None:
        _fer = cv2.dnn.readNet(str(FER_MODEL))
        _FER_STD = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                             [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)
    pts = (np.array(face[4:14], dtype=np.float32).reshape(5, 2) * scale)
    m, _ = cv2.estimateAffinePartial2D(pts, _FER_STD)      # 눈·코·입 위치를 맞춰 112x112 로 정렬
    if m is None:
        return 0.0
    img = cv2.cvtColor(cv2.warpAffine(frame, m, (112, 112)), cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    _fer.setInput(cv2.dnn.blobFromImage((img - 0.5) / 0.5), "data")
    z = _fer.forward(["label"])[0][0].astype(np.float64)
    pr = np.exp(z - z.max())
    pr /= pr.sum()
    return float(min(1.0, pr[FER_LABELS.index("surprised")] + 0.5 * pr[FER_LABELS.index("fearful")]))


def pick_cover(src, t0, t1, step=0.4, detail=False):
    """t0~t1 사이에서 썸네일로 쓸 순간을 고른다: 놀란 표정을 가장 우선하고, 그다음 얼굴이 크고 초점이 선명한 프레임.
    (틱톡·클립은 영상 첫 화면이 썸네일이 된다)  detail=True 면 (시각, 놀람 정도)."""
    global _detector
    import cv2
    cap = cv2.VideoCapture(src)
    best, best_t, best_shock = -1.0, t0, 0.0
    t = t0
    while t <= t1:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        W, H = 640, max(1, int(h * 640 / w))
        small = cv2.resize(frame, (W, H))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        box, area, shock = None, 0.0, 0.0
        if FACE_MODEL.exists():
            if _detector is None:
                _detector = cv2.FaceDetectorYN.create(str(FACE_MODEL), "", (W, H), 0.7)
            _detector.setInputSize((W, H))
            _, faces = _detector.detect(small)
            if faces is not None and len(faces):
                f = max(faces, key=lambda r: r[2] * r[3])
                x, y, fw, fh = (int(max(0, v)) for v in f[:4])
                box, area = gray[y:y + fh, x:x + fw], fw * fh / (W * H)
                # 화면에 크게 보이는 얼굴들 중 가장 놀란 얼굴 (원본 해상도에서 판정)
                shock = max(face_shock(frame, r, w / W) for r in faces if r[2] * r[3] >= 0.4 * f[2] * f[3])
        region = box if box is not None and box.size > 400 else gray[H // 4: 3 * H // 4, W // 4: 3 * W // 4]
        sharp = float(cv2.Laplacian(region, cv2.CV_64F).var())
        score = (sharp * (1 + 6 * min(area, 0.25)) * (1.0 if box is not None else 0.4)
                 * (1 + config.COVER_SHOCK_WEIGHT * shock))
        if score > best:
            best, best_t, best_shock = score, t, shock
        t += step
    cap.release()
    return (best_t, round(best_shock, 2)) if detail else best_t


def _duck_expr(seg, ramp_in, ramp_out):
    """나레이션 동안 원음을 줄인다. 원본 구간과 맞닿는 쪽은 부드럽게 줄였다 키운다."""
    v, f = config.NARR_ORIG_VOLUME, min(config.DUCK_FADE, seg / 3)
    expr = f"{v}"
    if ramp_out:
        expr = f"if(gt(t,{seg - f:.3f}),{v}+(1-{v})*(t-{seg - f:.3f})/{f:.3f},{expr})"
    if ramp_in:
        expr = f"if(lt(t,{f:.3f}),1-(1-{v})*t/{f:.3f},{expr})"
    return f"volume='{expr}':eval=frame"


def out_fps(info):
    """완성 영상 프레임 수: 원본이 찍힌 그대로(25fps 원본은 25). 30으로 억지로 올리면 5장마다 같은 장이 끼어 뚝뚝 끊긴다.
    6장마다 같은 장을 끼워 30으로 올린 원본(media.pulldown)도 25로 본다. 원본마다 다르면 30."""
    natives = {25 if x.get("pulldown") else round(x.get("fps") or 30) for x in info.values()}
    f = natives.pop() if len(natives) == 1 else 30
    return f if 20 <= f <= 30 else 30


def render_segment(src, has_audio, start, seg, narr_mp3, out, lay, ramp_in=False, ramp_out=False, orig_vol=None,
                   bars=None, fps=30, pulldown=False):
    z = lay["zoom"]
    # 25→30 으로 올리며 끼워 넣은 같은 장(6장마다 1장)을 빼서 원래 25장으로 되돌린다
    vin = f"[0:v]{'decimate=cycle=6,' if pulldown and fps == 25 else ''}fps={fps}"
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
    sharpen = ",unsharp=5:5:0.6:5:5:0.0" if ws > 1080 + 8 else ""
    unbar = f"crop=iw:{bars[1]}:0:{bars[0]}," if bars else ""      # 원본의 위아래 검은 띠 잘라내기
    fg = (f"{unbar}scale={ws}:-2:flags=lanczos,crop='min(iw,1080)':'min(ih,{lay['max_h']})':{crop_x}:'(ih-oh)/2'"
          f"{sharpen}")
    if config.BG_STYLE == "blur" and lay["template"] == "classic":
        fc = (f"{vin},split=2[v1][v2];"
              f"[v1]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=40,"
              f"eq=brightness=-0.12[bg];[v2]{fg}[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v];")
    else:   # 검은 여백
        py = lay["pad_y"] if lay["pad_y"] is not None else "(oh-ih)/2"
        fc = f"{vin},{fg},pad=1080:1920:(ow-iw)/2:{py}:black,setsar=1,format=yuv420p[v];"
    fc += f"[{a_orig}]aresample=48000,aformat=channel_layouts=stereo"
    if narr_mp3:
        fc += f",{_duck_expr(seg, ramp_in, ramp_out)}[oa]"
        args += ["-i", narr_mp3]
        fc += (f";[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,adelay=150|150,volume=1.5[na];"
               f"[oa][na]amix=inputs=2:duration=first:normalize=0[a]")
    elif orig_vol is not None:
        fc += f",volume={orig_vol}[a]"
    else:
        fc += "[a]"
    media.ffmpeg([*args, "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{seg:.3f}",
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", str(fps),
                  "-c:a", "aac", "-b:a", "192k", "-ar", "48000", out])


def run(wdir, tts_engine=None, voice=None):
    wdir = Path(wdir)
    engine = (tts_engine or config.TTS_ENGINE or "auto").lower()
    if voice:
        tts.set_voice(engine, voice)
    shown = tts.voice_key(engine) if engine in tts.VOICE_LISTS else ""
    print(f"  · 나레이션: {engine}{' / ' + tts.VOICE_LISTS[engine][shown][0] if shown else ''}", flush=True)
    plan = json.loads((wdir / "plan.json").read_text(encoding="utf-8"))
    by_id = {x["id"]: x for x in json.loads((wdir / "lines.json").read_text(encoding="utf-8"))}
    sources = plan["sources"]
    info = {s: media.probe(s) for s in sources}
    for s in sources:
        info[s]["pulldown"] = media.pulldown(s)
        if info[s]["pulldown"]:
            print(f"  · 원본이 25fps 를 30fps 로 늘린 영상 → 끼워 넣은 프레임을 빼고 25fps 로 ({Path(s).name})")
        info[s]["bars"] = media.letterbox(s)
        if info[s]["bars"]:
            print(f"  · 원본 위아래 검은 띠 {info[s]['bars'][0]}px 잘라냄 ({Path(s).name})")
    first = dict(info[sources[0]])
    if first["bars"]:
        first["height"] = first["bars"][1]        # 띠를 뺀 실제 그림 비율로 배치
    lay = layout(first["width"], first["height"])
    fps = out_fps(info)
    seg_kw = lambda s: {"bars": info[s]["bars"], "fps": fps, "pulldown": info[s]["pulldown"]}
    rdir = wdir / "render"
    rdir.mkdir(exist_ok=True)
    for old in rdir.glob("seg_*.mp4"):
        old.unlink()
    if FONTS.is_dir():   # 굵은 제목체(Black Han Sans)를 자막 합성에 쓰도록 복사
        shutil.copytree(FONTS, rdir / "fonts", dirs_exist_ok=True)
    print(f"  · 템플릿: {lay['template']}, {fps}fps")

    # 1단계: 나레이션 음성을 먼저 만들어 항목별 실제 길이를 잰다
    tl = plan["timeline"]
    items = []
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
        if i == 1:
            # 표지(첫 1초 = 썸네일) 장면을 따로 정한다. 나레이션은 대본에 정해진 첫 장면 위에서 시작한다.
            ct = plan.get("cover_time")
            if ct and 0 <= int(ct.get("src", 0)) < len(sources):
                cover_src = sources[int(ct["src"])]
                cover_start = max(0.0, float(ct["t"]) - 0.05)
                why = "직접 고른 장면"
            else:
                # 후보 장면: 대본이 고른 썸네일 대사 + 대본에 쓴 장면 중 가장 격한 대사 4개
                #  → 각 장면 앞뒤에서 놀란 표정이 가장 큰 프레임. 대본 추천 장면은 조금 더 쳐준다.
                cv = by_id.get(plan.get("cover") or "")
                used = [by_id[f"L{n}"] for t in tl for n in range(int(t["from"][1:]), int(t["to"][1:]) + 1)
                        if f"L{n}" in by_id]
                cands = ([cv] if cv else []) + sorted(used, key=lambda x: -x.get("energy", 0))
                picks = []
                for ln in cands:
                    if len(picks) >= 5:
                        break
                    if not any(q["src"] == ln["src"] and abs(q["start"] - ln["start"]) < 15 for q in picks):
                        picks.append(ln)
                found = []
                for ln in picks:
                    c_src = sources[ln["src"]]
                    csd = info[c_src]["duration"]
                    s_st, s_en = media.story_range(c_src)
                    t0, t1 = max(ln["start"] - 1.0, s_st, 0.0), min(ln["end"] + 4.0, s_en - 1, csd)
                    if t1 <= t0:
                        continue
                    ct_, shock = pick_cover(c_src, t0, t1, detail=True)
                    bonus = 0.15 if cv and ln is cv else 0.0
                    found.append((shock + bonus, shock, c_src, ct_, "대본이 고른 장면" if bonus else "격한 장면"))
                if found:
                    _, shock, cover_src, ct_, why = max(found, key=lambda x: x[0])
                    why += f", 놀람 {shock:.0%}"
                else:
                    cover_src, (s_st, s_en) = src, media.story_range(src)
                    t0 = max(start, s_st)
                    ct_ = pick_cover(src, t0, min(s_en - 1, t0 + 12.0))
                    why = "첫 장면 근처"
                cover_start = max(0.0, ct_ - 0.05)
            print(f"  · 썸네일(표지): {int(cover_start // 60)}:{cover_start % 60:04.1f} ({why})")
        seg = nd + 0.5 if narr else clip
        # 오프닝 곡·엔딩 크레딧 위에 나레이션이 깔리지 않게 본편 안으로 옮긴다
        s_st, s_en = media.story_range(src)
        if narr and (start + seg > s_en or start < s_st):
            moved = min(max(start, s_st), max(s_st, s_en - seg - 0.3))
            print(f"  · [{i}] 나레이션 화면이 크레딧에 걸려 {int(moved // 60)}:{moved % 60:04.1f}로 옮김")
            start = moved
        elif not narr and start >= s_en:
            print(f"  · [{i}] 크레딧 구간 원본 장면 제외")
            continue
        if start + seg > sdur:
            start = max(0.0, sdur - seg)
            seg = min(seg, sdur)
        items.append({"it": it, "idx": i - 1, "src": src, "start": start, "seg": seg, "narr": narr,
                      "mp3": mp3, "nd": nd})

    # 2단계: 너무 길면(8% 배속으로도 90초 초과) 덜 격한 원본 장면부터 뺀다 (표지 길이 포함)
    limit = (config.MAX_SEC - 0.5) * 1.08 - max(0.0, config.COVER_SECONDS)
    while sum(x["seg"] for x in items) > limit:
        cands = [x for x in items[1:-1] if not x["narr"]]
        if not cands:
            break
        worst = min(cands, key=lambda x: sum(by_id[f"L{n}"].get("energy", 0) for n in
                                              range(int(x["it"]["from"][1:]), int(x["it"]["to"][1:]) + 1)
                                              if f"L{n}" in by_id))
        items.remove(worst)
        print(f"  · 길이 초과 → 원본 장면 {worst['it']['from']}~{worst['it']['to']} 제외")

    events, segs, marks, T = [], [], [], 0.0
    tl = [x["it"] for x in items]
    is_narr = [bool(x["narr"]) for x in items]

    # 맨 앞 표지: 썸네일 장면에 제목만 (나레이션·자막 없음). 틱톡·클립은 첫 화면을 썸네일로 쓴다.
    cover = config.COVER_SECONDS
    if cover > 0 and items:
        out = rdir / "seg_00.mp4"
        print(f"  · 표지 {cover:.1f}초 (썸네일 장면 + 제목만, 그다음 대본 첫 장면에서 나레이션 시작)")
        render_segment(cover_src, info[cover_src]["has_audio"], cover_start, cover, None, str(out), lay,
                       orig_vol=config.NARR_ORIG_VOLUME, **seg_kw(cover_src))
        segs.append(out.name)
        T = cover
    for i, x in enumerate(items, 1):
        it, src, start, seg, narr, mp3, nd = (x["it"], x["src"], x["start"], x["seg"], x["narr"], x["mp3"], x["nd"])
        print(f"  · [{i}/{len(tl)}] 구간 편집 {seg:.1f}초")
        out = rdir / f"seg_{i:02d}.mp4"
        render_segment(src, info[src]["has_audio"], start, seg, str(mp3) if mp3 else None, str(out), lay,
                       ramp_in=i > 1 and not is_narr[i - 2], ramp_out=i < len(tl) and not is_narr[i],
                       **seg_kw(src))
        segs.append(out.name)
        marks.append([round(T, 2), round(seg, 2), x["idx"]])

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
            shown = [(by_id[f"L{n}"], (it.get("subs") or {}).get(f"L{n}")) for n in range(n_from, n_to + 1)
                     if f"L{n}" in by_id and (it.get("subs") or {}).get(f"L{n}")]
            for k, (ln, ko) in enumerate(shown):
                st = max(T, ln["start"] - start + T)
                en = min(T + seg, ln["end"] - start + T + 0.2)
                if k + 1 < len(shown):            # 다음 대사가 나오면 앞 대사는 내린다 (두 줄 겹침 방지)
                    en = min(en, shown[k + 1][0]["start"] - start + T - 0.02)
                en = max(en, st + 0.3)
                # 번역에 문장이 여러 개면(두 사람 말이 한 줄로 합쳐진 경우 등) 글자 수에 맞춰 차례로 나눠 보여준다
                parts = [x.strip() for x in re.split(r"(?<=[.!?…])\s+", ko.strip()) if x.strip()]
                if len(parts) > 1 and en - st > 2.5:
                    total = sum(len(x) for x in parts)
                    t = st
                    for x in parts:
                        d = (en - st) * len(x) / total
                        events.append((t, t + d, "Dialog", x))
                        t += d
                else:
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
    # 빠르게 돌릴 때 프레임 수를 그대로 두면 일정 간격으로 장이 빠져 끊긴다 → 배속만큼 초당 장 수를 올려 모든 장을 살린다
    rate = f"{fps * speed:.3f}".rstrip("0").rstrip(".")
    af = (f"atempo={speed:.5f}," if speed != 1 else "") + "loudnorm=I=-14:TP=-1.5:LRA=11"
    media.ffmpeg(["-f", "concat", "-safe", "0", "-i", "list.txt", "-vf", vf, "-af", af,
                  "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", rate,
                  "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
                  str(video.resolve())], cwd=rdir)

    from .plan import normalize_tags
    from .upload import clip_tags
    tag_list = normalize_tags(plan.get("hashtags"), plan["drama"])
    tags, ctags = " ".join(tag_list), " ".join(clip_tags(tag_list))   # 클립에만 #오늘클립챌린지 등
    post = (f"[제목]\n{plan.get('title', '')}\n\n[틱톡 캡션]\n{plan.get('caption', '')}\n\n{tags}\n\n"
            f"[네이버 클립 설명]\n{plan.get('title', '')}\n{plan.get('caption', '')}\n\n{ctags}\n\n"
            f"[출처 표기]\n드라마: {plan['drama']}\n" +
            "".join(f"원본 {c['ep']}화: {c['channel']} 공식 채널 {c['url']}\n" for c in plan.get("credits") or []))
    (odir / "post.txt").write_text(post, encoding="utf-8")
    (odir / "timeline.json").write_text(json.dumps(
        {"duration": round(final_len, 2), "speed": round(speed, 4),
         "items": [[round(s / speed, 2), round(d / speed, 2), k] for s, d, k in marks]}), encoding="utf-8")

    if final_len < 60.5:
        print(f"  ! {final_len:.1f}초 — 틱톡 크리에이터 리워드는 1분 초과 영상만 해당됩니다.")
    print(f"\n완료 → {video}  ({final_len:.1f}초)\n업로드 문구 → {odir / 'post.txt'}")
    return video
