"use strict";
/* ══ Fastest Lap — 화면 ════════════════════════════════════════════════════════
   환자를 고르는 것까지는 본편과 똑같다. 거기서 이 모드를 켜면
     · 사진 넣는 칸이 **좌우로 갈린다** — 왼쪽 정합용 기준, 오른쪽 오늘 사진
     · 자동 분류 화면이 **위아래 두 줄**이 된다 — 위 기준, 아래 오늘
     · 저장이 **덱을 만들지 않는다** — 사진만 환자 폴더로 간다

   차수는 여전히 PPT 가 진실이다. 이 모드는 그것을 읽어 쓰기만 하고, 차수를
   덱에 남기는 일은 사람이 PowerPoint 에서 한다. 저장 검토가 그 사실을 알린다.

   app.js 에는 갈림길만 두고 화면은 전부 여기서 그린다 — 본편 코드가 그대로
   남아야 fastest_lap 브랜치로 cherry-pick 이 계속 흐른다. */

/* ── 모드 ────────────────────────────────────────────────────────────────── */
function setFast(on){
  FAST = !!on;
  document.body.dataset.mode = FAST ? "fast" : "full";
  // 버튼 모습은 켜든 끄든 같다 — 지금 어느 방식인지는 오른쪽 카드가 말한다.
  // 눌린 상태는 보조기기를 위해 aria 로만 남긴다.
  const b = el("btn-fast");
  if(b) b.setAttribute("aria-pressed", String(FAST));
}

function toggleFast(){
  // 모드는 세션에 박힌다 — 이미 잡아 둔 구도·기준영상이 그 규칙 위에서 계산된
  // 값이라, 진행 중에 갈아타면 화면과 서버가 서로 다른 것을 믿게 된다.
  if(SESSION){
    if(!confirm("진행 중인 작업을 버리고 모드를 바꿀까요?")) return;
    resetSession();
  }
  setFast(!FAST);
  drawDetail();
}

/* ── Setup: 좌우 두 드롭존 ───────────────────────────────────────────────── */
const POOL_LABEL = {ref: "정합용 기준 사진", cur: "오늘 사진"};

function fastZonesHtml(){
  const zone = (pool, sub) => `
       <div class="upzone" data-pool="${pool}">
         <h3>${POOL_LABEL[pool]} <span class="aux">${sub}</span>
           <span class="aux" id="n-${pool}"></span></h3>
         <div class="dropzone" id="dz-${pool}">
           <div class="dz-empty" id="dz-${pool}-empty">
             <p class="dz-main">${pool === "ref" ? "지난 차수 사진을 여기에" : "오늘 찍은 사진을 여기에"}</p>
             ${pool === "ref" ? `<p class="dz-sub">비워두면 정합 없이 진행합니다</p>` : ""}
             <p class="dz-sub">DRAG<i>·</i><button id="pick-${pool}">BROWSE</button></p>
           </div>
           <div class="thumbs" id="thumbs-${pool}"></div>
           <input type="file" id="file-${pool}" multiple accept="image/*" hidden>
         </div>
       </div>`;
  return `
     <div class="sec">
       <h3>사진 추가 <span class="aux"><span id="stage-msg"></span><span id="staged-n"></span></span></h3>
       <div class="upzones">${zone("ref", "(재진)")}${zone("cur", "")}</div>
       <button class="btn primary wide" id="btn-go" disabled>자동 분류로 ▶</button>
       <p class="tip">기준 사진은 넣는 즉시 분류·준비가 백그라운드로 돕니다 —
         오른쪽을 채우고 넘어갈 때쯤이면 끝나 있습니다.</p>
     </div>`;
}

/* ── 환자 없이 진행하기 ────────────────────────────────────────────────────
   환자를 고르면 차수·레이아웃·이름 규칙이 전부 그 환자에게서 온다. 고르지
   않으면 그럴 것이 없으므로 **저장 폴더 이름과 파일 접두어**를 직접 적는다.
   등록되지 않은 사람의 사진을 한 번 자르고 넘기는 자리다. */
function fastFolderHtml(){
  const folder = (SESSION && SESSION.folder) || "";
  const prefix = (SESSION && SESSION.prefix) || "";
  const own = !!(prefix && prefix !== folder);
  return `
     <div class="sec">
       <h3>저장 위치 <span class="aux">환자 등록 없이 사진만 저장합니다</span></h3>
       <div class="flbar">
         <label class="flfld">저장 폴더 이름
           <input id="fl-folder" autocomplete="off" placeholder="예: 김하늘_260831"
                  value="${esc(folder)}"></label>
         <button class="btn" type="button" id="fl-browse"
                 title="폴더 선택 창을 엽니다 — 없는 폴더는 그 창에서 만들 수 있습니다">📁 찾아보기</button>
         <label class="flchk" title="끄면 접두어는 폴더 이름과 같습니다">
           <input type="checkbox" id="fl-pfx-on"${own ? " checked" : ""}> 사진 접두어 직접 입력</label>
         <input id="fl-prefix" autocomplete="off" placeholder="폴더 이름과 동일"
                value="${esc(own ? prefix : "")}"${own ? "" : " disabled"}>
       </div>
       <!-- 폴더 선택 창이 안 뜨는 환경에서만 쓰는 앱 안 폴더 트리 -->
       <div class="card" id="fl-picker" hidden></div>
       <p class="tip">저장 위치 <code id="fl-dest">—</code></p>
       <p class="tip" style="margin-bottom:3px">이렇게 저장됩니다</p>
       <div class="egname" id="fl-egname">—</div>
     </div>` + fastZonesHtml();
}

