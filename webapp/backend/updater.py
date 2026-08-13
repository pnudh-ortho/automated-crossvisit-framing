"""
프로그램 업데이트 — git 으로 코드를, 구글 드라이브로 가중치를 받는다.

    st = check()                  # 새 버전이 있나 (네트워크)
    apply_update()                # 받고 재시작 요청
    rollback()                    # 직전 버전으로

### 왜 앱 안에 두나

터미널을 안 여는 사람이 쓴다. 버그를 고쳐도 상대 컴퓨터에 안 들어가면 고친 게 아니다.

### 재시작

파이썬 프로세스는 **자기 자신을 바꿔치울 수 없다.** `git pull` 로 파일이 바뀌어도
이미 메모리에 올라간 모듈은 그대로다. 그래서 종료코드 `RESTART_CODE`(42)로 죽고,
`run.bat` / `run.command` 의 루프가 다시 띄운다. 다른 종료코드면 루프가 끝난다.

### 안전장치

    작업 중이면 거부      확정 안 된 세션이 있으면 재시작이 그 작업을 날린다
    로컬 수정이 있으면 거부  `git pull` 이 충돌하면 앱이 반쯤 갱신된 상태로 남는다
    직전 커밋 기록        되돌릴 길이 없으면 깨진 업데이트가 곧 못 쓰는 프로그램이다
    가중치는 따로         코드만 바뀌었으면 400MB 를 다시 받지 않는다
"""

from __future__ import annotations

import json
import shutil
import subprocess
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATE_FILE = REPO / ".update_state.json"        # 직전 커밋 — 롤백용
RESTART_CODE = 42
TIMEOUT = 60


def _git(*args, timeout: int = TIMEOUT) -> tuple[int, str]:
    """git 호출. 실패해도 예외를 안 던진다 — 업데이트 실패가 앱을 죽이면 안 된다.

    ### 인코딩을 반드시 못 박는다

    `text=True` 만 주면 파이썬은 **로케일 인코딩**으로 디코딩한다. 한국어 Windows 는
    CP949 다. git 은 어느 OS 에서든 커밋 제목을 UTF-8 로 내보내므로, 제목에 한글이
    한 자라도 있으면 리더 스레드가 `UnicodeDecodeError` 로 죽고 `p.stdout` 이
    `None` 이 되어 여기서 `TypeError` 가 난다 — 업데이트 확인이 500 으로 무너진다.

    실제로 그렇게 무너졌다. `git log --oneline` 으로 변경 요약을 읽는 마지막 한 줄
    때문에, 이미 성공한 확인 결과(behind 2)까지 통째로 버려졌다.

    `errors="replace"` 는 이중 안전장치다 — 어떤 바이트가 와도 예외 대신 글자가
    깨질 뿐이고, 그 대가로 업데이트 통로가 살아 있다.
    """
    try:
        p = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 127, f"{type(e).__name__}: {e}"


def available() -> bool:
    """git 저장소로 설치됐나. zip 으로 푼 설치본은 업데이트를 못 한다."""
    return _git("rev-parse", "--git-dir")[0] == 0


