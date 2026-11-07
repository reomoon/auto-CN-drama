"""PC 대시보드를 AWS nginx(auto-cn.hot-news.app)에 연결하는 SSH 역방향 터널.

AWS 127.0.0.1:REMOTE_PORT  ──(ssh -R)──▶  PC 127.0.0.1:대시보드 포트
끊기면 5초 뒤 다시 연결한다.
"""
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import config


def _ssh_cmd(local_port):
    key = Path(config.REMOTE_SSH_KEY).expanduser() if config.REMOTE_SSH_KEY else None
    if not (config.REMOTE_SSH_HOST and key and key.exists()):
        raise RuntimeError(".env 에 REMOTE_SSH_HOST / REMOTE_SSH_KEY 를 설정하세요 (키 파일이 있어야 합니다).")
    ssh = shutil.which("ssh") or r"C:\Windows\System32\OpenSSH\ssh.exe"
    return [ssh, "-i", str(key), "-N", "-T",
            "-R", f"127.0.0.1:{config.REMOTE_PORT}:127.0.0.1:{local_port}",
            "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
            "-o", "StrictHostKeyChecking=accept-new",
            f"{config.REMOTE_SSH_USER}@{config.REMOTE_SSH_HOST}"]


def _loop(local_port):
    cmd = _ssh_cmd(local_port)
    while True:
        started = time.time()
        print(f"[터널] {config.REMOTE_URL} 연결 중...", flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        msg = (r.stderr or "").strip().splitlines()[-1:] or ["연결 끊김"]
        print(f"[터널] 끊김 ({msg[0]}) → 5초 뒤 재연결", flush=True)
        time.sleep(5 if time.time() - started > 30 else 15)


def start(local_port):
    _ssh_cmd(local_port)   # 설정 확인 (잘못되면 바로 오류)
    threading.Thread(target=_loop, args=(local_port,), daemon=True).start()
    print(f"[터널] 외부 주소: {config.REMOTE_URL}  (로그인 비밀번호: .env 의 DASHBOARD_PASSWORD)")