/* 파일 이름 규칙(별칭·번호·구분자)은 설정에서 온다. 화면이 지어내면 실제 저장물과
   갈라지므로 서버가 쓰는 값을 그대로 받아 둔다. 한 번만 받고 들고 있는다. */
let FL_NAMING = null;

async function fastNaming(){
  if(FL_NAMING) return FL_NAMING;
  try{ FL_NAMING = await api("/api/fl/naming"); }
  catch(e){ FL_NAMING = null; }
  return FL_NAMING;
}

/* 지금 적힌 접두어로 **실제로 저장될 이름**을 보여준다.

   규칙(별칭·번호·구분자)이 설정에 있고 얼굴은 번호가 이어 오르므로, 화면에서
   조립하면 언젠가 실제와 갈라진다. 그래서 서버에 접두어만 주고 **완성된 이름**을
   받는다. 타자마다 부르지 않게 잠깐 모아서 한 번 부른다. */
let FL_EG_T = null;

function fastPrefix(){
  const folder = (el("fl-folder") && el("fl-folder").value.trim()) || "";
  const own = el("fl-pfx-on") && el("fl-pfx-on").checked;
  const typed = own ? (el("fl-prefix").value || "").trim() : "";
  // 절대 경로를 골랐으면 마지막 칸만 접두어가 된다 (서버의 _label 과 같은 규칙)
  return typed || (isPath(folder) ? lastSeg(folder) : folder);
}

/* 예시 두 줄 — 구내와 얼굴을 나눈다.
   첫 이름은 통째로 보이고 나머지는 접두어를 뗀 꼬리만 남긴다. 그래야 무엇이
   달라지는지(번호가 오르는 것)가 눈에 들어오고 줄이 길어지지 않는다. */
function egTail(list, head){
  // `head` 는 이름들이 공유하는 머리(접두어+구분자). 서버가 알려준다 — 화면이
  // 접두어만 떼면 구분자가 남아 "(2)" 가 "_(2)" 로 보인다.
  return list.map(x => x.slice(head.length).replace(/\.jpg$/, "").trim()).join(" ");
}

function egBlock(ex){
  if(!ex || !ex.io || !ex.io.length) return "—";
  const head = ex.join || ex.prefix || "";
  const row = (label, list, note) =>
    `<span class="egrow"><i>${label}</i>` +
      `<span class="ident">${esc(list[0])}</span>` +
      (list.length > 1 ? `<b>이어서 ${esc(egTail(list.slice(1), head))}</b>` : "") +
      (note ? `<em>${note}</em>` : "") + `</span>`;
  return row("구내", ex.io, "") + row("얼굴", ex.face, "장수만큼 이어집니다");
}

function fastEgName(){
  const box = el("fl-egname"); if(!box) return;
  const prefix = fastPrefix();
  if(!prefix){ box.textContent = "—"; return; }
  clearTimeout(FL_EG_T);
  FL_EG_T = setTimeout(async () => {
    try{
      const r = await api("/api/fl/naming?prefix=" + encodeURIComponent(prefix));
      box.innerHTML = egBlock(r.example);
    }catch(e){ /* 예시가 없다고 진행을 막지는 않는다 */ }
  }, 200);
}

function fastDest(){
  fastEgName();
  const box = el("fl-dest"); if(!box) return;
  const root = fastCurrentRoot();
  const folder = (el("fl-folder") && el("fl-folder").value.trim()) || "";
  if(!folder){ box.textContent = joinPath(root, "…"); return; }
  // 탐색기로 루트 밖을 골랐으면 이미 완성된 경로다
  box.textContent = isPath(folder) ? folder : joinPath(root, folder);
}

/* 폴더 이름은 저장할 때 비로소 쓰인다 — 사진을 넣은 뒤에 고쳐도 된다.
   세션이 이미 열려 있으면 버리지 않고 이름만 갈아 끼운다. */
async function fastSyncNames(){
  fastDest();
  if(!SESSION || picked) return;
  const folder = el("fl-folder").value.trim();
  if(!folder) return;
  const prefix = el("fl-pfx-on").checked ? el("fl-prefix").value.trim() : "";
  try{
    const r = await api(`/api/fl/session/${SESSION.session_id}/names`, {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({folder, prefix})});
    SESSION.folder = r.folder; SESSION.prefix = r.prefix;
    SESSION.ids = {...SESSION.ids, name: r.folder};
    stageMsg("");
  }catch(e){ stageMsg(e.message, "err"); }
}

async function fastFolderSession(){
  const fi = el("fl-folder");
  const folder = (fi && fi.value.trim()) || "";
  if(!folder){
    if(fi){ fi.classList.add("attn"); fi.focus(); }
    throw new Error("저장할 폴더 이름을 먼저 적어 주세요");
  }
  if(fi) fi.classList.remove("attn");
  if(SESSION) return SESSION;          // 이미 열렸다 — 이름은 fastSyncNames 가 맞춘다
  const prefix = el("fl-pfx-on").checked ? el("fl-prefix").value.trim() : "";
  const r = await api("/api/fl/session", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({folder, prefix})});
  startSession(r);
  return r;
}

