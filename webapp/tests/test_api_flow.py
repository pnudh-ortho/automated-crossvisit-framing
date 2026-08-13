"""
백엔드 종단 플로우 검증 (Stage 5).
초진: 세션→업로드(분류)→검수→확정, 재진: PPT선택→업로드(정합)→확정.
Mock 분류기는 파일명 힌트로 슬롯을 배정한다(front/left/right/upper/lower).

실행: cd webapp && python tests/test_api_flow.py
"""
import io
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import template as T  # noqa: E402

client = TestClient(main.app)

SLOTS = ["front", "left", "right", "upper", "lower"]
NAME = "검사환자"           # 정리 시 식별용 고유 이름
HOSP, ORTHO = "123456789", "54321"


def synth(slot, seed):
    rng = np.random.default_rng(seed)
    img = np.full((1200, 1600, 3), 25, np.uint8)
    for _ in range(150):
        c = tuple(int(x) for x in rng.integers(60, 255, 3))
        p = tuple(int(x) for x in rng.integers(0, [1600, 1200], 2))
        cv2.circle(img, p, int(rng.integers(8, 30)), c, -1)
    cv2.putText(img, slot, (500, 620), cv2.FONT_HERSHEY_SIMPLEX, 5, (255, 255, 255), 10)
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()


def upload_files(sid, transform=False):
    files = []
    for i, slot in enumerate(SLOTS):
        data = synth(slot, seed=100 + i)
        if transform:  # 재진: 살짝 회전/이동
            arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            M = cv2.getRotationMatrix2D((800, 600), 3, 1.03)
            arr = cv2.warpAffine(arr, M, (1600, 1200), borderMode=cv2.BORDER_REFLECT)
            data = cv2.imencode(".jpg", arr)[1].tobytes()
        files.append(("files", (f"{slot}.jpg", io.BytesIO(data), "image/jpeg")))
    return client.post(f"/api/upload/{sid}", files=files)


def plan_files(sid) -> set:
    """
    /api/plan 이 예고한 파일 이름 전부. 확정 후 실제 저장물과 **정확히 같아야** 한다.

    저장 전 검토 화면이 보여주는 것이 바로 이 목록이라, 여기가 어긋나면 사용자는
    실제와 다른 것을 보고 확정하게 된다. 의료 기록물에서는 그게 곧 사고다.
    """
    p = client.get(f"/api/plan/{sid}")
    assert p.status_code == 200, p.text
    p = p.json()
    out = {p["ppt"]}
    for e in p["slots"]:
        if e["empty"]:
            continue
        out.add(e["file"])
        out |= {x["file"] for x in e["extras"]}
    out |= {f["file"] for f in p["faces"]}
    return out


def cleanup(patient_dir: Path):
    if patient_dir and patient_dir.exists():
        shutil.rmtree(patient_dir, ignore_errors=True)


def main_flow():
    root = Path(main.cfg.resolve(main.cfg.paths.root))
    folder = f"{NAME}_{HOSP}_{ORTHO}"
    patient_dir = root / folder
    cleanup(patient_dir)

    # ── 초진 ──
    r = client.post("/api/session/first",
                    json={"name": NAME, "hospital_id": HOSP, "ortho_id": ORTHO})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    assert r.json()["visit"] == "A"
    print("초진 세션:", r.json()["folder"])

    rev = upload_files(sid).json()["review"]
    filled = {k: v for k, v in rev["slots"].items() if v}
    print("  분류 배정:", {k: v["label"] for k, v in filled.items()})
    assert len(filled) == 5, f"슬롯 5개가 안 채워짐: {rev['missing']}"

    planned = plan_files(sid)
    print("  저장 계획:", sorted(planned))

    r = client.post(f"/api/commit/{sid}")
    assert r.status_code == 200, r.text
    files = r.json()["files"]
    print("  확정 파일:", files)
    assert set(files) == planned, (
        f"미리보기와 실제 저장이 다르다\n계획: {sorted(planned)}\n실제: {sorted(files)}")
    ppt_name = f"{NAME}_{HOSP}_{ORTHO}.pptx"
    assert (patient_dir / ppt_name).exists()
    for idx in range(1, 6):
        assert (patient_dir / f"{ORTHO}_A ({idx}).jpg").exists(), f"({idx}) 누락"
    # INFO_BOX 초진 확인
    prs = T.load_presentation(patient_dir / ppt_name)
    info = T.find_shape(prs.slides[0], main.cfg.ppt.info_box_name)
    assert "초진" in info.text_frame.text
    print("  ✓ 초진 PPT + 사진 5장 + INFO_BOX 확인")

    # ── 재진 ──
    r = client.post("/api/session/revisit", json={"ppt_path": f"{folder}/{ppt_name}"})
    assert r.status_code == 200, r.text
    sid2 = r.json()["session_id"]
    assert r.json()["visit"] == "B", r.json()
    print("재진 세션: visit=", r.json()["visit"], "prev=", r.json()["prev_visits"])

    rev2 = upload_files(sid2, transform=True).json()["review"]
    badges = {k: v["badge"] for k, v in rev2["slots"].items() if v}
    refv = {k: v["ref_visit"] for k, v in rev2["slots"].items() if v}
    print("  정합 배지:", badges)
    print("  채택 기준:", refv)
    assert len(rev2["missing"]) == 0

    planned2 = plan_files(sid2)
    r = client.post(f"/api/commit/{sid2}")
    assert r.status_code == 200, r.text
    print("  재진 확정 파일:", r.json()["files"])
    assert set(r.json()["files"]) == planned2, (
        f"재진 미리보기와 실제 저장이 다르다\n계획: {sorted(planned2)}\n"
        f"실제: {sorted(r.json()['files'])}")
    for idx in range(1, 6):
        assert (patient_dir / f"{ORTHO}_B ({idx}).jpg").exists(), f"재진 ({idx}) 누락"
    prs2 = T.load_presentation(patient_dir / ppt_name)
    assert len(prs2.slides) == 2, f"슬라이드 수 {len(prs2.slides)}"
    # 두 번째 슬라이드 INFO_BOX 재진 B
    info2 = T.find_shape(prs2.slides[1], main.cfg.ppt.info_box_name)
    assert "재진 B" in info2.text_frame.text, info2.text_frame.text
    print("  ✓ 재진 슬라이드 추가 + 사진 5장 + INFO_BOX '재진 B' 확인")

    cleanup(patient_dir)
    print("\n✅ API 종단 플로우(초진+재진) 통과")


if __name__ == "__main__":
    main_flow()
