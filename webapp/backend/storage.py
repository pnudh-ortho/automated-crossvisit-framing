"""
저장·이력 안전장치 (의료 기록물)

원칙:
 - 원본 무손상: 업로드 원본은 절대 수정하지 않고 복사본으로만 처리.
 - 원자적 확정: 임시 폴더에 전부 기록 후 대상 폴더로 이동, 실패 시 롤백.
 - 이력 로그: 모든 저장/분류/수정 이력을 JSONL로 기록.
 - 명시적 확정 없이는 디스크에 아무것도 쓰지 않음(Transaction.commit 호출 전까지 staging).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path


# ── 이력 로그 ─────────────────────────────────────────────────────────────────
def append_audit(log_file: str | Path, record: dict) -> None:
    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now().isoformat(timespec="seconds"), **record}
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── 원본 무손상 복사 ──────────────────────────────────────────────────────────
def copy_original(src: str | Path, dst: str | Path) -> Path:
    """원본을 읽기만 하여 복사. 원본 파일은 변경하지 않는다."""
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)  # 메타데이터/원본 무변경
    return dst


# ── 원자적 확정 ───────────────────────────────────────────────────────────────
class Transaction:
    """
    확정 전 모든 결과를 임시 staging 폴더에 모았다가, commit() 시 대상 환자
    폴더로 이동한다. 대상과 같은 상위 폴더에 staging을 두어 rename 이동이
    같은 파일시스템에서 이뤄지도록 한다.

    사용:
        with Transaction(patient_dir) as tx:
            tx.stage_file(local_img, "12345_A (1).jpg")
            tx.stage_pptx(prs, "홍길동_...pptx")
            tx.commit()     # 명시적 확정
        # 예외 발생 시 자동 롤백(임시폴더 삭제, 대상 미변경)
    """

    def __init__(self, patient_dir: str | Path, overwrite: bool = True):
        self.patient_dir = Path(patient_dir)
        self.overwrite = overwrite
        self._staged: list[tuple[Path, str]] = []  # (staged_path, final_name)
        self._tmp: Path | None = None
        self._committed = False

    def __enter__(self) -> "Transaction":
        parent = self.patient_dir.parent
        parent.mkdir(parents=True, exist_ok=True)
        self._tmp = Path(tempfile.mkdtemp(prefix=".tx_", dir=str(parent)))
        return self

    def stage_file(self, src: str | Path, final_name: str) -> Path:
        dst = self._tmp / final_name
        dst.parent.mkdir(parents=True, exist_ok=True)   # "raw/…" 같은 하위 경로 허용
        shutil.copy2(str(src), str(dst))   # 촬영시각(mtime)을 보존한다
        self._staged.append((dst, final_name))
        return dst

    def stage_bytes(self, data: bytes, final_name: str) -> Path:
        dst = self._tmp / final_name
        with open(dst, "wb") as f:
            f.write(data)
        self._staged.append((dst, final_name))
        return dst

    def stage_pptx(self, prs, final_name: str) -> Path:
        dst = self._tmp / final_name
        prs.save(str(dst))
        self._staged.append((dst, final_name))
        return dst

    def commit(self) -> list[Path]:
        """staging 파일들을 환자 폴더로 이동(명시적 확정)."""
        self.patient_dir.mkdir(parents=True, exist_ok=True)
        moved: list[Path] = []
        try:
            for staged, final_name in self._staged:
                target = self.patient_dir / final_name
                if target.exists() and not self.overwrite:
                    raise FileExistsError(f"이미 존재: {target}")
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(str(staged), str(target))  # 같은 FS면 원자적 rename
                moved.append(target)
            self._committed = True
            return moved
        except Exception:
            # 부분 이동 롤백: 방금 옮긴 것들 제거
            for m in moved:
                try:
                    m.unlink()
                except OSError:
                    pass
            raise

    def __exit__(self, exc_type, exc, tb):
        # 임시 폴더 정리(성공/실패 무관)
        if self._tmp and self._tmp.exists():
            shutil.rmtree(self._tmp, ignore_errors=True)
        return False


def patient_dir_path(root: str | Path, folder_name: str) -> Path:
    return Path(root) / folder_name