/* 저장할 폴더 고르기.

   **앱 안 폴더 트리**를 쓴다. 운영체제 창(main 의 저장 위치 고르기)이 더 익숙하긴
   한데, 그 창은 시작 위치를 지정해도 UNC 경로에서는 듣지 않는다 — WSL 에 저장
   루트를 두면 `\\wsl.localhost\...` 가 되어 창이 엉뚱한 자리에서 열린다.
   여기서는 **지금 고른 저장 위치에서 여는 것**이 요점이라 서버가 직접 훑는 쪽을
   쓴다. 어느 환경에서든 같은 자리에서 열리고, 새 폴더도 그 안에서 만든다. */
/* 서버는 **제 운영체제의 경로**를 그대로 준다 — 배포처(Windows)에서는
   `C:\CRoCs_data\...` 다. 여기서 경로를 다룰 때는 두 구분자를 다 봐야 한다.
   `/` 만 보면 Windows 에서 "루트 아래면 이름만" 도, 저장 위치 표시도, 접두어
   뽑기도 전부 어긋난다 — 미리보기가 실제 저장 이름과 달라진다. */
const SEP = /[\\/]/;
const isPath = s => SEP.test(s || "");
const lastSeg = s => String(s || "").split(SEP).filter(Boolean).pop() || "";
const joinPath = (root, name) =>
  !root ? name : root + (SEP.test(root.slice(-1)) ? "" : (root.includes("\\") ? "\\" : "/")) + name;

function fastCurrentRoot(){
  // 상단 '저장 위치' 선택기가 진실이다 — 페이지를 연 뒤에 바꿨을 수 있다.
  const sel = el("root-sel");
  return (sel && sel.value) || (HEALTH && HEALTH.root) || "";
}

function fastPickFolder(){
  const host = el("fl-picker"); if(!host) return;
  if(!host.hidden){ closePicker(host); return; }   // 다시 누르면 닫힌다
  host.hidden = false;
  drawRootPicker(fastCurrentRoot(), host, path => fastSetFolder(path),
    {title: "저장할 폴더 고르기",
     hint: "사진이 들어갈 폴더를 고르세요 — 없으면 ＋ 새 폴더로 만듭니다",
     ok: "여기에 저장"});
}

function fastSetFolder(path){
  const root = fastCurrentRoot();
  // 저장 루트 **바로 아래**면 이름만 들고 있는다 — 나중에 루트를 옮겨도 따라간다.
  // 그 밖(외장 드라이브·바탕화면 등)이면 고른 경로를 그대로 쓴다.
  const rest = root && path.startsWith(root) ? path.slice(root.length) : "";
  const rel = SEP.test(rest.slice(0, 1)) ? rest.slice(1) : "";
  const fi = el("fl-folder");
  fi.value = (rel && !isPath(rel)) ? rel : path;
  fi.classList.remove("attn");
  fastSyncNames();
}

function fastBindZones(){
  // 환자 없이 진행하는 화면에서만 있는 칸들
  const fi = el("fl-folder");
  if(fi){
    const br = el("fl-browse"); if(br) br.onclick = fastPickFolder;
    const pfx = el("fl-pfx-on"), pin = el("fl-prefix");
    fi.oninput = () => { fi.classList.remove("attn"); fastDest(); };
    fi.onchange = fastSyncNames;
    pfx.onchange = () => { pin.disabled = !pfx.checked; if(!pfx.checked) pin.value = ""; fastSyncNames(); };
    pin.oninput = fastEgName;
    pin.onchange = fastSyncNames;
    fastDest();
    fastNaming().then(fastEgName);   // 규칙이 도착하면 예시를 다시 그린다
  }
  for(const pool of ["ref", "cur"]){
    const dz = el(`dz-${pool}`), fi = el(`file-${pool}`), pick = el(`pick-${pool}`);
    if(!dz) continue;
    if(pick) pick.onclick = e => { e.stopPropagation(); fi.click(); };
    dz.onclick = () => fi.click();
    fi.onchange = () => { if(fi.files.length) addFiles([...fi.files], pool); fi.value = ""; };
    for(const ev of ["dragover", "dragenter"])
      dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add("over"); });
    for(const ev of ["dragleave", "drop"])
      dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove("over"); });
    dz.addEventListener("drop", e => {
      const files = [...e.dataTransfer.files].filter(f => f.type.startsWith("image/"));
      if(files.length) addFiles(files, pool);
    });
  }
  el("btn-go").onclick = runClassify;
}

function fastDrawZones(){
  let cur = 0;
  for(const pool of ["ref", "cur"]){
    const box = el(`thumbs-${pool}`); if(!box) continue;
    const list = STAGED.filter(p => p.pool === pool);
    if(pool === "cur") cur = list.length;
    const empty = el(`dz-${pool}-empty`);
    if(empty) empty.hidden = list.length > 0;
    el(`dz-${pool}`).classList.toggle("filled", list.length > 0);
    box.innerHTML = list.map(p =>
      `<figure class="th"><img src="${p.card || p.thumb}" alt="" loading="lazy">` +
      `<button class="x" data-pid="${p.id}" title="빼기">×</button></figure>`).join("") +
      (list.length ? `<button class="th add" data-pool="${pool}" title="사진 더 추가">＋</button>` : "");
    for(const b of box.querySelectorAll(".x"))
      b.onclick = e => { e.stopPropagation(); dropStaged(b.dataset.pid); };
    const n = el(`n-${pool}`); if(n) n.textContent = list.length ? `${list.length}장` : "";
  }
  // 오늘 사진이 없으면 넘어갈 것이 없다 — 기준만으로는 만들 결과물이 없다.
  const go = el("btn-go"); if(go) go.disabled = !cur;
  setStep("setup", cur ? "done" : "",
          SESSION ? [SESSION.ids.name, SESSION.visit].filter(Boolean).join(" · ") : "");
  setStep("pre", "", cur ? `${cur}장 대기` : "대기");
}

