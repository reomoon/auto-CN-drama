"""테스트용: 중국어 막장 대사 음성 + 테스트 화면 → 가로 영상."""
import asyncio
import subprocess
import sys
from pathlib import Path

import edge_tts

OUT = Path(sys.argv[1])
tmp = OUT.parent / "tts_parts"
tmp.mkdir(parents=True, exist_ok=True)

F, M, O = "zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-XiaoyiNeural"
lines = [
    (F, "今天是我和子轩的婚礼，我终于等到这一天了。"), (M, "林婉，你真美。"),
    (O, "等一下！这场婚礼不能继续！"), (F, "你是谁？你凭什么闯进来？"),
    (O, "我是子轩的前女友，我怀了他的孩子！"), (M, "你胡说什么！我们早就分手了！"),
    (O, "三个月前，你说你会娶我的！"), (F, "子轩，她说的是真的吗？"),
    (M, "婉儿，你相信我，我根本不认识她！"), (O, "这是孕检报告，你自己看吧！"),
    (F, "我不想听你解释！"), (M, "妈，你快说句话啊！"),
    (O, "阿姨，您不是说只认我这个儿媳妇吗？"), (F, "什么？婆婆你早就知道？"),
    (M, "妈，你到底瞒了我们多少事？"), (O, "其实，林婉根本不是林家的亲生女儿！"),
    (F, "你说什么？这不可能！"), (O, "当年医院抱错了孩子，真正的林家千金是我！"),
    (M, "这……这怎么可能？"), (F, "我爸妈不会骗我的！"),
    (O, "不信的话，我们去做亲子鉴定！"), (F, "好，去就去！我不怕你！"),
    (M, "婉儿，不管你是谁，我都只爱你一个人。"), (O, "子轩，你醒醒吧！她就是个冒牌货！"),
    (F, "啪！这一巴掌，是替我自己打的！"), (O, "你敢打我？你知道我是谁吗？"),
    (F, "我管你是谁！今天这婚，我不结了！"), (M, "婉儿，你别走！"),
    (F, "放手！从今天开始，我们两清了！"), (O, "哈哈哈，林婉，你什么都没有了！"),
    (F, "三年后，我会让你们所有人都后悔。"), (M, "婉儿！婉儿！"),
    (O, "子轩，现在你只能娶我了。"), (M, "滚！我这辈子都不会娶你！"),
    (F, "三年后，我回来了。这一次，我是林氏集团的总裁。"), (O, "怎么会是你？你不是已经……"),
    (F, "没想到吧？当年的亲子鉴定，是你伪造的。"), (O, "你……你有什么证据？"),
    (F, "证据就在这里，警察已经在门外了。"), (M, "婉儿，对不起，我错了，你能原谅我吗？"),
    (F, "原谅你？三年前你在哪里？"),
]


async def gen():
    files = []
    for i, (v, t) in enumerate(lines):
        p = tmp / f"{i:02d}.mp3"
        if not p.exists():
            await edge_tts.Communicate(t, v).save(str(p))
        files.append(p)
    return files


files = asyncio.run(gen())
# 각 대사 뒤 0.8초 정적
concat = tmp / "list.txt"
sil = tmp / "sil.wav"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "0.8", str(sil)], check=True)
concat.write_text("".join(f"file '{f.name}'\nfile 'sil.wav'\n" for f in files), encoding="utf-8")
audio = tmp / "all.wav"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "list.txt", "-ar", "48000", "-ac", "2", str(audio)], check=True, cwd=tmp)
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30", "-i", str(audio),
                "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(OUT)], check=True)
print("ok", OUT)
