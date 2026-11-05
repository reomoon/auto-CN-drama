"""구독 계정 CLI(Claude Code / Codex)를 번갈아 호출한다. API 키 불필요."""
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config

ENGINES = ("claude", "gpt")
STATE = config.WORK / ".llm_state.json"


def _newest(pattern):
    c = glob.glob(pattern)
    return max(c, key=os.path.getmtime) if c else None


def find_claude():
    p = config.CLAUDE_PATH or shutil.which("claude") or _newest(
        str(Path.home() / ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe"))
    if not p:
        raise RuntimeError("claude 실행 파일을 찾지 못했습니다. .env 의 CLAUDE_PATH 를 지정하세요.")
    return p


def find_codex():
    local = os.environ.get("LOCALAPPDATA", "")
    p = config.CODEX_PATH or _newest(os.path.join(local, "OpenAI", "Codex", "bin", "*", "codex.exe")) \
        or shutil.which("codex")
    if not p:
        raise RuntimeError("codex 실행 파일을 찾지 못했습니다. .env 의 CODEX_PATH 를 지정하세요.")
    return p


def _run_claude(prompt, web=False, timeout=900):
    cmd = [find_claude(), "-p", "--output-format", "text"]
    if config.CLAUDE_MODEL:
        cmd += ["--model", config.CLAUDE_MODEL]
    if web:
        cmd += ["--allowedTools", "WebSearch", "WebFetch"]
    with tempfile.TemporaryDirectory() as d:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, cwd=d)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"claude 실패 (exit {r.returncode}): {(r.stderr or r.stdout)[-500:]}")
    return r.stdout.strip()


def _run_gpt(prompt, web=False, timeout=900):
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "answer.txt")
        cmd = [find_codex(), "exec", "--skip-git-repo-check", "-s", "read-only", "--color", "never",
               "-m", config.CODEX_MODEL,
               "-c", 'service_tier="default"',
               "-c", f'model_reasoning_effort="{config.CODEX_EFFORT}"',
               "-o", out, "-"]
        if web:
            cmd[2:2] = ["-c", 'web_search="live"']
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, cwd=d)
        text = Path(out).read_text(encoding="utf-8").strip() if os.path.exists(out) else ""
    if not text:
        raise RuntimeError(f"codex 실패 (exit {r.returncode}): {(r.stderr or r.stdout)[-500:]}")
    return text


_RUNNERS = {"claude": _run_claude, "gpt": _run_gpt}


def next_engine():
    """영상마다 Claude → GPT → Claude ... 순으로 번갈아 고른다."""
    last = None
    if STATE.exists():
        last = json.loads(STATE.read_text(encoding="utf-8")).get("last")
    eng = "gpt" if last == "claude" else "claude"
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"last": eng}), encoding="utf-8")
    return eng


def generate(prompt, engine="claude", web=False, fallback=True):
    """(텍스트, 실제 사용한 엔진). 실패하면 다른 엔진으로 한 번 넘어간다."""
    order = [engine] + ([e for e in ENGINES if e != engine] if fallback else [])
    err = None
    for eng in order:
        try:
            return _RUNNERS[eng](prompt, web=web), eng
        except Exception as e:  # noqa: BLE001
            err = e
            print(f"  ! {eng} 호출 실패 → {'다른 엔진 시도' if eng != order[-1] else '중단'}: {e}")
    raise RuntimeError(f"모든 LLM 호출 실패: {err}")


def extract_json(text):
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError("JSON이 없습니다")
    s = min(starts)
    e = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[s:e + 1])


def generate_json(prompt, engine="claude", web=False, validate=None, retries=2):
    """JSON 응답을 받아 검증한다. 검증 오류는 모델에게 되돌려 고치게 한다."""
    p = prompt
    for attempt in range(retries + 1):
        text, used = generate(p, engine=engine, web=web)
        try:
            data = extract_json(text)
            errors = validate(data) if validate else []
        except Exception as e:  # noqa: BLE001
            data, errors = None, [f"JSON 파싱 실패: {e}"]
        if not errors:
            return data, used
        print(f"  ! 응답 검증 실패({attempt + 1}/{retries + 1}): {'; '.join(errors)[:300]}")
        engine = used
        p = (prompt + "\n\n---\n이전 응답:\n" + text[-6000:] +
             "\n\n위 응답에 다음 문제가 있습니다. 모두 고쳐서 JSON만 다시 출력하세요:\n- " + "\n- ".join(errors))
    raise RuntimeError("LLM 응답이 계속 검증에 실패했습니다: " + "; ".join(errors))