/* ── Pre: 짝맞춤 ─────────────────────────────────────────────────────────────
   화면이 가로로 기니 기준·현재를 위아래 두 줄로 깔고, 카테고리가 열이 되어
   위아래로 짝이 맞는다. 기준을 안 넣었으면 아래 줄 하나뿐이다. */
function fastCard(p, isPrimary){
  const low = p.confidence < 0.75;
  return `<figure class="ph-card${isPrimary ? " primary" : ""}" draggable="true"` +
      ` data-pid="${p.id}" data-pool="${p.pool}">` +
    `<div class="pcimg">` +
      `<img src="${p.card || p.thumb}" alt="" draggable="false"` +
        `${p.flip_v ? ` class="fv"` : ""} loading="lazy">` +
      // 반전은 분류가 끝나야 정할 수 있다 — 기본값이 카테고리에서 오기 때문이다.
      // 카드는 화면에서 뒤집어 보이고(.fv), 검수 캔버스도 그린다. 파일에 실제로
      // 구워지는 것은 확정 저장 때뿐이다 — 원본은 끝까지 손대지 않는다.
      `<button type="button" class="flipbar${p.flip_v ? " on" : ""}" data-pid="${p.id}"` +
        ` draggable="false" title="이 사진을 위아래로 뒤집습니다">` +
        `↕ 상하반전${p.flip_v ? " 켜짐" : ""}</button>` +
    `</div>` +
    `<figcaption${low ? ` class="low"` : ""}>${p.label || "—"} ` +
      `${Math.round((p.confidence || 0) * 100)}%</figcaption>` +
    `</figure>`;
}

function fastBinHtml(slot, label, pool, list){
  const face = slot === "FACE";
  return `<div class="bin${face ? " face" : ""}" data-slot="${slot}" data-pool="${pool}">` +
    `<div class="bin-h">${label}` +
      (pool === "ref" ? ` <span class="poolmark">기준</span>` : "") +
      `<span class="hr"><span class="cnt">${list.length || ""}</span></span></div>` +
    `<div class="bin-body${face ? " grid3" : ""}">` +
      (list.length ? list.map((p, i) => fastCard(p, i === 0)).join("")
                   : `<p class="bin-empty">비어 있음</p>`) +
    `</div></div>`;
}

function fastOthersHtml(label, list, pool){
  return `<div class="bin-h">${label}` +
      (pool === "ref" ? ` <span class="poolmark">기준</span>` : "") +
      `<span class="cnt">${list.length || ""}</span></div>` +
    `<div class="bin-body row">` +
      (list.length ? list.map(p => fastCard(p, false)).join("")
                   : `<p class="bin-empty">비어 있음</p>`) +
    `</div>`;
}

function fastDrawPairs(){
  const R = REVIEW, hasRef = !!R.has_ref;
  const cols = BINS.filter(b => b.key !== "FACE");
  const cur = R.bins || {}, ref = R.ref_bins || {};
  const face = cur.FACE || [];
  const others = (R.others && R.others.cur) || [];
  const refOthers = (R.others && R.others.ref) || [];

  if(!hasRef){
    // 기준 사진이 없으면 **짝지을 것이 없다.** 그때는 본편과 같은 판을 그대로
    // 쓴다 — 한 줄짜리 격자를 카드 높이만큼 늘리면 상자만 커지고 사진은 그대로라
    // 빈 공간만 남는다. 얼굴도 본편처럼 두 칸을 차지하며 같은 줄에 선다.
    el("bins").className = "bins";
    // **BINS 순서 그대로** 돈다 — 본편은 FACE 가 맨 앞(두 칸)이고 그 뒤로 다섯
    // 자리가 온다. 여기서 순서를 바꾸면 "같은 판"이 아니게 된다.
    el("bins").innerHTML =
      BINS.map(b => fastBinHtml(b.key, b.label, "cur", cur[b.key] || [])).join("");
    el("bin-others").className = "bin wide";
    el("bin-others").dataset.pool = "cur";
    el("bin-others").innerHTML = fastOthersHtml("OTHERS", others, "cur");
  }else{
    // 위 기준 가로줄 · 점선 · 아래 오늘 가로줄. 카테고리가 열이 되어 위아래로
    // 짝이 맞는다.
    const row = bins =>
      cols.map(b => fastBinHtml(b.key, b.label, bins === ref ? "ref" : "cur",
                                bins[b.key] || [])).join("");
    const link = cols.map(b => {
      const n = (ref[b.key] || []).length, c = (cur[b.key] || []).length;
      const cls = !c ? "miss" : (n ? "ok" : "excl");
      return `<div class="pairlink ${cls}">${!c ? "빈 자리" : n ? "정합" : "프레이밍"}</div>`;
    }).join("");
    el("bins").className = "pairs";
    el("bins").innerHTML = row(ref) + link + row(cur);

    // 얼굴과 OTHERS 는 짝을 맞출 것이 아니라 아래에 가로로 쌓는다.
    el("bin-others").className = "binstack";
    el("bin-others").removeAttribute("data-pool");
    el("bin-others").innerHTML =
        fastBinHtml("FACE", "FACE", "cur", face)
      + `<div class="bin" data-slot="" data-pool="cur">`
        + fastOthersHtml("OTHERS", others, "cur") + `</div>`
      + (refOthers.length
          ? `<div class="bin" data-slot="" data-pool="ref">`
            + fastOthersHtml("OTHERS", refOthers, "ref") + `</div>`
          : "");
  }

  // 다섯 자리를 다 채우지 않아도 넘어간다 — 사진이 빠지는 날이 있고, 확정 저장도
  // 빈 자리를 되묻고 넘어간다. 그 앞에서만 막아 두면 사람은 되돌아갈 길이 없다.
  // 다만 오늘 사진이 한 장도 없으면 만들 결과물이 없으므로 그때는 막는다.
  const missing = cols.filter(b => !(cur[b.key] || []).length);
  const filled = cols.length - missing.length;
  el("pre-n").textContent = `${STAGED.filter(p => p.pool === "cur").length}장`
    + (missing.length ? ` · 빈 슬롯 ${missing.length}` : "");
  // 얼굴만 찍은 날도 있다 — 구내가 하나도 없어도 저장할 것이 있으면 넘어간다.
  const any = filled || face.length;
  const go = el("btn-toproc");
  go.disabled = !any;
  go.title = !any ? "사진을 한 장 이상 배정해 주세요"
    : missing.length ? `빈 자리 ${missing.length}곳 — ${missing.map(b => b.label).join(", ")}`
                       + ". 이대로 진행할 수 있고, 저장할 때 한 번 더 묻습니다."
    : "";
  fastBindDnD();
}