@dataclass
class UpdateStatus:
    ok: bool = False                 # 확인 자체가 성공했나
    has_update: bool = False
    local: str = ""                  # 현재 커밋 (짧게)
    remote: str = ""
    behind: int = 0                  # 몇 커밋 뒤처졌나
    app_from: str = ""
    app_to: str = ""
    weights_changed: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)   # 변경 요약 (커밋 제목)
    blocked: str = ""                # 비어 있지 않으면 적용 불가 사유
    reason: str = ""                 # 확인 실패 사유

    def to_json(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def _version(rev: str | None = None) -> dict:
    """`version.json` 을 읽는다. `rev` 를 주면 그 커밋의 것을 읽는다."""
    if rev is None:
        f = REPO / "version.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    code, out = _git("show", f"{rev}:version.json")
    try:
        return json.loads(out) if code == 0 else {}
    except json.JSONDecodeError:
        return {}


def check(*, busy: bool = False) -> UpdateStatus:
    """원격에 새 버전이 있나. **네트워크를 쓴다** — 호출부가 비동기로 돌릴 것."""
    st = UpdateStatus()
    if not available():
        st.reason = "git 저장소로 설치되지 않았습니다 (zip 설치본)"
        return st
    if _git("remote")[1].strip() == "":
        st.reason = "업데이트 원격이 설정되지 않았습니다 (개발용 설치본)"
        return st
    if _git("fetch", "--quiet", "origin", timeout=120)[0] != 0:
        st.reason = "원격에 연결하지 못했습니다. 인터넷 연결을 확인하세요"
        return st

    st.local = _git("rev-parse", "--short", "HEAD")[1]
    code, upstream = _git("rev-parse", "--abbrev-ref", "@{u}")
    if code != 0:
        st.reason = "추적 중인 원격 브랜치가 없습니다"
        return st
    st.remote = _git("rev-parse", "--short", upstream)[1]
    st.ok = True
    if st.local == st.remote:
        return st

    st.has_update = True
    cnt = _git("rev-list", "--count", f"HEAD..{upstream}")[1]
    st.behind = int(cnt) if cnt.isdigit() else 0
    st.log = [l for l in _git("log", "--oneline", "--no-decorate",
                              f"HEAD..{upstream}")[1].splitlines() if l][:20]

    cur, new = _version(), _version(upstream)
    st.app_from, st.app_to = cur.get("app", ""), new.get("app", "")
    for key, spec in (new.get("weights") or {}).items():
        old = (cur.get("weights") or {}).get(key, {})
        if spec.get("sha256") != old.get("sha256"):
            st.weights_changed.append(key)

    # ── 적용을 막아야 하는 상황 ──────────────────────────────────────────
    if busy:
        st.blocked = "확정하지 않은 작업이 있습니다. 저장하거나 닫은 뒤 다시 시도하세요"
    elif _git("status", "--porcelain")[1].strip():
        st.blocked = ("프로그램 폴더에 직접 수정한 파일이 있습니다. "
                      "그대로 두면 업데이트가 충돌합니다")
    return st


def _backup_local_changes() -> str | None:
    """추적 파일의 로컬 수정을 백업 폴더로 복사하고 원본 상태로 되돌린다.

    강제 업데이트에서만 쓴다 — 사용자 수정을 밀어내되 버리지는 않는다.
    반환: 백업 폴더 이름 (수정이 없었으면 None). 백업 폴더는 .gitignore 에
    올라 있어 다음 업데이트 확인을 다시 막지 않는다.
    """
    changed: set[str] = set()
    for args in (("diff", "--name-only"), ("diff", "--cached", "--name-only")):
        code, out = _git(*args)
        if code == 0:
            changed |= {l.strip() for l in out.splitlines() if l.strip()}
    if not changed:
        return None
    bdir = REPO / f"_update_backup_{time.strftime('%Y%m%d-%H%M%S')}"
    for rel in sorted(changed):
        src = REPO / rel
        if src.is_file():
            dst = bdir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    _git("reset", "--hard", "HEAD")
    return bdir.name


def apply_update(force: bool = False) -> dict:
    """`git pull` → 의존성 갱신 → 재시작 요청.

    가중치는 받지 않는다 — 용량이 크고, `weightstore` 가 기동 때 확인해서
    `models/` 에 넣으라고 안내한다. 코드와 다른 속도로 바뀌는 것이라 분리한다.

    force 는 직접 수정한 파일이 있어도 진행한다 — 수정본을 백업 폴더로 옮기고
    원본으로 되돌린 뒤 받는다.
    """
    steps: list[str] = []
    if force:
        bak = _backup_local_changes()
        if bak:
            steps.append(f"직접 수정한 파일을 {bak} 폴더에 백업했습니다")
    before = _git("rev-parse", "HEAD")[1]
    req_before = _git("hash-object", "webapp/requirements.txt")[1]

    code, out = _git("pull", "--ff-only", timeout=180)
    if code != 0:
        return {"ok": False, "detail": f"내려받기 실패: {out[:300]}"}
    after = _git("rev-parse", "HEAD")[1]
    STATE_FILE.write_text(json.dumps({"previous": before, "current": after}),
                          encoding="utf-8")

    steps.append("코드를 받았습니다")
    if _git("hash-object", "webapp/requirements.txt")[1] != req_before:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r",
                            str(REPO / "webapp" / "requirements.txt")],
                           capture_output=True, encoding="utf-8",
                           errors="replace", timeout=600)
        steps.append("구성 요소를 갱신했습니다" if r.returncode == 0
                     else f"구성 요소 갱신 실패: {(r.stderr or '')[-200:]}")
    return {"ok": True, "steps": steps, "restart_required": True,
            "from": before[:7], "to": after[:7]}


def rollback() -> dict:
    """직전 버전으로. 업데이트가 깨졌을 때 쓸 유일한 길이다."""
    if not STATE_FILE.exists():
        return {"ok": False, "detail": "되돌릴 기록이 없습니다"}
    prev = json.loads(STATE_FILE.read_text(encoding="utf-8")).get("previous")
    if not prev:
        return {"ok": False, "detail": "되돌릴 기록이 없습니다"}
    code, out = _git("reset", "--hard", prev)
    if code != 0:
        return {"ok": False, "detail": out[:300]}
    return {"ok": True, "restart_required": True, "to": prev[:7]}


def restart_now() -> None:
    """재시작 종료코드로 죽는다. 실행 스크립트의 루프가 다시 띄운다.

    `os._exit` 여야 한다. 이 함수는 응답을 먼저 보내려고 **타이머 스레드**에서
    불리는데, 거기서 `sys.exit()` 은 그 스레드만 끝내고 프로세스는 멀쩡히 산다 —
    종료코드가 나가지 않으니 실행 스크립트의 루프도 돌지 않는다. 그러면 `git pull`
    은 됐는데 **옛 코드가 계속 도는** 상태가 되고, 사용자는 업데이트가 된 줄 안다.
    정리할 것은 없다: 감사 로그는 매번 닫고, 임시 업로드는 기한으로 청소된다.
    """
    os._exit(RESTART_CODE)
