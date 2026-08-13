"""배포 파일 관리 — 사용자가 `models/` 에 **그냥 던져 넣으면** 알아서 찾는다.

모델 가중치와 PPT 양식처럼 **git 에 넣지 않는 것들**을 다룬다. 양식은 병원 고유
자료라 공개 저장소에 올리지 않는다.

    from weightstore import scan, Store
    st = scan()                      # models/ 를 훑어 상태를 낸다
    if not st.ready:  ...            # 화면에 안내
    p = st.path("segmentation")      # 실제 파일 경로

### 왜 폴더 하나에 던지게 하나

예전 방식은 `backend/models/framing/framing_IO_UPPER_final.onnx` 처럼 **경로를 손으로
맞추게** 했다. 파일이 6개면 6번 틀릴 수 있고, 틀려도 앱은 "모델 없음"이라고만 한다.

여기서는 `models/` 하나만 알면 된다. 무엇을 어디에 둘지는 앱이 판단한다.

### 이름과 해시를 **둘 다** 쓴다

    seg-1.1.0-20260811.onnx
    └모델┘ └버전┘ └날짜─┘

    <모델>-<버전>-<날짜>[+<변형>].<확장자>

PyTorch 휠(`torch-2.5.1+cu124-cp312-...whl`)과 같은 규칙이다. **`-` 로 항목을 가르고
`.` 은 항목 안에서만** 쓴다. 전부 점으로 이으면 버전이 어디서 끝나고 날짜가 어디서
시작하는지 파서가 모른다. 변형은 `+` 뒤에 두고, 없으면 standard 다.

    이름   어느 버전인지 — 사람도 앱도 읽는다
    해시   온전한지 — 잘린 파일과 드라이브 경고 HTML 을 잡는다

둘 다 필요하다. 해시만 쓰면 안 맞을 때 앱이 할 말이 "모르는 파일"뿐이고, 이름만
쓰면 **잘린 파일이 그대로 통과한다** — 구글 드라이브 대용량 링크는 바이러스 검사
경고 페이지를 HTML 로 저장하는 일이 흔하다.

브라우저가 `seg-1.1.0-20260811 (1).onnx` 로 저장해도 앞부분으로 알아본다.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DROP_DIR = ROOT / "models"                 # 사용자가 받은 파일을 넣는 곳
STORE_DIR = ROOT / "models" / "_installed"  # 검증을 통과한 것이 자리 잡는 곳
VERSION_FILE = ROOT / "version.json"

# `seg-1.1.0-20260811+fp16.onnx` · 브라우저가 붙인 " (1)" 도 받아준다
NAME_RE = re.compile(
    r"^(?P<name>[a-z][a-z0-9_]*)"
    r"-(?P<version>\d+(?:\.\d+)*)"
    r"-(?P<date>\d{8})"
    r"(?:\+(?P<variant>[a-z0-9_]+))?"
    r"(?:\s*\(\d+\))?"                     # "seg-1.1.0-20260811 (1).onnx"
    r"(?P<ext>\.[a-z0-9]+)?$", re.IGNORECASE)
#                        ↑ 확장자는 **없어도 된다** — zip 을 풀면 폴더가 되고,
#                          그 폴더도 같은 이름 규칙으로 알아봐야 한다


@dataclass
class Item:
    """`version.json` 이 요구하는 가중치 하나와, 지금 그게 어떤 상태인가."""
    key: str
    name: str
    version: str
    date: str
    file: str
    sha256: str
    bytes: int
    drive_url: str = ""
    variant: str = ""
    install_as: str = ""                   # **프로그램 루트 기준** 최종 경로
    min_app: str = ""
    # ── 상태 ──
    state: str = "missing"                 # ok · missing · corrupt · outdated
    found: Path | None = None
    found_version: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok"


@dataclass
class Store:
    items: list[Item] = field(default_factory=list)
    strays: list[tuple[Path, str]] = field(default_factory=list)   # (파일, 이유)

    @property
    def ready(self) -> bool:
        return all(i.ok for i in self.items)

    def get(self, key: str) -> Item | None:
        return next((i for i in self.items if i.key == key), None)

    def path(self, key: str) -> Path | None:
        it = self.get(key)
        return it.found if it and it.ok else None

    def report(self) -> str:
        """사용자에게 그대로 보여줄 수 있는 요약."""
        mark = {"ok": "✓", "missing": "✗", "corrupt": "⚠", "outdated": "↻"}
        out = [f"가중치 {sum(i.ok for i in self.items)}/{len(self.items)} 준비됨", ""]
        for i in self.items:
            line = f" {mark[i.state]} {i.key:<18}{i.file}"
            if i.detail:
                line += f"\n     → {i.detail}"
            out.append(line)
        for p, why in self.strays:
            out.append(f" ⚠ 알 수 없는 파일: {p.name}\n     → {why}")
        return "\n".join(out)


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while blk := f.read(chunk):
            h.update(blk)
    return h.hexdigest()


def parse(filename: str) -> dict | None:
    """파일명 → {name, version, date, variant, ext}. 규칙에 안 맞으면 None."""
    m = NAME_RE.match(filename)
    return m.groupdict() if m else None


def _looks_like_html(p: Path) -> bool:
    """구글 드라이브 경고 페이지가 저장된 것인가.

    대용량 파일은 바이러스 검사 확인 단계가 있어서, 단순 요청으로 받으면 파일 대신
    HTML 이 온다. 크기만 보면 몇 KB 라 "받다 만 것"처럼 보이고 원인을 못 찾는다.
    """
    try:
        head = p.open("rb").read(512).lstrip().lower()
        return head.startswith(b"<!doctype html") or head.startswith(b"<html")
    except OSError:
        return False


def scan(*, drop_dir: Path = DROP_DIR, store_dir: Path = STORE_DIR,
         version_file: Path = VERSION_FILE, verify: bool = True) -> Store:
    """`models/` 와 설치 폴더를 훑어 상태를 만든다.

    `verify=False` 면 해시를 건너뛴다 — 291MB 를 매 기동마다 읽으면 느리다. 크기가
    같고 이름이 맞으면 통과시키고, 처음 설치할 때만 완전 검증을 한다.
    """
    spec = json.loads(Path(version_file).read_text(encoding="utf-8"))
    items = [Item(key=k, **v) for k, v in spec.get("weights", {}).items()]
    store = Store(items=items)
    drop_dir.mkdir(parents=True, exist_ok=True)
    store_dir.mkdir(parents=True, exist_ok=True)

    # ── 이미 설치된 것 ──────────────────────────────────────────────────
    # **파일명으로 다시 알아보지 않는다.** 설치하면서 `install_as` 로 이름이 바뀌므로
    # (`framing_io_upper-1.0.0-...onnx` → `framing/framing_IO_UPPER_final.onnx`)
    # 이름 규칙이 깨진다. 무엇을 설치했는지는 그때 적어둔다.
    done = _read_manifest(store_dir)
    for it in items:
        rec = done.get(it.key)
        if not rec or rec.get("sha256") != it.sha256:
            continue
        dst = ROOT / it.install_as
        if dst.exists():
            it.state, it.found = "ok", dst

    # ── 사용자가 던져 넣은 것 ────────────────────────────────────────────
    # 안내문은 우리가 둔 것이라 "알 수 없는 파일"로 세면 안 된다
    cands = [p for p in drop_dir.iterdir()
             if p.is_file() and not p.name.startswith(".")
             and p.suffix.lower() not in (".txt", ".md")]
    by_name: dict[str, list[tuple[Path, dict]]] = {}
    for p in cands:
        info = parse(p.name)
        if info is None:
            if p.is_file():
                why = ("드라이브 경고 페이지가 저장된 것 같습니다. 지우고 다시 받으세요."
                       if _looks_like_html(p) else
                       "이름 규칙에 맞지 않습니다 (<모델>-<버전>-<날짜>.확장자)")
                store.strays.append((p, why))
            continue
        by_name.setdefault(info["name"].lower(), []).append((p, info))

    for it in items:
        if it.ok:                       # 이미 설치돼 있으면 그대로 둔다
            continue
        got = by_name.get(it.name.lower(), [])
        exact = [(p, i) for p, i in got
                 if i["version"] == it.version and i["date"] == it.date
                 and (i["variant"] or "") == (it.variant or "")]
        if not exact:
            if got:
                it.state = "outdated"
                it.found_version = ", ".join(sorted({i["version"] for _, i in got}))
                it.detail = (f"{it.name}-{it.found_version} 을(를) 갖고 계십니다. "
                             f"필요한 것은 {it.file} 입니다")
            else:
                it.detail = f"{it.file} 이(가) 없습니다"
            continue
        p, _ = exact[0]
        size = p.stat().st_size
        if it.bytes and size != it.bytes:
            it.state, it.found = "corrupt", p
            it.detail = (f"크기가 다릅니다 ({size:,} ≠ {it.bytes:,} 바이트). "
                         "받다 만 파일 같습니다")
            continue
        if verify and it.sha256 and not it.sha256.startswith("TODO"):
            if sha256(p) != it.sha256:
                it.state, it.found = "corrupt", p
                it.detail = "내용이 손상됐습니다. 지우고 다시 받으세요"
                continue
        it.state, it.found = "ok", p
    return store


MANIFEST = "installed.json"


def _read_manifest(store_dir: Path) -> dict:
    f = store_dir / MANIFEST
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except json.JSONDecodeError:
        return {}


def install(store: Store, *, store_dir: Path = STORE_DIR) -> list[str]:
    """검증을 통과한 것을 `install_as` 자리로 옮기고 **무엇을 넣었는지 적는다**.

    앱이 기대하는 자리(`models/_installed/framing/framing_IO_UPPER_final.onnx`,
`templates/intraoral_template.pptx`)와 배포용 이름
    (`framing_io_upper-1.0.0-20260731.onnx`)이 다르다. 배포용 이름은 버전을 담아야
    하고, 앱 쪽 이름은 이미 코드가 쓰고 있어 바꾸면 안 된다.

    옮기고 나면 파일명으로는 무엇인지 알 수 없으므로 `installed.json` 에 적는다.
    다음 기동 때 이 기록으로 확인한다 — 291MB 를 매번 해싱하지 않아도 된다.
    """
    moved = []
    store_dir.mkdir(parents=True, exist_ok=True)
    done = _read_manifest(store_dir)
    for it in store.items:
        if not it.ok or it.found is None:
            continue
        dst = ROOT / (it.install_as or f"models/_installed/{it.file}")
        if it.found == dst:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(it.found), dst)
        it.found = dst
        done[it.key] = {"file": it.install_as or it.file, "version": it.version,
                        "date": it.date, "sha256": it.sha256}
        moved.append(it.key)
    if moved:
        (store_dir / MANIFEST).write_text(
            json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")
    return moved


def _main(argv=None) -> int:
    st = scan(verify="--fast" not in (argv or []))
    print(st.report())
    if st.ready:
        moved = install(st)
        if moved:
            print(f"\n설치함: {', '.join(moved)}")
    return 0 if st.ready else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