function fastBindDnD(){
  for(const f of document.querySelectorAll(".ph-card"))
    f.ondragstart = e => {
      e.dataTransfer.setData("pid", f.dataset.pid);
      e.dataTransfer.setData("pool", f.dataset.pool);
    };
  for(const bin of document.querySelectorAll(".bin")){
    bin.ondragover = e => { e.preventDefault(); bin.classList.add("over"); };
    bin.ondragleave = () => bin.classList.remove("over");
    bin.ondrop = e => {
      e.preventDefault(); bin.classList.remove("over");
      // 풀을 건너뛰는 이동은 받지 않는다 — 기준 사진을 오늘 상자에 넣으면
      // 서버는 제 풀의 상자에 넣으므로 화면과 결과가 어긋난다.
      if(e.dataTransfer.getData("pool") !== bin.dataset.pool) return;
      const pid = e.dataTransfer.getData("pid");
      if(pid) assign(pid, bin.dataset.slot || null, dropIndex(bin, e));
    };
  }
  for(const b of document.querySelectorAll(".flipbar"))
    b.onclick = e => { e.stopPropagation(); fastFlip(b.dataset.pid); };
}

async function fastFlip(pid){
  const p = STAGED.find(x => x.id === pid); if(!p) return;
  try{
    const r = await api("/api/fl/flip", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({session_id: SESSION.session_id, photo_id: pid,
                            on: !p.flip_v})});
    REVIEW = r.review; STAGED = r.photos;
    // 그림 자체는 그대로다 — 뒤집는 것은 화면이다. 다시 받을 것이 없다.
    fastDrawPairs();
  }catch(e){ preMsg(e.message, "err"); }
}

/* 정합은 슬롯별로 **병렬**이라 한 번만 부른다. 도는 동안 진행은 폴링해서 보인다 —
   본편처럼 자리를 하나씩 부르면 병렬로 만든 이득이 사라진다. */
const PROG_TEXT = {wait: "대기", run: "정합 중", reg: "정합됨",
                   frame: "프레이밍", fallback: "프레이밍(정합 실패)"};

async function fastRegister(){
  const b = el("btn-toproc"), label = b.textContent;
  b.disabled = true;
  const box = el("reg-progress");
  const paint = prog => {
    if(!box) return;
    const rows = BINS.filter(x => x.key !== "FACE" && prog[x.key])
      .map(x => `<span class="pg" data-st="${prog[x.key]}">${x.label}` +
                `<i>${PROG_TEXT[prog[x.key]] || prog[x.key]}</i></span>`).join("");
    box.innerHTML = rows; box.hidden = !rows;
  };
  const timer = setInterval(async () => {
    try{ paint((await api(`/api/fl/register/${SESSION.session_id}/status`)).progress); }
    catch(e){ /* 폴링 실패는 조용히 넘긴다 — 본 작업은 따로 돌고 있다 */ }
  }, 400);
  try{
    b.textContent = "정합 중…";
    const r = await api(`/api/fl/register/${SESSION.session_id}`, {method: "POST"});
    REVIEW = r.review; STAGED = r.photos;
    paint(r.review.progress || {});
  }catch(e){
    preMsg(`정합에 실패했습니다: ${e.message}`, "err");
  }finally{
    clearInterval(timer);
    b.disabled = false; b.textContent = label;
    if(box) setTimeout(() => { box.hidden = true; }, 1200);
  }
  // 기준영상은 **정합을 돌 때** 구워진다(`_ref_bake`). 본편은 세션을 열 때
  // 덱에서 복원해 두므로 분류 시점에 받아 둔 목록으로 충분하지만, 여기서는 그때
  // 아직 비어 있다 — 다시 받지 않으면 겹쳐보기와 대보기가 "기준이 없다" 고 한다.
  await loadRefList();
  showView("proc");
  drawBoard();
}

