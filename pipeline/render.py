"""plan.json → 1080x1920 숏폼 (나레이션 + 자막) + 업로드 문구."""
import json
import re
from pathlib import Path

from . import config, media, tts


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


def build_ass(events, overlay, total, path):
    f = config.FONT
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,{f},84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,7,2,8,60,60,250,1
Style: Narr,{f},72,&H0033E6FF,&H0033E6FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,6,2,2,90,90,{config.SUB_MARGIN_V},1
Style: Dialog,{f},68,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,6,2,2,90,90,{config.SUB_MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = []
    if overlay:
        title = r"\N".join(ass_text(x) for x in overlay if x)
        rows.append(f"Dialogue: 1,{ass_time(0)},{ass_time(total)},Title,,0,0,0,,{title}")
    for st, en, style, text in events:
        if en - st > 0.05:
            rows.append(f"Dialogue: 0,{ass_time(st)},{ass_time(en)},{style},,0,0,0,,{wrap(ass_text(text))}")
    Path(path).write_text(head + "\n".join(rows) + "\n", encoding="utf-8")


# ---------------- 영상 ----------------
def render_segment(src, has_audio, start, seg, narr_mp3, out):
    z = config.FG_ZOOM
    args = ["-ss", f"{start:.3f}", "-t", f"{seg:.3f}", "-i", src]
    a_orig = "0:a"
    idx = 1
    if not has_audio:
        args += ["-f", "lavfi", "-t", f"{seg:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        a_orig, idx = "1:a", 2
    vol = config.NARR_ORIG_VOLUME if narr_mp3 else 1.0
    fc = (f"[0:v]fps=30,split=2[v1][v2];"
          f"[v1]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=40,"
          f"eq=brightness=-0.12[bg];"
          f"[v2]scale={int(1080 * z)}:-2,crop='min(iw,1080)':'min(ih,1920)'[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v];"
          f"[{a_orig}]aresample=48000,aformat=channel_layouts=stereo,volume={vol}[oa]")
    if narr_mp3:
        args += ["-i", narr_mp3]
        fc += (f";[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,adelay=150|150,volume=1.5[na];"
               f"[oa][na]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        fc += ";[oa]anull[a]"
    media.ffmpeg([*args, "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{seg:.3f}",
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", "30",
                  "-c:a", "aac", "-b:a", "192k", "-ar", "48000", out])


def run(wdir, tts_engine=None):
    wdir = Path(wdir)
    plan = json.loads((wdir / "plan.json").read_text(encoding="utf-8"))
    by_id = {x["id"]: x for x in json.loads((wdir / "lines.json").read_text(encoding="utf-8"))}
    sources = plan["sources"]
    info = {s: media.probe(s) for s in sources}
    rdir = wdir / "render"
    rdir.mkdir(exist_ok=True)
    for old in rdir.glob("seg_*.mp4"):
        old.unlink()

    events, segs, marks, T = [], [], [], 0.0
    tl = plan["timeline"]
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
        render_segment(src, info[src]["has_audio"], start, seg, str(mp3) if mp3 else None, str(out))
        segs.append(out.name)
        marks.append([round(T, 2), round(seg, 2)])

        if narr:
            chunks = chunk_narration(narr)
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
    build_ass(events, plan.get("overlay_title"), final_len, rdir / "subs.ass")
    (rdir / "list.txt").write_text("".join(f"file '{s}'\n" for s in segs), encoding="utf-8")

    print("  · 자막 합성 + 음량 정규화")
    slug = wdir.name
    odir = config.OUT / slug
    odir.mkdir(parents=True, exist_ok=True)
    video = odir / f"{slug}.mp4"
    vf = (f"setpts=PTS/{speed:.5f}," if speed != 1 else "") + "subtitles=subs.ass"
    af = (f"atempo={speed:.5f}," if speed != 1 else "") + "loudnorm=I=-14:TP=-1.5:LRA=11"
    media.ffmpeg(["-f", "concat", "-safe", "0", "-i", "list.txt", "-vf", vf, "-af", af,
                  "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", "30",
                  "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
                  str(video.resolve())], cwd=rdir)

    tags = " ".join(plan.get("hashtags") or [])
    post = (f"[제목]\n{plan.get('title', '')}\n\n[틱톡 캡션]\n{plan.get('caption', '')}\n\n{tags}\n\n"
            f"[네이버 클립 설명]\n{plan.get('title', '')}\n{plan.get('caption', '')}\n\n{tags}\n\n"
            f"[출처 표기]\n드라마: {plan['drama']}\n")
    (odir / "post.txt").write_text(post, encoding="utf-8")
    (odir / "timeline.json").write_text(json.dumps(
        {"duration": round(final_len, 2), "speed": round(speed, 4),
         "items": [[round(s / speed, 2), round(d / speed, 2)] for s, d in marks]}), encoding="utf-8")

    if final_len < 60.5:
        print(f"  ! {final_len:.1f}초 — 틱톡 크리에이터 리워드는 1분 초과 영상만 해당됩니다.")
    print(f"\n완료 → {video}  ({final_len:.1f}초)\n업로드 문구 → {odir / 'post.txt'}")
    return video
