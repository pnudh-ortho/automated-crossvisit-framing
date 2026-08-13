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

    def to_json(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def inventory(program_dir: Path, data_dir: Path) -> Inventory:
    """지워질 것과 남을 것을 센다. 화면이 이걸 그대로 보여준다."""
    inv = Inventory(program_dir=str(program_dir), data_dir=str(data_dir))
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
{data}rmdir /s /q "{prog}"
echo.
echo Uninstall complete.
echo.
pause
"""

_SH = """#!/bin/bash
# Removes the program folder after the app has exited.
sleep 2
{data}rm -rf "{prog}"
echo
echo "Uninstall complete."
echo
read -r -p "Press Enter to close."
"""


def prepare(program_dir: Path, data_dir: Path, *, drop_data: bool = False) -> dict:
    """삭제 스크립트를 **프로그램 폴더 바깥**에 만든다.

    안에 만들면 자기 자신을 지우면서 실행돼 결과가 불확실하다.
    """
    parent = program_dir.parent
    win = os.name == "nt"
    name = "uninstall_finish.bat" if win else "uninstall_finish.command"
    tmpl = _BAT if win else _SH
    if drop_data:
        line = (f'rmdir /s /q "{data_dir}"\n' if win else f'rm -rf "{data_dir}"\n')
    else:
        line = ""
    script = parent / name
    body = tmpl.format(prog=program_dir, data=line)
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
            "drop_data": drop_data,
            "detail": ("앱을 끝낸 뒤 이 파일을 실행하면 삭제가 끝납니다: "
                       f"{script}")}