/* ── Fin: 저장 검토 ─────────────────────────────────────────────────────────
   이름이 겹치면 **조용히 덮어쓰지 않는다.** 차수의 진실은 PPT 인데 이 모드는
   PPT 를 쓰지 않으므로, 사람이 덱에 차수를 넣기 전에 한 번 더 돌리면 차수 글자가
   같아진다. 그때 파일마다 무엇을 할지 사람이 고른다. */
let FAST_OVERWRITE = new Set();

async function fastLoadPlan(){
  const body = el("fin-body"), err = el("fin-err"), btn = el("btn-commit");
  // 환자를 고르지 않고 진행하면 '환자 폴더' 라는 말이 성립하지 않는다.
  const pf = el("btn-open-patient");
  if(pf) pf.textContent = picked ? "환자 폴더 열기" : "저장 폴더 열기";
  err.textContent = ""; btn.disabled = true; syncFinButtons(false);
  if(!SESSION){ body.innerHTML = `<div class="ph">세션이 없습니다</div>`; return; }
  body.innerHTML = `<div class="ph">불러오는 중…</div>`;
  try{
    const q = [...FAST_OVERWRITE].join("|");
    const p = await api(`/api/fl/plan/${SESSION.session_id}`
                        + (q ? `?overwrite=${encodeURIComponent(q)}` : ""));
    const items = p.files.map(f => {
      const nm = f.slot === "FACE" ? "얼굴" : slotNm(f.slot);
      const seg = f.exists
        ? `<span class="finseg seg">` +
          `<button type="button" data-base="${esc(f.base)}" data-act="number"` +
            `${f.action === "number" ? ` class="on"` : ""}>자동 번호</button>` +
          `<button type="button" data-base="${esc(f.base)}" data-act="overwrite"` +
            `${f.action === "overwrite" ? ` class="on"` : ""}>덮어쓰기</button></span>`
        : "";
      return `<li${f.extra ? ` class="sub"` : ""}>` +
        `<span class="k">${f.extra ? "추가" : nm}</span><code>${esc(f.file)}</code>` +
        `<span class="aux${f.exists ? " warn" : ""}">` +
          `${f.exists ? "이미 있음" : esc(f.label || "")}</span>${seg}</li>`;
    });
    body.innerHTML =
        `<div class="finsec"><span class="eyebrow">저장 위치</span>`
      + `<code>${esc(p.patient_dir)}</code></div>`
      + `<div class="finsec"><span class="eyebrow">프레젠테이션</span>`
      + `<span class="aux warn">만들지 않습니다 — 차수 ${esc(p.visit)} 는 `
      + `PowerPoint 에서 직접 추가하세요</span></div>`
      + `<ul class="finlist">${items.join("")}</ul>`;
    for(const b of body.querySelectorAll(".finseg button"))
      b.onclick = () => {
        if(b.dataset.act === "overwrite") FAST_OVERWRITE.add(b.dataset.base);
        else FAST_OVERWRITE.delete(b.dataset.base);
        fastLoadPlan();
      };
    if(p.missing.length)
      err.textContent = `빈 슬롯 ${p.missing.length}곳 — ${p.missing.map(slotNm).join(", ")}. `
                      + `채우고 오거나, 이대로 확정할 수 있습니다.`;
    btn.disabled = false;
    syncFinButtons(true);
    const first = p.files[0];
    FINDIRS = {folder: (picked && picked.folder) || "",
               photos: first ? dirPart(first.file) : "", ppt: "",
               exists: !!p.patient_dir_exists};
    FL_DIR = {dir: p.patient_dir, exists: !!p.patient_dir_exists};
    syncFinDirButtons();
  }catch(e){
    body.innerHTML = `<div class="ph">불러오지 못했습니다</div>`;
    err.textContent = e.message;
    FINDIRS = null; syncFinDirButtons();
  }
}

/* 환자 없이 저장할 때의 '열기'. 열 자리가 **하나뿐**이고 저장 루트 밖일 수도
   있어서, 경로를 들려 보내는 대신 서버가 아는 자리(세션의 저장 위치 · 방금 저장한
   자리)를 열게 한다. */
let FL_DIR = null;                      // {dir, exists}

function fastSyncDirButtons(btns){
  const [open, photos, pptdir] = btns;
  const ready = !!(FL_DIR && FL_DIR.exists);
  open.textContent = "저장 폴더 열기";
  open.disabled = !ready;
  open.title = ready ? `탐색기에서 엽니다 — ${FL_DIR.dir}`
                     : "확정 저장을 하면 폴더가 만들어집니다 — 그 뒤에 열 수 있습니다";
  // 사진이 그 폴더에 바로 들어간다 — 같은 자리를 두 번 열 이유가 없다.
  photos.hidden = true;
  if(pptdir) pptdir.hidden = true;      // 덱을 만들지 않는다 (CSS 로도 감춰 둔다)
}

