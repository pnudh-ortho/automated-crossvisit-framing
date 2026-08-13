"""
프로그램 삭제 — **환자 자료를 실수로 지우지 않는 것**이 이 모듈의 유일한 목적이다.

    inv = inventory(root)           # 무엇이 얼마나 있나
    prepare(root, drop_data=False)  # 삭제 스크립트를 만들고 앱을 끝낸다

### 왜 앱이 자기 폴더를 직접 못 지우나

Windows 는 실행 중인 파일을 잠근다. `.venv\\Scripts\\python.exe` 가 돌고 있으므로
그 폴더를 지울 수 없다. 그래서 **삭제 스크립트를 만들어 두고 앱이 끝난 뒤** 그 스크립트가
지운다. 사용자는 그 파일을 한 번 더 실행하면 된다 — 되돌릴 수 없는 일이므로 확인이
한 번 더 있는 편이 낫다.

### 환자 자료는 기본으로 남긴다

의료 기록이다. `drop_data=True` 를 **명시**해야만 지운다. 지우기로 했더라도 환자 수와
용량을 먼저 보여주고, 화면에서 한 번 더 확인을 받는다.

프로그램 폴더와 자료 폴더가 애초에 떨어져 있어서(`~/ortho-webapp/`) 프로그램만 지우는
것이 자연스러운 기본값이 된다.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


def _size(p: Path) -> int:
    if not p.exists():
        return 0
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


# 설치 스크립트가 **자기가 깐 것**을 한 줄에 하나씩 적어 두는 자리.
# 원래 있던 Git·Python 을 지우면 사용자의 다른 작업이 깨지므로, 이 기록에 있는
# 것만 지운다. 옛 설치본에는 이 파일이 없고, 그때는 아무것도 지우지 않는다.
TOOLS_FILE = ".installed_tools"

# winget 패키지 id → 화면에 보일 이름
TOOL_NAMES = {"Git.Git": "Git", "Python.Python.3.12": "Python 3.12"}


def installed_tools(program_dir: Path) -> list[str]:
    """이 설치본이 winget 으로 깐 도구들. 없으면 빈 목록."""
    try:
        lines = (program_dir / TOOLS_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [x.strip() for x in lines if x.strip() and not x.startswith("#")]


def _present(tool_id: str) -> bool:
    """이 PC 에 그 도구가 있나 — 없는 것을 지우겠냐고 묻지 않으려고."""
    if tool_id == "Git.Git":
        return shutil.which("git") is not None
    if tool_id.startswith("Python."):
        return any(shutil.which(x) for x in ("py", "python", "python3"))
    return False


def tool_options(program_dir: Path) -> list[dict]:
    """삭제 화면이 물어볼 도구 목록.

    기록(`.installed_tools`)이 생기기 **전에 설치한 사람**이 더 많다. 기록이 없다고
    선택지를 안 주면 그 사람들은 영영 못 지운다. 그래서 이 PC 에 있는 도구는 항상
    묻되, 기록이 있는 것만 기본으로 체크해 둔다 — 원래 있던 것을 무심코 지우는
    쪽보다, 지우려던 것을 한 번 더 누르는 쪽이 낫다.
    """
    ours = set(installed_tools(program_dir))
    return [{"id": tid, "name": name, "ours": tid in ours}
            for tid, name in TOOL_NAMES.items() if _present(tid)]


@dataclass
class Inventory:
    program_dir: str = ""
    program_bytes: int = 0
    venv_bytes: int = 0
    weights_bytes: int = 0
    data_dir: str = ""
    data_bytes: int = 0
    patients: int = 0
    patient_names: list[str] = field(default_factory=list)
    # 이 설치본이 깐 도구들 (winget id) 과 사람이 읽을 이름
    tools: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    # 삭제 화면이 물어볼 목록 — [{id, name, ours}]
    tool_options: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def inventory(program_dir: Path, data_dir: Path) -> Inventory:
    """지워질 것과 남을 것을 센다. 화면이 이걸 그대로 보여준다."""
    inv = Inventory(program_dir=str(program_dir), data_dir=str(data_dir))
    inv.tools = installed_tools(program_dir)
    inv.tool_names = [TOOL_NAMES.get(t, t) for t in inv.tools]
    inv.tool_options = tool_options(program_dir)
    inv.program_bytes = _size(program_dir)
    inv.venv_bytes = _size(program_dir / ".venv")
    inv.weights_bytes = _size(program_dir / "models")
    inv.data_bytes = _size(data_dir)
    if data_dir.exists():
        pats = [d for d in data_dir.iterdir()
                if d.is_dir() and not d.name.startswith("_")]
        inv.patients = len(pats)
        inv.patient_names = sorted(d.name for d in pats)[:20]
    return inv


_BAT = """@echo off
REM Removes the program folder after the app has exited.
setlocal
timeout /t 2 /nobreak >nul
del /f /q "%USERPROFILE%\\Desktop\\CRoCs.lnk" 2>nul
del /f /q "%USERPROFILE%\\OneDrive\\Desktop\\CRoCs.lnk" 2>nul
{tools}{data}rmdir /s /q "{prog}"
echo.
echo Uninstall complete.
echo.
pause
"""

_SH = """#!/bin/bash
# Removes the program folder after the app has exited.
sleep 2
rm -f "$HOME/Desktop/CRoCs.command"
{tools}{data}rm -rf "{prog}"
echo
echo "Uninstall complete."
echo
read -r -p "Press Enter to close."
"""


def prepare(program_dir: Path, data_dir: Path, *, drop_data: bool = False,
            drop_tools: list[str] | None = None) -> dict:
    """삭제 스크립트를 **프로그램 폴더 바깥**에 만든다.

    안에 만들면 자기 자신을 지우면서 실행돼 결과가 불확실하다.

    `drop_tools` 는 함께 지울 winget 패키지 id 목록이다. 아는 id 만 받는다 —
    화면에서 온 값을 그대로 명령줄에 넣으면 안 된다.
    """
    parent = program_dir.parent
    win = os.name == "nt"
    name = "uninstall_finish.bat" if win else "uninstall_finish.command"
    tmpl = _BAT if win else _SH
    if drop_data:
        line = (f'rmdir /s /q "{data_dir}"\n' if win else f'rm -rf "{data_dir}"\n')
    else:
        line = ""
    tools = [t for t in (drop_tools or []) if t in TOOL_NAMES]
    tline = "".join(
        f'winget uninstall -e --id {t} --silent --accept-source-agreements\n'
        if win else f'echo "{t} 은(는) 직접 제거해 주세요."\n'
        for t in tools)
    script = parent / name
    body = tmpl.format(prog=program_dir, data=line, tools=tline)
    if win:
        script.write_bytes(body.replace("\n", "\r\n").encode("ascii", "replace"))
    else:
        script.write_text(body, encoding="utf-8")
        script.chmod(0o755)

    # 지금 지울 수 있는 것은 먼저 지운다 — 잠기지 않은 것들이라 실패해도 무해하다.
    freed = 0
    for sub in ("models/_installed", ".pytest_cache", "__pycache__"):
        p = program_dir / sub
        if p.exists():
            freed += _size(p)
            shutil.rmtree(p, ignore_errors=True)
    return {"ok": True, "script": str(script), "freed_bytes": freed,
            "drop_data": drop_data, "tools": tools,
            "detail": ("앱을 끝낸 뒤 이 파일을 실행하면 삭제가 끝납니다: "
                       f"{script}")}