async function fastOpenDir(btn){
  const err = el("fin-err"); err.textContent = ""; btn.disabled = true;
  try{
    await api("/api/fl/open", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({session_id: SESSION ? SESSION.session_id : ""})});
  }catch(e){
    err.textContent = e.message || "폴더를 열지 못했습니다";
  }finally{ btn.disabled = false; }
}

async function fastCommit(){
  const err = el("fin-err"), btn = el("btn-commit");
  err.textContent = ""; btn.disabled = true; btn.textContent = "저장 중…";
  const payload = {method: "POST", headers: {"Content-Type": "application/json"},
                   body: JSON.stringify({overwrite: [...FAST_OVERWRITE]})};
  try{
    let r;
    try{
      r = await api(`/api/fl/commit/${SESSION.session_id}`, payload);
    }catch(e){
      // 빈 슬롯은 막지 않는다 — 사진이 없는 날도 있다. 대신 반드시 되묻는다.
      if(e.status === 409 && e.data?.error === "missing_slots"){
        const nm = (e.data.missing || []).map(slotNm).join(", ");
        if(!confirm(`빈 슬롯이 있습니다 — ${nm}\n\n이대로 저장할까요?`)){
          btn.textContent = "확정 저장"; btn.disabled = false; return;
        }
        r = await api(`/api/fl/commit/${SESSION.session_id}?allow_missing=true`, payload);
      }else throw e;
    }
    el("fin-body").innerHTML =
        `<div class="finsec"><span class="eyebrow">저장 완료</span>`
      + `<code>${esc(r.patient_dir)}</code></div>`
      + `<ul class="finlist">${(r.files || []).map(f => `<li><code>${esc(f)}</code></li>`).join("")}</ul>`
      + (r.visit
          ? `<p class="tip">사진만 저장했습니다. 차수 <b>${esc(r.visit)}</b> 를 기록으로 `
            + `남기려면 PowerPoint 에서 슬라이드를 추가하세요.</p>`
          : `<p class="tip">사진만 저장했습니다.</p>`);
    el("fin-visit").hidden = false;
    el("fin-visit").dataset.tone = "done";
    el("fin-visit").textContent = r.visit ? `차수 ${r.visit} 사진 저장됨` : "사진 저장됨";
    setStep("fin", "done", "완료");
    const saved = picked && picked.folder;
    const dirs = FINDIRS ? {...FINDIRS, folder: saved || FINDIRS.folder, exists: true} : null;
    // 여기서 폴더가 **비로소 만들어졌다** — 열기 버튼을 켜도 되는 시점이 지금이다.
    FL_DIR = {dir: r.patient_dir, exists: true};
    FAST_OVERWRITE = new Set();
    const keep = FL_DIR;           // resetSession 뒤에도 방금 저장한 자리는 살린다
    resetSession();
    setFast(true);                 // resetSession 이 세션만 지운다 — 모드는 남는다
    FL_DIR = keep;
    FINDIRS = dirs; syncFinDirButtons();
    btn.textContent = "확정 저장";
    await loadPatients();
    if(saved) await openPatient(saved);
    else drawDetail();     // 환자 없이 저장했다 — 폴더 화면을 새 것으로 되돌린다
  }catch(e){
    err.textContent = e.message || "저장 실패";
    btn.textContent = "확정 저장"; btn.disabled = false;
  }
}

/* ── 버튼 ────────────────────────────────────────────────────────────────── */
{
  const b = el("btn-fast");
  if(b) b.onclick = toggleFast;
  setFast(false);
}

/* ══ 설정 ═════════════════════════════════════════════════════════════════════
   이 모드에만 쓰이는 두 가지 — 상하반전 기본값과 파일 이름 규칙.
   본편 설정과 같은 `settings.json` 에 들어가고, 창을 열 때마다 서버에서 받는다. */
let FL_PREFS = null;

const flSetPref = body =>
  api("/api/fl/prefs", {method: "POST", headers: {"Content-Type": "application/json"},
                        body: JSON.stringify(body)});

async function flSave(body){
  const err = el("fl-preferr"); if(err) err.textContent = "";
  try{
    const r = await flSetPref(body);
    fastDrawPrefs(r);
    // 규칙이 바뀌었으니 '이렇게 저장됩니다' 예시도 새 값을 따른다
    FL_NAMING = {...r.naming, ext: "jpg"};
    fastEgName();
  }catch(e){ if(err) err.textContent = e.message; }
}

/* 구분자를 말로 옮긴다. 공백은 입력칸에서 보이지 않아서, 무엇이 들어 있는지
   글로 적지 않으면 "설정이 안 먹는다" 로 읽힌다 — 실제로 그렇게 보였다. */
function sepName(sep){
  if(sep === " ") return "공백 1칸";
  if(sep === "_") return "밑줄";
  if(sep === "-") return "붙임표";
  if(!sep) return "";
  return /^\s+$/.test(sep) ? `공백 ${sep.length}칸` : `"${sep}"`;
}

function fastDrawPrefs(r){
  FL_PREFS = r;
  const grid = el("fl-flipgrid"); if(!grid) return;

  // ── 상하반전 기본값: 카테고리 × (기준·오늘) ──
  const fd = r.flip_defaults || {};
  grid.innerHTML = `<table class="flipgrid"><tbody>` +
    `<tr><th>카테고리</th><th>기준 사진</th><th>오늘 사진</th></tr>` +
    r.classes.map(c => `<tr><td>${esc(c)}</td>` +
      ["ref", "cur"].map(pool =>
        `<td><input type="checkbox" data-pool="${pool}" data-c="${esc(c)}"` +
        `${(fd[pool] || {})[c] ? " checked" : ""}></td>`).join("") +
      `</tr>`).join("") +
    `</tbody></table>`;
  for(const b of grid.querySelectorAll("input"))
    b.onchange = () => {
      const out = {ref: {}, cur: {}};
      for(const x of grid.querySelectorAll("input"))
        out[x.dataset.pool][x.dataset.c] = x.checked;
      flSave({flip_defaults: out});
    };

  // ── 파일 이름 규칙 ──
  const n = r.naming || {};
  for(const b of el("fl-nummode").children)
    b.setAttribute("aria-pressed", String(b.dataset.m === n.number_mode));
  for(const b of el("fl-numstart").children)
    b.setAttribute("aria-pressed", String(+b.dataset.s === n.start));
  el("fl-sep").value = n.separator || " ";
  el("fl-sepname").textContent = sepName(n.separator);

  // 번호 붙이기·시작 번호·구분자는 **글자 별칭에만** 쓰인다. 기본값처럼 이름이
  // `(1)` 꼴이면 본편의 번호 규칙이 대신 서므로 이 셋은 아무 일도 하지 않는다.
  // 눌러도 예시가 안 바뀌면 사람은 고장으로 읽는다 — 잠그고 이유를 적는다.
  const al = n.aliases || {};
  // 이름이 `(1)` 꼴이면 **번호가 곧 이름**이라 따로 붙일 번호가 없다. 그때는
  // 본편의 규칙이 대신 서므로 두 손잡이를 감추고 지금 규칙을 글로 적는다 —
  // 회색으로 남겨 두면 "왜 안 되지?" 만 남는다. 구분자는 접두어와 이름을 잇는
  // 자리라 어느 쪽이든 늘 쓰인다.
  const usesText = (r.classes || []).some(c => !/^\(\d+\)$/.test(al[c] || c));
  el("fl-numrows").hidden = !usesText;
  el("fl-numnote").textContent = usesText ? ""
    : "카테고리 이름이 번호라 번호 규칙은 본편과 같습니다 — 얼굴은 (6) (7) 로 "
      + "이어 오르고, 같은 자리를 여러 장 찍으면 (1)-2 가 됩니다. "
      + "이름을 글자로 바꾸면(예: (1) → 정면) 번호 설정이 나타납니다.";
  el("fl-aliasgrid").innerHTML = r.classes.map(c =>
    `<label>${esc(c)}<input data-c="${esc(c)}" value="${esc(al[c] || c)}"></label>`).join("");
  for(const inp of el("fl-aliasgrid").querySelectorAll("input"))
    inp.onchange = () => flSave({naming: {aliases: {[inp.dataset.c]: inp.value.trim()}}});

  // 규칙이 실제로 어떤 이름을 만드는지 그 자리에서 보여준다 (서버가 만든 값)
  el("fl-prefex").innerHTML = egBlock(r.example);
}

async function fastSyncPrefs(){
  if(!el("fl-flipgrid")) return;
  const r = await api("/api/fl/prefs").catch(() => null);
  if(!r) return;
  fastDrawPrefs(r);
  FL_NAMING = {...r.naming, ext: "jpg"};
}

for(const [id, key, cast] of [["fl-nummode", "number_mode", String],
                              ["fl-numstart", "start", Number]]){
  const host = el(id);
  if(host) for(const b of host.children)
    b.onclick = () => flSave({naming: {[key]: cast(b.dataset.m ?? b.dataset.s)}});
}
{
  const sep = el("fl-sep");
  if(sep) sep.onchange = () => flSave({naming: {separator: sep.value}});
}

/* ══ 얼굴 검수 ════════════════════════════════════════════════════════════════
   본편은 얼굴을 **케이스 덱의 슬라이드 자리**에 놓고 그 자리마다 구도를 잡는다.
   이 모드는 덱을 만들지 않으므로 놓을 자리가 없다 — 대신 **사진 한 장이 곧 하나의
   대상**이고, 저장되는 그림도 그 한 장이다. 그래서 사진마다 선택기에 칸을 하나씩
   붙이고, 구내 슬롯과 **같은 편집기**로 조정한다(창만 3:4 세로로 바뀐다). */
function faceList(){
  return (REVIEW && REVIEW.face) || [];
}

/* 편집기 제목과 선택기 칸에 쓸 이름. 저장될 번호와 맞춘다 — 사람이 화면에서 본
   "얼굴 2" 가 파일에서도 두 번째 얼굴이라야 헷갈리지 않는다. */
function faceLabel(key){
  const pid = key.slice(FACE_KEY.length);
  const i = faceList().findIndex(p => p.id === pid);
  return i < 0 ? "얼굴" : `얼굴 ${i + 1}`;
}

function fastFaceSeg(){
  for(const [i, p] of faceList().entries()){
    const g = document.createElement("button");
    g.textContent = `얼${i + 1}`;
    g.title = `얼굴 ${i + 1} — ${p.label || ""}`;
    g.dataset.key = FACE_KEY + p.id;
    g.onclick = () => pick(g.dataset.key);
    segEl.appendChild(g);
  }
}
