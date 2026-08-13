"use strict";
let SESSION = null;
const STEPS = [
  {v:"setup", n:1, nm:"환자 · 사진", code:"Setup & Upload",      st:"",     state:""},
  {v:"pre",   n:2, nm:"자동 분류",   code:"Pre-processing (AI)", st:"대기", state:""},
  {v:"proc",  n:3, nm:"검수·조정",   code:"Process (User)",      st:"대기", state:""},
  {v:"fin",   n:4, nm:"저장",       code:"Finalize & Save",     st:"대기", state:""},
];

/* 사이드바 — 한글이 먼저 읽히고, 라틴 용어가 아래에 남는다 */
const navEl = document.getElementById("nav");
for(const st of STEPS){
  const b = document.createElement("button");
  b.className = "nav"; b.dataset.view = st.v; b.dataset.state = st.state;
  b.innerHTML = `<span class="n">${st.n}</span>` +
    `<span class="top"><span class="nm">${st.nm}</span><span class="st">${st.st}</span></span>` +
    `<span class="code">${st.code}</span>`;
  b.onclick = () => showView(st.v);
  navEl.appendChild(b);
}

/* ══ Process (User): 십자 보드 + 편집기 ═══════════════════════════════════════
   보드에는 각 상자의 대표(0번)만 올라간다 — 슬라이드에 들어갈 그 사진이다. */
/* key = 템플릿의 '자리' 이름, nm = 그 자리에 들어가는 구내 사진, hk = 파일명 순번.
   치과 관례대로 환자의 우측방이 보는 사람 왼쪽 자리(SLOT_LEFT)에 온다. */
const SLOTS = [
  {key:"SLOT_FRONT", nm:"정면", area:"f", hk:1},
  {key:"SLOT_LEFT",  nm:"우측", area:"l", hk:2},
  {key:"SLOT_RIGHT", nm:"좌측", area:"r", hk:3},
  {key:"SLOT_UPPER", nm:"상악", area:"u", hk:4},
  {key:"SLOT_LOWER", nm:"하악", area:"b", hk:5},
];
const primaryOf = key => (REVIEW && REVIEW.bins && REVIEW.bins[key] || [])[0] || null;

const imgCache = new Map();
function getImg(url){
  if(imgCache.has(url)) return imgCache.get(url);
  const pr = new Promise(res => { const im = new Image();
    im.onload = () => res(im); im.onerror = () => res(null); im.src = url; });
  imgCache.set(url, pr); return pr;
}

/* 창을 사진으로 빈틈없이 덮는다 — PPT의 cover-fit과 같은 규약 */
function coverDraw(c, img, W, H){
  const k = Math.max(W / img.width, H / img.height);
  c.drawImage(img, -img.width * k / 2, -img.height * k / 2, img.width * k, img.height * k);
}
/* flipV: 교합면(SLOT_UPPER/LOWER)은 거울로 찍어 사용자가 뒤집어 본다. 원본
   파일은 그대로 두고 여기서만 뒤집는다 — PPT도 같은 방식(a:xfrm/@flipV)이다.
   반전을 사진 쪽(scale/rotate 안쪽)에 걸어야 st의 dx·dy·angle이 화면에서 본
   그대로의 뜻을 유지한다(드래그 아래 = dy 증가, Q/E = 화면 기준 회전). */
function drawComposite(c, W, H, img, st, border, flipV){
  c.clearRect(0, 0, W, H); c.fillStyle = "#000"; c.fillRect(0, 0, W, H);
  if(img){
    c.save(); c.translate(W / 2 + st.dx, H / 2 + st.dy);
    c.rotate(st.angle * Math.PI / 180); c.scale(st.scale, st.scale);
    if(flipV) c.scale(1, -1);
    coverDraw(c, img, W, H); c.restore();
  }
  if(border){
    c.strokeStyle = "rgba(61,144,240,.65)"; c.lineWidth = 2;
    c.strokeRect(1, 1, W - 2, H - 2);
  }
}

/* 슬롯 창은 세션이 정한다 — 재진이면 그 환자 PPT가 실제로 쓰던 레이아웃이고,
   없을 때만 템플릿 기본값으로 물러난다. 화면·미리보기·PPT가 같은 창을 써야
   에디터에서 맞춘 그림이 슬라이드에서도 그대로 나온다. */
function slotWindow(key){
  return (SESSION && SESSION.windows && SESSION.windows[key])
      || (HEALTH && HEALTH.windows && HEALTH.windows[key]) || null;
}
/* 캔버스 크기는 창(cm) × 렌더 배율로 정한다.
   폭을 고정해 버리면 창 크기가 다를 때 캔버스 1px이 렌더 1px과 어긋나고,
   드래그로 만든 dx/dy(렌더 픽셀 단위)를 백엔드가 다르게 해석한다.
   템플릿 창(8.4×6.3cm, 100px/cm)에서는 종전과 같은 840×630이 나온다. */
function canvasSize(key){
  const win = slotWindow(key);
  const ppc = (HEALTH && HEALTH.px_per_cm) || 100;
  if(!win) return {w: 840, h: 630};
  return {w: Math.max(1, Math.round(win.w * ppc)),
          h: Math.max(1, Math.round(win.h * ppc))};
}
function fitCanvas(cv, key){
  const {w, h} = canvasSize(key);
  if(cv.width !== w || cv.height !== h){ cv.width = w; cv.height = h; }
  return {w, h};
}

const boardEl = document.getElementById("board"), segEl = document.getElementById("seg");
const slotCanvas = {};
const ED = {slot:null, dx:0, dy:0, scale:1, angle:0, img:null, drag:false, lx:0, ly:0, timer:null};

function drawBoard(){
  boardEl.innerHTML = ""; segEl.innerHTML = "";
  for(const k of Object.keys(slotCanvas)) delete slotCanvas[k];
  for(const s of SLOTS){
    const p = primaryOf(s.key);
    const cell = document.createElement("button");
    cell.className = "cell" + (p ? "" : " void");
    cell.style.gridArea = s.area;
    if(p){
      const cv = document.createElement("canvas");
      fitCanvas(cv, s.key);
      cell.appendChild(cv); slotCanvas[s.key] = cv;
      cell.insertAdjacentHTML("beforeend",
        `<span class="hk">${s.hk}</span>` +
        `<span class="tag${p.confidence < 0.75 ? " attn" : ""}">${s.nm} ${Math.round(p.confidence * 100)}%</span>`);
      cell.onclick = () => pick(s.key);
      renderSlot(s.key);
    } else {
      cell.textContent = `${s.nm} 없음`;
    }
    boardEl.appendChild(cell);
  }
  for(const s of SLOTS){
    const g = document.createElement("button");
    g.textContent = s.hk; g.title = s.nm; g.dataset.key = s.key;
    g.disabled = !primaryOf(s.key);
    g.onclick = () => pick(s.key);
    segEl.appendChild(g);
  }
  if(ED.slot && primaryOf(ED.slot)) pick(ED.slot);
  else { const f = SLOTS.find(x => primaryOf(x.key)); if(f) pick(f.key); }
  drawNoteOverlay();   // 판을 다시 그리면 오버레이도 날아간다
}

async function renderSlot(key){
  const cv = slotCanvas[key], p = primaryOf(key);
  if(!cv || !p) return;
  const img = await getImg(p.thumb);
  const {w, h} = fitCanvas(cv, key);
  // 겹쳐보기가 켜져 있으면 십자뷰의 다섯 슬롯도 전부 아나글리프로 그린다 —
  // 편집 중인 슬롯만 겹쳐 보이면 나머지 넷은 결국 하나씩 열어 봐야 한다.
  if(OV.on){
    const ref = await boardRefImg(key);
    if(ref){
      drawAnaglyph(cv.getContext("2d"), w, h, ref, img, p.editor, p.flip_v);
      return;
    }
  }
  drawComposite(cv.getContext("2d"), w, h, img, p.editor, false, p.flip_v);
}

/* 슬롯의 기준영상 — 편집 중인 슬롯은 선택된 차수, 나머지는 직전 차수. */
async function boardRefImg(slot){
  const list = OV.list[slot] || [];
  if(!list.length || !SESSION) return null;
  const visit = (slot === OV.slot && OV.visit && list.includes(OV.visit))
              ? OV.visit : list[list.length - 1];
  const c = OV.board[slot];
  if(c && c.visit === visit) return c.img;
  let img = null;
  try{
    img = await getImg(
      `/api/reference/${SESSION.session_id}/${slot}?visit=${visit}`);
  }catch(e){ img = null; }
  OV.board[slot] = {visit, img};
  return img;
}

function redrawBoardSlots(){
  for(const k of Object.keys(slotCanvas)) renderSlot(k);
}

async function pick(key){
  const p = primaryOf(key); if(!p) return;
  const meta = SLOTS.find(x => x.key === key);
  ED.slot = key;
  ED.dx = p.editor.dx; ED.dy = p.editor.dy; ED.scale = p.editor.scale; ED.angle = p.editor.angle;
  ED.flip_v = !!p.flip_v;
  ED.img = await getImg(p.thumb);
  [...boardEl.children].forEach(c => c.setAttribute("aria-pressed", c.style.gridArea === meta.area));
  [...segEl.children].forEach(g => g.setAttribute("aria-pressed", g.dataset.key === key));
  showPhotoDock();   // 노트 서식을 보던 중이면 사진 편집기로 돌아온다
  el("dock-title").firstChild.textContent =
    `${meta.nm} · ${p.label || "—"} ${Math.round((p.confidence || 0) * 100)}%`;
  syncKnobs(); renderEditor();
  await syncOverlayBar(); renderEditor();   // 기준영상이 늦게 오면 한 번 더 그린다
}

el("ov-on").onchange = async e => {
  OV.on = e.target.checked;
  if(OV.on && !OV.img) await loadOverlayImg();
  renderEditor();
  redrawBoardSlots();          // 십자뷰 다섯 슬롯도 같이 켜고 끈다
};
el("ov-visit").onchange = async e => {
  OV.visit = e.target.value; OV.img = null;
  delete OV.board[ED.slot];    // 이 슬롯의 판 캐시도 새 차수로
  if(OV.on) await loadOverlayImg();
  renderEditor();
  if(OV.on) renderSlot(ED.slot);
};

/* 어느 슬롯에 어느 차수를 겹쳐볼 수 있나. 세션마다 한 번만 물어본다. */
async function loadRefList(){
  OV.list = {}; OV.img = null; OV.slot = null; OV.board = {};
  if(!SESSION) return;
  OV.list = await api(`/api/references/${SESSION.session_id}`).catch(() => ({})) || {};
}

/* ── 기준 겹쳐보기 (아나글리프) ───────────────────────────────────────────────
   기준영상과 지금 구도를 **채널을 갈라** 합친다: 기준은 빨강, 현재는 청록. 맞는
   곳은 회색으로 사라지고 **어긋난 곳만 색이 남는다.** 반투명으로 포개면 둘 다
   흐려질 뿐 어디가 틀렸는지는 안 보인다.

   밝기를 먼저 편다(히스토그램 평활화). 차수마다 노출이 달라서 그냥 겹치면 밝기
   차이가 통째로 색으로 남고, 정작 봐야 할 어긋남이 그 아래 묻힌다.

   합성은 화면에서 한다 — 서버로 보내면 드래그를 못 따라가고, 따라가지 못하는
   겹쳐보기는 맞추는 도구가 아니라 그림일 뿐이다. */
const OV = {on: false, visit: "", img: null, slot: null, list: {}, board: {}};

function equalize(d){                     // 8비트 그레이 히스토그램 평활화
  const n = d.length / 4, hist = new Uint32Array(256);
  const g = new Uint8ClampedArray(n);
  for(let i = 0; i < n; i++){
    // ImageData 는 RGBA 다 — cv2 의 BGR 가중치를 그대로 쓰면 R 과 B 가 뒤바뀐다
    const v = (d[i*4] * 0.299 + d[i*4+1] * 0.587 + d[i*4+2] * 0.114) | 0;
    g[i] = v; hist[v]++;
  }
  const lut = new Uint8ClampedArray(256);
  let acc = 0;
  for(let v = 0; v < 256; v++){ acc += hist[v]; lut[v] = acc * 255 / n; }
  for(let i = 0; i < n; i++) g[i] = lut[g[i]];
  return g;
}

function drawAnaglyph(ctx, W, H, refImg, curImg, st, flipV){
  const a = document.createElement("canvas"), b = document.createElement("canvas");
  a.width = b.width = W; a.height = b.height = H;
  // 기준영상은 **이전 차수 슬라이드에 보이던 그림**이라 창에 그대로 깔면 된다
  drawComposite(a.getContext("2d"), W, H, refImg, {dx:0, dy:0, scale:1, angle:0}, false, false);
  drawComposite(b.getContext("2d"), W, H, curImg, st, false, flipV);
  const A = a.getContext("2d").getImageData(0, 0, W, H);
  const B = b.getContext("2d").getImageData(0, 0, W, H);
  const ga = equalize(A.data), gb = equalize(B.data);
  const out = ctx.createImageData(W, H);
  for(let i = 0; i < ga.length; i++){
    out.data[i*4]   = ga[i];              // R = 기준
    out.data[i*4+1] = gb[i];              // G,B = 현재 (청록)
    out.data[i*4+2] = gb[i];
    out.data[i*4+3] = 255;
  }
  ctx.putImageData(out, 0, 0);
  ctx.strokeStyle = "rgba(61,144,240,.65)"; ctx.lineWidth = 2;
  ctx.strokeRect(1, 1, W - 2, H - 2);
}

async function syncOverlayBar(){
  const bar = el("ov-bar"); if(!bar) return;
  const list = OV.list[ED.slot] || [];
  bar.hidden = !(ED.slot && list.length);          // 초진에는 기준이 없다
  if(bar.hidden){ OV.img = null; return; }
  const sel = el("ov-visit");
  // **직전 차수가 기본이다.** 목록은 차수 오름차순이라 마지막이 직전이다.
  // 사람이 같은 슬롯에서 고른 차수만 그보다 우선한다.
  const want = (list.includes(OV.visit) && OV.slot === ED.slot) ? OV.visit
             : list[list.length - 1];
  sel.innerHTML = list.map(v =>
    `<option value="${v}"${v === want ? " selected" : ""}>${v} 차수</option>`).join("");
  if(OV.slot !== ED.slot || OV.visit !== want || !OV.img){
    OV.slot = ED.slot; OV.visit = want; OV.img = null;
    if(OV.on) await loadOverlayImg();
  }
}

async function loadOverlayImg(){
  if(!SESSION || !OV.slot || !OV.visit) return;
  try{
    OV.img = await getImg(
      `/api/reference/${SESSION.session_id}/${OV.slot}?visit=${OV.visit}`);
  }catch(e){ OV.img = null; }
}

function renderEditor(){
  const cv = el("ed-canvas"); if(!cv || !ED.slot) return;
  const {w, h} = fitCanvas(cv, ED.slot);
  const ctx = cv.getContext("2d");
  if(OV.on && OV.img && OV.slot === ED.slot)
    drawAnaglyph(ctx, w, h, OV.img, ED.img, ED, ED.flip_v);
  else
    drawComposite(ctx, w, h, ED.img, ED, true, ED.flip_v);
  updateReadout();
}

/* 확정 시 PPT에 들어갈 값 — 백엔드 editor_to_placement와 같은 산식 */
function updateReadout(){
  const win = slotWindow(ED.slot); if(!win || !HEALTH) return;
  const ppc = HEALTH.px_per_cm;
  const a = ED.img ? ED.img.width / ED.img.height : 4 / 3;
  const winA = win.w / win.h;
  const bw = a >= winA ? win.h * a : win.w;
  const bh = a >= winA ? win.h : win.w / a;
  const extW = bw * ED.scale, extH = bh * ED.scale;
  const cx = win.x + win.w / 2 + ED.dx / ppc, cy = win.y + win.h / 2 + ED.dy / ppc;
  el("readout").innerHTML =
    `PPT 기록값<br>rot <b>${Math.round(ED.angle * 60000)}</b> · ` +
    `크기 <b>${extW.toFixed(2)}×${extH.toFixed(2)}</b>cm<br>` +
    `오프셋 <b>(${(cx - extW / 2).toFixed(2)}, ${(cy - extH / 2).toFixed(2)})</b>cm`;
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
function syncKnobs(){
  el("ed-angle").value = ED.angle.toFixed(1);
  el("ed-scale").value = Math.round(clamp(ED.scale, .5, 2) * 100);
  el("ed-tx").value = Math.round(clamp(ED.dx, -200, 200));
  el("ed-ty").value = Math.round(clamp(ED.dy, -200, 200));
  el("v-angle").textContent = ED.angle.toFixed(1) + "°";
  el("v-scale").textContent = Math.round(ED.scale * 100) + "%";
  el("v-tx").textContent = Math.round(ED.dx);
  el("v-ty").textContent = Math.round(ED.dy);
}

function afterEdit(){
  const p = primaryOf(ED.slot);
  if(p) p.editor = {dx: ED.dx, dy: ED.dy, scale: ED.scale, angle: ED.angle};
  syncKnobs(); renderEditor(); renderSlot(ED.slot); saveEdit();
}

function saveEdit(){
  clearTimeout(ED.timer);
  el("ed-saved").textContent = "…";
  ED.timer = setTimeout(async () => {
    try{
      const r = await api("/api/adjust", {method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({session_id: SESSION.session_id, slot: ED.slot,
                              dx: ED.dx, dy: ED.dy, scale: ED.scale, angle: ED.angle})});
      // 서버가 cover 조건으로 배율을 되돌릴 수 있다 — 창에 빈틈이 생기지 않게
      if(r.clamped_scale && Math.abs(r.clamped_scale - ED.scale) > 1e-6){
        ED.scale = r.clamped_scale;
        const p = primaryOf(ED.slot); if(p) p.editor.scale = ED.scale;
        syncKnobs(); renderEditor(); renderSlot(ED.slot);
      }
      el("ed-saved").textContent = "저장됨";
    }catch(e){ el("ed-saved").textContent = "저장 실패"; }
  }, 200);
}

function bindEditor(){
  const cv = el("ed-canvas");
  el("ed-angle").oninput = () => { ED.angle = +el("ed-angle").value; afterEdit(); };
  el("ed-scale").oninput = () => { ED.scale = +el("ed-scale").value / 100; afterEdit(); };
  el("ed-tx").oninput = () => { ED.dx = +el("ed-tx").value; afterEdit(); };
  el("ed-ty").oninput = () => { ED.dy = +el("ed-ty").value; afterEdit(); };
  el("ed-reset").onclick = () => { ED.dx = ED.dy = 0; ED.scale = 1; ED.angle = 0; afterEdit(); };
  const back = el("nt-back");
  if(back) back.onclick = () => { showPhotoDock(); if(ED.slot) pick(ED.slot); };
  cv.addEventListener("pointerdown", e => {
    if(!ED.slot) return;
    ED.drag = true; ED.lx = e.offsetX; ED.ly = e.offsetY; cv.setPointerCapture(e.pointerId);
  });
  cv.addEventListener("pointermove", e => {
    if(!ED.drag) return;
    const f = cv.width / cv.clientWidth;
    ED.dx += (e.offsetX - ED.lx) * f; ED.dy += (e.offsetY - ED.ly) * f;
    ED.lx = e.offsetX; ED.ly = e.offsetY;
    syncKnobs(); renderEditor(); renderSlot(ED.slot);
  });
  cv.addEventListener("pointerup", () => { if(ED.drag){ ED.drag = false; afterEdit(); } });
  cv.addEventListener("wheel", e => {
    if(!ED.slot) return;
    e.preventDefault();
    ED.scale = clamp(ED.scale * (e.deltaY < 0 ? 1.03 : .97), .5, 2);
    afterEdit();
  }, {passive:false});
}

/* 키보드: 1~5 슬롯, 방향키 이동, Q/E 회전, A/D 배율 (e.code라 한/영 무관) */
addEventListener("keydown", e => {
  if(VIEW !== "proc") return;
  // 글을 치는 중에는 사진이 움직이면 안 된다. TEXTAREA(노트 오버레이)가 빠져
  // 있어서 Q/E·A/D·방향키가 사진 회전·배율·이동으로 새어 들어갔다.
  const t = e.target;
  if(t && (t.isContentEditable || t.tagName === "TEXTAREA" ||
           (t.tagName === "INPUT" && t.type !== "range"))) return;
  const code = e.code;
  // FACE 탭도 같은 조작을 쓴다 — 대상만 자리(FED)로 바뀐다
  const face = TAB === "face";
  if(!face && (code.startsWith("Digit") || code.startsWith("Numpad"))){
    const s = SLOTS.find(x => x.hk === +code.slice(-1));
    if(s && primaryOf(s.key)){ pick(s.key); e.preventDefault(); }
    return;
  }
  const E = face ? FED : ED;
  if(face ? !(FED.cell && faceSlots()[FED.cell]) : !ED.slot) return;
  const mv = e.shiftKey ? 15 : 4, rot = e.shiftKey ? 1 : .2, sc = e.shiftKey ? .04 : .02;
  let hit = true;
  switch(code){
    case "ArrowLeft":  E.dx = clamp(E.dx - mv, -200, 200); break;
    case "ArrowRight": E.dx = clamp(E.dx + mv, -200, 200); break;
    case "ArrowUp":    E.dy = clamp(E.dy - mv, -200, 200); break;
    case "ArrowDown":  E.dy = clamp(E.dy + mv, -200, 200); break;
    case "KeyQ": E.angle = clamp(E.angle - rot, -10, 10); break;
    case "KeyE": E.angle = clamp(E.angle + rot, -10, 10); break;
    case "KeyA": E.scale = clamp(E.scale - sc, .5, 2); break;
    case "KeyD": E.scale = clamp(E.scale + sc, .5, 2); break;
    default: hit = false;
  }
  if(hit){ e.preventDefault(); face ? afterFaceEdit() : afterEdit(); }
});

/* ══ Pre-processing (AI) ═/* ══ Pre-processing (AI) ═════════════════════════════════════════════════════
   상자 = 순서 있는 목록. 맨 위가 슬라이드에 들어갈 대표, 아래는 같은 자리의
   추가 촬영본. 사진을 끌어 상자를 옮기고, ▲로 대표를 바꾼다. */
const BINS = [
  {key:"FACE",       label:"FACE"},
  {key:"SLOT_FRONT", label:"IO_FRONT"},
  {key:"SLOT_LEFT",  label:"IO_RIGHT"},
  {key:"SLOT_RIGHT", label:"IO_LEFT"},
  {key:"SLOT_UPPER", label:"IO_UPPER"},
  {key:"SLOT_LOWER", label:"IO_LOWER"},
];

async function runClassify(){
  showView("pre");
  preMsg("분류 중…", "busy");
  loadRefList();          // 겹쳐보기 목록 (재진에서만 내용이 있다)
  try{
    const r = await api(`/api/classify/${SESSION.session_id}`, {method:"POST"});
    REVIEW = r.review; STAGED = r.photos;
    preMsg("");
    drawBins();
    renderVisitBadges();   // 예상 기준 → 실제로 정합에 쓰인 기준으로 갱신
    drawFace();
  }catch(e){ preMsg(e.message, "err"); }
}
function preMsg(text, kind){
  const n = el("pre-msg"); if(!n) return;
  n.textContent = text || ""; n.dataset.kind = kind || "";
}

const othersOf = () => STAGED.filter(p => !p.slot);

function photoCard(p, isPrimary, binKey){   // binKey는 PPT 배지 표시에만 쓰인다
  const low = p.confidence < 0.75;
  return `<figure class="ph-card${isPrimary ? " primary" : ""}" draggable="true" data-pid="${p.id}">` +
    `<img src="${p.thumb}" alt="" draggable="false"${p.flip_v ? ` class="fv"` : ""}>` +
    (isPrimary && binKey !== "FACE" ? `<span class="tagp">PPT</span>` : "") +
    `<figcaption${low ? ` class="low"` : ""}>${p.label || "—"} ${Math.round((p.confidence || 0) * 100)}%</figcaption>` +
    `</figure>`;
}

function drawBins(){
  if(!REVIEW) return;
  const box = el("bins");
  box.innerHTML = BINS.map(b => {
    const list = (REVIEW.bins && REVIEW.bins[b.key]) || [];
    /* FACE는 장수가 많고 사람이 자리를 직접 고르는 상자다. 두 칸 폭에 3열로
       깔아서 여러 장을 한눈에 훑을 수 있게 한다(다른 상자는 종전대로 1열). */
    const face = b.key === "FACE";
    return `<div class="bin${face ? " face" : ""}" data-slot="${b.key}">` +
      `<div class="bin-h">${b.label}<span class="hr">` +
        (face ? `<button type="button" class="minibtn" id="face-sort"` +
                ` title="EXIF 촬영 시각 순서로 세웁니다">촬영순</button>` : "") +
        `<span class="cnt">${list.length || ""}</span></span></div>` +
      `<div class="bin-body${face ? " grid3" : ""}">` +
        (list.length ? list.map((p, i) => photoCard(p, i === 0, b.key)).join("")
                     : `<p class="bin-empty">비어 있음</p>`) +
      `</div></div>`;
  }).join("");
  const sortBtn = el("face-sort");
  if(sortBtn) sortBtn.onclick = sortFace;

  const others = othersOf();
  el("bin-others").innerHTML =
    `<div class="bin-h">OTHERS<span class="cnt">${others.length || ""}</span></div>` +
    `<div class="bin-body row">` +
      (others.length ? others.map(p => photoCard(p, false, null)).join("")
                     : `<p class="bin-empty">비어 있음</p>`) +
    `</div>`;

  const missing = BINS.filter(b => b.key !== "FACE" && !((REVIEW.bins[b.key] || []).length));
  el("pre-n").textContent = `${STAGED.length}장` + (missing.length ? ` · 빈 슬롯 ${missing.length}` : "");
  el("btn-toproc").disabled = missing.length > 0;
  bindBinDnD();
}

/* 떨어뜨린 위치가 순서를 정한다 — 위쪽에 놓으면 대표가 된다.
   세로 상자는 Y, 가로 트레이(OTHERS)는 X 기준. */
function dropIndex(bin, e){
  const cards = [...bin.querySelectorAll(".ph-card")];
  const body = bin.querySelector(".bin-body");
  const row = !!body && body.classList.contains("row");
  const grid = !!body && body.classList.contains("grid3");
  for(let i = 0; i < cards.length; i++){
    const r = cards[i].getBoundingClientRect();
    if(grid){
      // 3열 격자는 읽기 순서(왼→오, 위→아래)로 센다. 윗줄이면 그 앞이고,
      // 같은 줄이면 카드의 좌우 중앙으로 앞뒤를 가른다.
      if(e.clientY < r.top) return i;
      if(e.clientY <= r.bottom && e.clientX < r.left + r.width / 2) return i;
      continue;
    }
    const mid = row ? r.left + r.width / 2 : r.top + r.height / 2;
    if((row ? e.clientX : e.clientY) < mid) return i;
  }
  return cards.length;
}

function bindBinDnD(){
  for(const f of document.querySelectorAll(".ph-card"))
    f.ondragstart = e => e.dataTransfer.setData("pid", f.dataset.pid);
  for(const bin of document.querySelectorAll(".bin")){
    bin.ondragover = e => { e.preventDefault(); bin.classList.add("over"); };
    bin.ondragleave = () => bin.classList.remove("over");
    bin.ondrop = e => {
      e.preventDefault(); bin.classList.remove("over");
      const pid = e.dataTransfer.getData("pid");
      if(pid) assign(pid, bin.dataset.slot || null, dropIndex(bin, e));
    };
  }
}

/* FACE 상자를 촬영 순서로 세운다. 서버가 EXIF(서브초까지)·일련번호·파일명
   순으로 물러나며 정렬하므로, 여기서는 결과만 받아 다시 그린다. */
async function sortFace(){
  const btn = el("face-sort");
  if(btn) btn.disabled = true;
  try{
    const r = await api("/api/sort", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({session_id: SESSION.session_id, slot: "FACE"})});
    REVIEW = r.review; STAGED = r.photos;
    drawBins();
    // 촬영시각이 없는 사진이 섞여 있으면 무엇을 근거로 세웠는지 알려준다 —
    // 조용히 파일명 순으로 세우면 사용자가 결과를 믿을 수 없다.
    preMsg(r.with_time === r.n ? `촬영순 정렬 · ${r.n}장`
      : `촬영순 정렬 · ${r.n}장 (EXIF 시각 ${r.with_time}장, 나머지는 파일명 순)`,
      r.with_time === r.n ? "ok" : "warn");
  }catch(e){ preMsg(e.message, "err"); }
  finally{ const b = el("face-sort"); if(b) b.disabled = false; }
}

async function assign(pid, slot, at){
  try{
    const r = await api("/api/assign", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({session_id: SESSION.session_id, photo_id: pid, slot, at})});
    REVIEW = r.review; STAGED = r.photos;
    drawBins();
  }catch(e){ preMsg(e.message, "err"); }
}

/* ══ Setup ═══════════════════════════════════════════════════════════════════
   초진/재진을 사용자가 고르지 않는다. 폴더를 열면 서버가 PPT 유무로 판정한다. */
const api = async (url, opt) => {
  const r = await fetch(url, opt);
  const d = r.status === 204 ? null : await r.json();
  if(!r.ok){
    const e = new Error(d?.detail || "요청 실패"); e.status = r.status; e.data = d;
    if(r.status === 410) onSessionExpired();
    throw e;
  }
  return d;
};

/* 서버는 확정되지 않은 세션을 48시간 뒤 임시 업로드째로 걷어간다(410 Gone).
   그 뒤로는 무슨 요청을 해도 실패하므로, 화면마다 제각각인 오류 메시지로
   흘려보내지 않고 Setup으로 되돌려 무슨 일이 있었는지 분명히 알린다. */
function onSessionExpired(){
  if(!SESSION) return;              // 이미 정리됨 — 알림을 두 번 띄우지 않는다
  resetSession();
  showView("setup");
  alert("장시간 사용하지 않아 세션이 종료되었습니다.\n\n" +
        "업로드했던 사진은 저장되지 않았습니다. 환자를 다시 선택해 시작해 주세요.");
}
/* 함수 선언으로 둔다 — 최상위 핸들러 등록이 이 줄보다 위에서도 el()을 쓴다.
   const 화살표면 TDZ 때문에 스크립트 전체가 멈춘다. */
function el(id){ return document.getElementById(id); }
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

let PATIENTS = [], SKIPPED = [], picked = null, RULES = {}, STAGED = [], VIEW = "setup",
    FOLDER = null, REVIEW = null, HEALTH = null, CASE = null;

/* ── 저장 위치 고르기 ──────────────────────────────────────────────────────
   브라우저는 서버의 절대 경로를 모르므로 탐색을 서버에 맡긴다(/api/fs). */
async function drawRootPicker(path, host){
  const d = host || el("detail");
  d.innerHTML = `<p class="empty">폴더를 읽는 중…</p>`;
  let r;
  try{ r = await api("/api/fs" + (path ? "?path=" + encodeURIComponent(path) : "")); }
  catch(e){ d.innerHTML = `<p class="err">${esc(e.message)}</p>` +
    `<p style="margin-top:12px"><button class="btn" id="pk-cancel">돌아가기</button></p>`;
    el("pk-cancel").onclick = () => closePicker(host); return; }

  d.innerHTML =
    `<div class="who">저장 위치 고르기</div>` +
    `<div class="no">환자 폴더가 들어 있는(또는 들어갈) 폴더를 고르세요</div>` +
    `<div class="drives" id="pk-drives"></div>` +
    `<div class="crumb"><button class="btn" id="pk-up"${r.parent ? "" : " disabled"}>▲ 상위</button>` +
      `<span class="ident">${esc(r.path)}</span></div>` +
    `<div class="dlist" id="pk-list"></div>` +
    `<p style="margin:14px 0 0; display:flex; gap:8px;">` +
      `<button class="btn primary" id="pk-ok">이 폴더로 지정</button>` +
      `<button class="btn" id="pk-new">＋ 새 폴더</button>` +
      `<button class="btn" id="pk-cancel">취소</button></p>` +
    `<p class="err" id="pk-err"></p>`;

  const drv = el("pk-drives");
  for(const x of r.drives){
    const b = document.createElement("button");
    b.className = "btn"; b.style.padding = "4px 10px"; b.style.fontSize = "12px";
    b.textContent = x; b.onclick = () => drawRootPicker(x, host);
    drv.appendChild(b);
  }

  const list = el("pk-list");
  if(!r.dirs.length) list.innerHTML = `<p class="empty">하위 폴더가 없습니다</p>`;
  for(const x of r.dirs){
    const b = document.createElement("button");
    b.className = "drow"; b.textContent = "📁 " + x.name;
    b.onclick = () => drawRootPicker(x.path, host);
    list.appendChild(b);
  }

  el("pk-new").onclick = async () => {
    const name = (prompt("새 폴더 이름") || "").trim();
    if(!name) return;
    try{
      const res = await api("/api/fs/mkdir", {method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({path: r.path, name})});
      drawRootPicker(res.path, host);   // 만든 폴더 안으로 들어가 바로 지정할 수 있게
    }catch(e){ el("pk-err").textContent = e.message; }
  };
  el("pk-up").onclick = () => r.parent && drawRootPicker(r.parent, host);
  el("pk-cancel").onclick = () => closePicker(host);
  el("pk-ok").onclick = async () => {
    try{
      const res = await api("/api/root", {method:"POST",
        headers:{"Content-Type":"application/json"}, body: JSON.stringify({path: r.path})});
      setRootLabel(res.root);
      resetSession();
      closePicker(host);
      await loadPatients();
      drawDetail();
    }catch(e){ el("pk-err").textContent = e.message; }
  };
}

function closePicker(host){
  if(host){ host.hidden = true; host.innerHTML = ""; } else { drawDetail(); }
}
function setRootLabel(path){
  for(const id of ["rootpath","set-rootpath"]){
    const n = el(id); if(n){ n.textContent = path; n.title = path; }
  }
}

/* ── 설정 창 ─────────────────────────────────────────────────────────────── */
function openSettings(){
  syncThemeSeg();
  closePicker(el("set-picker"));
  el("dlg-set").showModal();
  syncPrefs();
  loadMaint();
}

/* ── 유지관리 ─────────────────────────────────────────────────────────────
   되돌리기는 업데이트가 깨졌을 때 쓸 유일한 길이다. 삭제는 환자 자료를 기본으로
   남기고, 지우려면 확인 문구를 받는다 — 의료 기록이라 되돌릴 수 없다. */
const MB = n => (n/1048576).toFixed(0) + " MB";

async function loadMaint(){
  const u = await api("/api/update/check").catch(() => null);
  const info = el("maint-info");
  const ver = u && u.app_to && u.app_to !== u.app_from
    ? `v${u.app_from} → v${u.app_to}` : `${u && u.behind}개 변경`;
  info.textContent = u && u.ok
    ? (u.has_update ? `새 버전 있음 (${ver})` : "최신 버전입니다")
    : ((u && u.reason) || "업데이트 확인 불가");
  el("btn-rollback").hidden = !(u && u.local);
  el("uninst").hidden = true;
  el("uninst-data").checked = false;
  el("uninst-confirm").hidden = true;
}

el("btn-shortcut").onclick = async () => {
  const r = await api("/api/shortcut", {method:"POST"})
    .catch(e => ({ok: false, detail: e.message}));
  alert(r.ok ? `바로가기를 만들었습니다: ${r.desktop}\\CRoCs.lnk`
             : (r.detail || "만들지 못했습니다"));
};

el("btn-rollback").onclick = async () => {
  if(!confirm("직전 버전으로 되돌립니다. 계속할까요?")) return;
  const r = await api("/api/update/rollback", {method:"POST"}).catch(() => null);
  if(!r || !r.ok){ alert((r && r.detail) || "되돌리지 못했습니다"); return; }
  if(confirm(`${r.to} 로 되돌렸습니다. 지금 다시 시작할까요?`))
    await api("/api/update/restart", {method:"POST"}).catch(() => {});
};

el("btn-uninstall").onclick = async () => {
  const v = await api("/api/uninstall/inventory").catch(() => null);
  if(!v){ alert("정보를 읽지 못했습니다"); return; }
  el("uninst-inv").innerHTML =
    `지워짐 — 프로그램 <b>${v.program_dir}</b> (${MB(v.program_bytes)}, 모델 ${MB(v.weights_bytes)} 포함)<br>` +
    `남음 &nbsp;— 환자 자료 <b>${v.data_dir}</b> (${MB(v.data_bytes)}, 환자 ${v.patients}명)`;
  el("uninst").hidden = false;
};

el("uninst-data").onchange = e => { el("uninst-confirm").hidden = !e.target.checked; };
el("btn-uninstall-cancel").onclick = () => { el("uninst").hidden = true; };

el("btn-uninstall-go").onclick = async () => {
  const drop = el("uninst-data").checked;
  const body = {drop_data: drop, confirm: el("uninst-word").value.trim()};
  if(drop && body.confirm !== "삭제"){ alert("확인 문구를 정확히 입력하세요"); return; }
  if(!confirm(drop ? "환자 자료까지 모두 지웁니다. 되돌릴 수 없습니다. 계속할까요?"
                   : "프로그램을 지웁니다. 환자 자료는 남습니다. 계속할까요?")) return;
  const r = await api("/api/uninstall/prepare",
                      {method:"POST", headers:{"Content-Type":"application/json"},
                       body: JSON.stringify(body)}).catch(() => null);
  if(!r || !r.ok){ alert((r && r.detail) || "실패했습니다"); return; }
  alert(r.detail + "\n\n앱이 곧 종료됩니다.");
};
/* 개인화 — 결과물 표기·저장 구성이라 설치본 공용(settings.json)이다. */
const setPref = body =>
  api("/api/prefs", {method:"POST", headers:{"Content-Type":"application/json"},
                     body: JSON.stringify(body)}).catch(() => null);

async function syncPrefs(){
  const r = await api("/api/prefs").catch(() => null);
  const cur = (r && r.months_unit) || "int";
  for(const b of el("set-unit").children) b.setAttribute("aria-pressed", b.dataset.u === cur);
  el("set-raw").checked = !!(r && r.save_raw);
  el("set-subdirs").checked = !!(r && r.scan_subdirs);
  const ns = (r && r.note_sizes) || {};
  el("nsz-soap").value = ns.NOTE_SOAP || "";
  el("nsz-ll").value = ns.NOTE_LL || "";
  el("nsz-next").value = ns.NOTE_NEXT || "";
}
for(const [id, key] of [["nsz-soap", "NOTE_SOAP"], ["nsz-ll", "NOTE_LL"],
                        ["nsz-next", "NOTE_NEXT"]]){
  el(id).onchange = async e => {
    const v = parseFloat(e.target.value);
    if(!(v >= 6 && v <= 40)) return;
    await setPref({note_sizes: {[key]: v}});
    if(NOTES) loadNotes();     // 판 위 오버레이 글자 크기도 바로 따라온다
  };
}
el("set-subdirs").onchange = async e => {
  await setPref({scan_subdirs: e.target.checked});
  if(typeof loadPatients === "function") loadPatients();  // 목록이 바로 다시 잡힌다
};
for(const b of el("set-unit").children) b.onclick = async () => {
  await setPref({months_unit: b.dataset.u});
  syncPrefs();
  if(NOTES) loadNotes();
};
/* 끄면 원본은 어디에도 남지 않는다 — 확정과 함께 업로드 임시본이 지워진다.
   되돌릴 수 없는 선택이라 켤 때가 아니라 **끌 때** 한 번 확인한다. */
el("set-raw").onchange = async e => {
  if(!e.target.checked &&
     !confirm("원본을 저장하지 않습니다.\n\n" +
              "앞으로 배정되는 사진은 잘린 상태로만 남고, 잘라낸 영역은 " +
              "되돌릴 수 없습니다. 계속할까요?")){
    e.target.checked = true; return;
  }
  await setPref({save_raw: e.target.checked});
  syncPrefs();
};

function syncThemeSeg(){
  const cur = document.documentElement.dataset.theme;
  for(const b of el("set-theme").children) b.setAttribute("aria-pressed", b.dataset.t === cur);
}

/* 저장 위치가 바뀌면 열려 있던 세션은 옛 경로를 가리킨다 — 서버도 함께 버린다 */
function resetSession(){
  SESSION = null;
  el("pchip").hidden = true; el("pchip").innerHTML = "";
  STAGED = []; REVIEW = null; ED.slot = null;
  boardEl.innerHTML = ""; segEl.innerHTML = "";
  setStep("setup", "", "");
  ["pre","proc","fin"].forEach(v => setStep(v, "", "대기"));
  picked = null;
  NOTES = null; NOTE_DIRTY.clear();
  renderVisitBadges();
  syncTabs();
}

async function loadPatients(){
  try{
    const d = await api("/api/patients");
    PATIENTS = d.patients; RULES = d.rules;
    setRootLabel(d.root);
    SKIPPED = d.skipped;
    drawList();
  }catch(e){ el("plist").innerHTML = `<p class="empty">목록을 불러오지 못했습니다<br>${esc(e.message)}</p>`; }
}

function drawList(){
  const terms = el("find").value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const hay = p => `${p.name} ${p.hospital_id} ${p.ortho_id}`.toLowerCase();
  const rows = PATIENTS.filter(p => terms.every(t => hay(p).includes(t)));
  const box = el("plist"); box.innerHTML = "";
  if(!rows.length){
    box.innerHTML = `<p class="empty">${PATIENTS.length ? "검색 결과가 없습니다" : "등록된 환자가 없습니다"}<br>새 환자로 시작하세요</p>`;
    return;
  }
  for(const p of rows){
    const b = document.createElement("button");
    b.className = "prow";
    b.setAttribute("aria-pressed", picked?.folder === p.folder);
    b.innerHTML =
      `<span class="who">${esc(p.name)}</span>` +
      `<span class="no">${[p.hospital_id, p.ortho_id].filter(Boolean).join(" · ")}</span>` +
      `<span class="hist">${visitLine(p)}</span>`;
    b.onclick = () => {
      if(STAGED.length && picked && picked.folder !== p.folder &&
         !confirm(`담아둔 사진 ${STAGED.length}장이 사라집니다. 다른 환자로 넘어갈까요?`)) return;
      if(picked && picked.folder !== p.folder) resetSession();
      picked = p; drawList(); drawDetail();
    };
    box.appendChild(b);
  }
  if(SKIPPED.length){
    const n = document.createElement("details");
    n.className = "skipnote";
    n.innerHTML =
      `<summary>표시하지 않은 폴더 ${SKIPPED.length}개</summary>` +
      `<ul>${SKIPPED.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` +
      `<p>폴더 이름이 <span class="ident">{이름}_{병원번호}_{교정번호}</span> 형식이 아님.</p>`;
    box.appendChild(n);
  }
}

/* ── 우측 패널: 환자 정보 + 폴더 내용 + 사진 투입 ─────────────────────────
   세션은 사진을 처음 넣을 때 만들어진다. 목록을 훑는 것만으로 임시폴더가
   쌓이지 않게, 그리고 "시작" 버튼을 따로 누를 일이 없게. */
/* 차수 이력 한 줄: 첫 차수와 마지막 차수만, 각자의 날짜와 함께.
   그 사이는 …으로 접는다 — 목록에서 알아야 할 건 "언제 시작해서 언제까지"다. */
function visitLine(p){
  const v = p.visits, dt = p.visit_dates || {};
  const warn = (v.length && !p.ppt) ? ` <span class="flag">⚠ PPT 없음</span>` : "";
  if(!v.length) return `<span class="none">기록 없음</span>` + warn;
  const one = L => `<b>${L}</b> ${dt[L] || "—"}`;
  if(v.length === 1) return one(v[0]) + warn;
  return `${one(v[0])}<span class="mid"> … </span>${one(v[v.length - 1])}` + warn;
}

function drawDetail(){
  const p = picked, d = el("detail");
  if(!p){
    d.innerHTML = `<div class="sec"><p class="empty">왼쪽에서 환자를 고르거나<br><b>＋ 새 환자</b>로 등록하세요</p></div>`;
    return;
  }
  const V = p.next_visit;
  const n = RULES.slots || 5;
  const first = shotName(p.ortho_id, V, 1), last = shotName(p.ortho_id, V, n);
  const lost = (p.visits.length || (p.ppt_diag || []).length) && !p.ppt;

  d.innerHTML =
    `<div class="sec idsec">
       <div class="idmain">
         <div class="idline">
           <span class="who">${esc(p.name)}</span>
           <span class="no">${[p.hospital_id, p.ortho_id].filter(Boolean).join(" · ")}</span>
         </div>
         ${timeline(p)}
         <div class="plan">
         <span class="vbig">${V}</span>${p.ppt ? "재진 — 기존 PPT에 슬라이드 1장 추가"
                                               : p.visits.length ? "PPT를 새로 만들어 기록" : "초진 — PPT를 새로 만듭니다"}<br>
         사진 <span class="ident">${esc(first)}</span><span class="mid"> … </span><span class="ident">${esc(last)}</span>
         </div>
         ${lost ? `<div class="note" style="margin-top:10px"><b>PPT가 없습니다.</b>
           ${p.visits.length ? `사진은 ${p.visits.join(", ")}차수까지 있지만 PPT를 찾을 수 없어 새로 만듭니다.
           이전 차수 사진은 겹쳐보기에 쓸 수 없습니다.` : "PPT를 찾을 수 없어 새로 만듭니다."}
           ${(p.ppt_diag || []).map(g =>
             `<br>· <span class="ident">${esc(g.name)}</span> — ${esc(g.why)}`).join("")}</div>` : ""}
       </div>
       ${p.ppt ? `<div class="slides" id="pv-slides">
         <div class="pv face" title="Face" hidden></div>
         <div class="pv" title="Intraoral" hidden><div class="pvgrid"></div></div>
       </div>` : ""}
     </div>

     <div class="sec sec-folder">
       <h3>폴더 내용 <span class="aux ident">${esc(p.folder)}</span></h3>
       <div class="flist" id="flist"><p class="empty">읽는 중…</p></div>
     </div>

     <div class="sec">
       <h3>사진 추가 <span class="aux"><span id="stage-msg"></span><span id="staged-n"></span></span></h3>
       <div class="dropzone" id="dz">
         <div class="dz-empty" id="dz-empty">
           <svg class="dz-mark" viewBox="0 0 34 34" aria-hidden="true">
             <rect x="12.4" y="0"    width="9.2" height="9.2" rx="2"/>
             <rect x="0"    y="12.4" width="9.2" height="9.2" rx="2"/>
             <rect x="12.4" y="12.4" width="9.2" height="9.2" rx="2"/>
             <rect x="24.8" y="12.4" width="9.2" height="9.2" rx="2"/>
             <rect x="12.4" y="24.8" width="9.2" height="9.2" rx="2"/>
           </svg>
           <p class="dz-main">사진을 여기에 놓으세요</p>
           <p class="dz-sub">DRAG<i>·</i>CTRL+V<i>·</i><button id="btn-pick">BROWSE</button></p>
         </div>
         <div class="thumbs" id="thumbs"></div>
         <input type="file" id="file-input" multiple accept="image/*" hidden>
       </div>
       <button class="btn primary wide" id="btn-go" disabled>자동 분류로 ▶</button>
     </div>`;

  loadFolder(p.folder);
  bindDrop();
  drawStaged();
}

/* 첫 진료 → (이력 펼치기) → 마지막 진료. 세로로 놓아 시간축이 그대로 읽히게. */
function timeline(p){
  const v = p.visits, dt = p.visit_dates || {};
  if(!v.length) return `<p class="tl-none">진료 기록 없음</p>`;
  const node = L =>
    `<div class="tl-node"><b>${L}</b><span class="tl-date">${dt[L] || "—"}</span>` +
    `<span class="tl-ago">${ago(dt[L])}</span></div>`;
  if(v.length === 1) return `<div class="tl">${node(v[0])}</div>`;
  return `<div class="tl">${node(v[0])}` +
    `<button class="tl-more" id="btn-hist">재진 이력 자세히 <span class="mid">…</span></button>` +
    `${node(v[v.length - 1])}</div>`;
}

/* "2Y 4M 전" — 진료 간격은 교정 치료에서 그 자체로 임상 정보다.
   단위를 영문 약자로 두면 숫자가 눈에 먼저 들어오고 폭도 일정하다. */
function ago(date){
  if(!date) return "";
  const [y, m, d] = date.split(".").map(Number);
  const then = new Date(y, m - 1, d), now = new Date();
  const days = Math.floor((now - then) / 86400000);
  if(days <= 0) return "오늘";
  if(days < 31) return `${days}D 전`;
  let mo = (now.getFullYear() - then.getFullYear()) * 12 + (now.getMonth() - then.getMonth());
  if(now.getDate() < then.getDate()) mo--;
  if(mo < 12) return `${mo}M 전`;
  const yy = Math.floor(mo / 12), mm = mo % 12;
  return mm ? `${yy}Y ${mm}M 전` : `${yy}Y 전`;
}

function openHist(){
  const v = FOLDER?.visits || [];
  el("hist-sub").textContent = `${esc(picked.name)} · 총 ${v.length}차수`;
  el("hist-list").innerHTML = v.map(x =>
    `<div class="hrow"><b>${x.visit}</b>` +
    `<span class="hd">${x.date}</span>` +
    `<span class="ha">${ago(x.date)}</span>` +
    `<span class="hn">사진 ${x.photos}</span></div>`).join("")
    || `<p class="empty">차수 기록이 없습니다</p>`;
  el("dlg-hist").showModal();
}

/* 사진 추가 섹션 제목 오른쪽에 상황을 알린다 — 진행과 오류가 같은 자리,
   색으로만 구분한다. 아래쪽에 빈 줄을 예약해 두지 않아도 된다. */
function stageMsg(text, kind){
  const n = el("stage-msg"); if(!n) return;
  n.textContent = text || "";
  n.dataset.kind = kind || "";
}

/* 확정하면 붙을 파일명. config의 photo_pattern을 그대로 쓴다. */
const shotName = (ortho, visit, i) => (RULES.photo_pattern || "{ortho_id}_{visit} ({index}).jpg")
  .replace("{ortho_id}", ortho).replace("{visit}", visit).replace("{index}", i);

/* 시작 화면 미리보기 — 슬라이드에 실린 그림이 진실이다. 서버가 PPT에서 첫
   차수 십자 5장 + 얼굴 2장을 복원해 준다(완성본 번호 규칙이 다른 옛 폴더에서도
   맞다). PPT를 읽지 못하면 종전대로 완성본 JPG 의 (1)~(n) 번호로 물러난다. */
async function fillSlidePreviews(folder, items){
  const box = el("pv-slides"); if(!box) return;
  try{
    const d = await api("/api/ppt_preview?folder=" + encodeURIComponent(folder));
    if(pvRender(box, d)) return;
  }catch(e){ /* 폴백으로 */ }
  pvRenderFromFiles(box, folder, items);
}

function pvRender(box, d){
  let any = false;
  const fb = box.querySelector(".pv.face");
  if(fb && d.faces && d.faces.length){
    fb.innerHTML = d.faces.map(u => `<img src="${u}" alt="">`).join("");
    fb.hidden = false; any = true;
  }
  const grid = box.querySelector(".pvgrid");
  if(grid && d.slots){
    grid.innerHTML = "";
    for(const meta of SLOTS){
      const u = d.slots[meta.key];
      if(!u) continue;
      const im = document.createElement("img");
      im.src = u; im.alt = ""; im.style.gridArea = meta.area;
      grid.appendChild(im);
    }
    if(grid.children.length){ grid.parentElement.hidden = false; any = true; }
  }
  return any;
}

function pvRenderFromFiles(box, folder, items){
  const idx = n => { const m = n.match(/\((\d+)\)/); return m ? +m[1] : 0; };
  const ph = items.filter(i => i.kind === "photo" && !i.raw && i.visit && idx(i.name));
  if(!ph.length) return;
  const visit = ph.map(i => i.visit).sort()[0];               // 초진 차수
  const vis = ph.filter(i => i.visit === visit);
  const src = i => `/api/file/${encodeURIComponent(folder)}/${encodeURIComponent(i.name)}`;
  const nIO = (RULES && RULES.slots) || 5;

  const faces = vis.filter(i => idx(i.name) > nIO)
                   .sort((a, b) => idx(a.name) - idx(b.name)).slice(0, 2);
  const fb = box.querySelector(".pv.face");
  if(fb && faces.length){
    fb.innerHTML = faces.map(i => `<img src="${src(i)}" alt="">`).join("");
    fb.hidden = false;
  }

  const grid = box.querySelector(".pvgrid");
  if(grid){
    grid.innerHTML = "";
    for(const [ix, slot] of Object.entries((RULES && RULES.io_slots) || {})){
      const it = vis.find(i => idx(i.name) === +ix);
      const meta = SLOTS.find(s => s.key === slot);
      if(!it || !meta) continue;
      const im = document.createElement("img");
      im.src = src(it); im.alt = ""; im.style.gridArea = meta.area;
      grid.appendChild(im);
    }
    if(grid.children.length) grid.parentElement.hidden = false;
  }
}

/* 폴더 내용 — 파일탐색기처럼 있는 그대로 */
async function loadFolder(folder){
  const box = el("flist"); if(!box) return;
  try{
    const d = await api("/api/folder?folder=" + encodeURIComponent(folder));
    FOLDER = d;
    const hb = el("btn-hist"); if(hb) hb.onclick = openHist;
    fillSlidePreviews(folder, d.items);
    if(!d.items.length){ box.innerHTML = `<p class="empty">폴더가 비어 있습니다</p>`; return; }
    // 구형 .ppt 는 코드로 열 수 없다 — 발견되면 F&Q 의 변환 안내를 가리킨다
    const oldPpt = d.items.some(i => i.name.toLowerCase().endsWith(".ppt"));
    box.innerHTML = (oldPpt ? `<div class="note">⚠ 구형 .ppt 파일이 있습니다 — ` +
      `이 프로그램에서는 열 수 없습니다. 방법은 F&Q를 참고해주세요.</div>` : "") +
      d.items.map(it =>
      `<div class="frow" data-kind="${it.kind}">` +
        `<span class="ic">${it.kind === "ppt" ? "📄" : it.kind === "photo" ? "🖼" : "▫"}</span>` +
        `<span class="fn">${esc(it.name)}</span>` +
        `<span class="fv">${it.visit || ""}</span>` +
        `<span class="fs">${fmtSize(it.size)}</span>` +
      `</div>`).join("");
  }catch(e){
    FOLDER = null;
    box.innerHTML = `<p class="empty">폴더가 아직 없습니다<br>확정할 때 만들어집니다</p>`;
  }
}
const fmtSize = n => n < 1024 ? `${n} B`
  : n < 1048576 ? `${(n/1024).toFixed(0)} KB` : `${(n/1048576).toFixed(1)} MB`;

/* 사진 투입 — 끌어다 놓기 / 붙여넣기 / 파일 선택 */
function bindDrop(){
  const dz = el("dz"), fi = el("file-input");
  const pick = el("btn-pick"); if(pick) pick.onclick = e => { e.stopPropagation(); fi.click(); };
  dz.onclick = () => fi.click();
  fi.onchange = () => { if(fi.files.length) addFiles([...fi.files]); fi.value = ""; };
  for(const ev of ["dragover","dragenter"])
    dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add("over"); });
  for(const ev of ["dragleave","drop"])
    dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove("over"); });
  dz.addEventListener("drop", e => {
    const files = [...e.dataTransfer.files].filter(f => f.type.startsWith("image/"));
    if(files.length) addFiles(files);
  });
  el("btn-go").onclick = runClassify;
}

async function ensureSession(){
  if(SESSION && SESSION.folder === picked.folder) return SESSION;
  const r = await api("/api/session", {method:"POST",
    headers:{"Content-Type":"application/json"}, body: JSON.stringify({folder: picked.folder})});
  startSession(r);
  return r;
}

async function addFiles(files){
  stageMsg(`${files.length}장 올리는 중…`, "busy");
  try{
    await ensureSession();
    const fd = new FormData();
    for(const f of files) fd.append("files", f);
    const r = await api(`/api/photos/${SESSION.session_id}`, {method:"POST", body: fd});
    STAGED = r.photos;
    stageMsg("");
    drawStaged();
    // 노트를 다시 받는다 — 날짜·개월은 사진 EXIF 촬영일에서 오는데, 노트는
    // 세션이 열리자마자(사진이 없을 때) 한 번 받아 두므로 여기서 갱신하지
    // 않으면 새로고침 전까지 작업일이 박혀 보인다.
    loadNotes();
  }catch(e){ stageMsg(e.message, "err"); }
}

async function dropStaged(pid){
  try{
    const r = await api(`/api/photos/${SESSION.session_id}/${pid}`, {method:"DELETE"});
    STAGED = r.photos; drawStaged();
    loadNotes();     // 사진이 빠지면 촬영일 최빈값도 달라질 수 있다
  }catch(e){ stageMsg(e.message, "err"); }
}

function drawStaged(){
  const box = el("thumbs"); if(!box) return;
  const empty = el("dz-empty");
  if(empty) empty.hidden = STAGED.length > 0;
  el("dz").classList.toggle("filled", STAGED.length > 0);
  box.innerHTML = STAGED.map(p =>
    `<figure class="th"><img src="${p.thumb}" alt="">` +
    `<button class="x" data-pid="${p.id}" title="빼기">×</button></figure>`).join("") +
    (STAGED.length ? `<button class="th add" id="th-add" title="사진 더 추가">＋</button>` : "");
  for(const b of box.querySelectorAll(".x"))
    b.onclick = e => { e.stopPropagation(); dropStaged(b.dataset.pid); };
  const n = el("staged-n"); if(n) n.textContent = STAGED.length ? `${STAGED.length}장` : "";
  // 버튼은 항상 자리를 지킨다 — 사진이 들어와도 아래 요소가 움직이지 않게
  const go = el("btn-go"); if(go) go.disabled = !STAGED.length;
  setStep("setup", STAGED.length ? "done" : "",
          SESSION ? `${SESSION.ids.name} · ${SESSION.visit}` : "");
  setStep("pre", "", STAGED.length ? `${STAGED.length}장 대기` : "대기");
}

/* 새 환자 — 모달. 취소하면 보던 목록이 그대로 남는다. */
const dlg = () => el("dlg-new");
function openNewDialog(){
  const needH = ((RULES && RULES.folder_pattern) || "").includes("{hospital_id}");
  el("new-rules").textContent =
    `한글·영문 이름 · 병원 ${RULES.hospital_digits}자리` +
    (needH ? "" : "(선택)") + ` · 교정과 ${RULES.ortho_digits}자리`;
  el("f-hosp").maxLength  = RULES.hospital_digits;
  el("f-ortho").maxLength = RULES.ortho_digits;
  el("f-hosp").placeholder  = `${RULES.hospital_digits}자리 숫자` +
    (needH ? "" : " — 없으면 비워두세요");
  el("f-ortho").placeholder = `${RULES.ortho_digits}자리 숫자`;
  for(const id of ["f-name","f-hosp","f-ortho"]) el(id).value = "";
  el("new-err").textContent = "";
  syncPreview();
  dlg().showModal();
  el("f-name").focus();
}
const newIds = () => ({name: el("f-name").value.trim(),
                       hospital_id: el("f-hosp").value.trim(),
                       ortho_id: el("f-ortho").value.trim()});
function syncPreview(){
  const needH = ((RULES && RULES.folder_pattern) || "").includes("{hospital_id}");
  const v = newIds(), full = v.name && v.ortho_id && (v.hospital_id || !needH);
  // 서버가 실제로 쓸 형식(★ 맨 위 등록 형식)으로 미리 보여준다 — 하드코딩하면
  // 설정에서 형식을 바꿨을 때 미리보기와 실제 폴더 이름이 갈라진다.
  const pat = (RULES && RULES.folder_pattern) || "{name}_{hospital_id}_{ortho_id}";
  el("f-preview").innerHTML = "만들어질 폴더 <b>" +
    (full ? esc(pat.replace("{name}", v.name)
                   .replace("{hospital_id}", v.hospital_id)
                   .replace("{ortho_id}", v.ortho_id)) : "—") + "</b>";
}

async function openSession(body, errId){  // 새 환자 모달 전용
  const err = el(errId); if(err) err.textContent = "";
  try{
    const r = await api("/api/session", {method:"POST",
      headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)});
    dlg().close();
    startSession(r);
    picked = {folder: r.folder, name: r.ids.name, hospital_id: r.ids.hospital_id,
              ortho_id: r.ids.ortho_id, visits: r.prev_visits, next_visit: r.visit,
              ppt: r.ppt_exists ? r.folder + ".pptx" : null, photos: 0, updated: "—"};
    await loadPatients();
    const hit = PATIENTS.find(x => x.folder === r.folder);
    if(hit) picked = hit;
    drawList(); drawDetail();
  }catch(e){
    if(err) err.textContent = e.message;
    // 이미 있는 환자를 새로 등록하려 한 경우 — 모달을 닫고 목록에서 바로 집어준다
    if(e.data?.error === "patient_exists"){
      const hit = PATIENTS.find(p => p.folder === e.data.folder);
      if(hit){
        dlg().close();
        picked = hit; drawList(); drawDetail();
        stageMsg(e.message, "err");
      }
    }
  }
}

/* 세션이 열리면 헤더와 사이드바가 실제 상태를 말하게 된다 */
function startSession(r){
  // 이전 세션의 이미지 캐시를 비운다 — 세션이 끝난 URL은 다시 조회될 수 없는
  // 죽은 키인데, Map이 쥐고 있는 한 원본(장당 수 MB)이 해제되지 않아 환자를
  // 넘어갈수록 탭 메모리가 쌓인다. 지금 세션 것만 남긴다.
  for(const url of [...imgCache.keys()]){
    if(/^\/api\/(thumb|reference)\//.test(url) && !url.includes(`/${r.session_id}/`))
      imgCache.delete(url);
  }
  SESSION = r;
  el("pchip").innerHTML =
    `<span class="nm">${esc(r.ids.name)}</span>` +
    `<span class="id">${[r.ids.hospital_id, r.ids.ortho_id].filter(Boolean).join(" · ")}</span>` +
    `<span class="visit" title="차수 ${r.visit}">${r.visit}</span>`;
  el("pchip").hidden = false;

  STAGED = []; REVIEW = null; ED.slot = null;
  setStep("setup", "", `${r.ids.name} · ${r.visit}`);
  setStep("pre",  "", r.ppt_exists ? `이전 ${r.prev_visits.join(",")}` : "대기");
  setStep("proc", "", "대기");
  setStep("fin",  "", "대기");
  NOTES = null; NOTE_DIRTY.clear();
  renderVisitBadges();
  syncTabs();
}

function setStep(v, state, st){
  const n = document.querySelector(`.nav[data-view="${v}"]`);
  if(!n) return;
  n.dataset.state = state;
  n.querySelector(".st").textContent = st;
}

/* 초진/재진은 mode가 아니라 차수 글자로 판정한다 — main.py:982-985와 같은 규칙.
   mode는 'PPT를 새로 만드는가'일 뿐이라, 사진은 C차수까지 있는데 PPT만 없는 폴더도
   mode='first'가 된다. 그걸 초진이라 부르면 기록이 틀린다. */
function visitInfo(){
  if(!SESSION) return null;
  const v = SESSION.visit, prev = SESSION.prev_visits || [];
  const first = v === "A";
  let basis = "", tone = first ? "first" : "revisit";

  if(!first){
    if(SESSION.mode !== "revisit"){
      // 사진은 있는데 PPT가 없다 — 정합할 기준 자체가 없다.
      basis = "기준 없음"; tone = "warn";
    }else{
      // 분류가 끝났으면 실제로 쓰인 기준을, 아직이면 예상 기준을 보여 준다.
      const used = REVIEW ? [...new Set(Object.values(REVIEW.slots || {})
                     .filter(p => p && p.ref_visit).map(p => p.ref_visit))] : [];
      const pick = used.length ? used
                 : [prev.length && `직전(${prev[prev.length-1]})`,
                    prev.includes("A") && prev[prev.length-1] !== "A" && "초진(A)"].filter(Boolean);
      if(pick.length) basis = "기준 " + pick.join(", ");
    }
  }
  return {text: first ? "초진" : `재진 ${v}`, basis, tone};
}

function renderVisitBadges(){
  const info = visitInfo();
  for(const id of ["pre-visit","proc-visit","fin-visit"]){
    const n = el(id); if(!n) continue;
    if(!info){ n.hidden = true; n.textContent = ""; n.removeAttribute("data-tone"); continue; }
    n.hidden = false;
    n.dataset.tone = info.tone;
    n.textContent = info.basis ? `${info.text} · ${info.basis}` : info.text;
  }
}

/* ══ FACE 배치 ═══════════════════════════════════════════════════════════════
   분류기는 얼굴을 정면/45도/측면으로 나누지 못한다. 어느 사진이 어느 슬라이드
   어느 쪽에 갈지는 사람이 정한다. 오른쪽 상자의 위는 자리(2열 = 슬라이드 좌/우),
   아래는 아직 자리를 못 잡은 사진들이고, 왼쪽 판이 그 결과를 슬라이드로 보여준다. */
const faceCells   = () => (CASE && CASE.cells)   || [];
const faceMirrors = () => (CASE && CASE.mirrors) || [];
const faceSlots   = () => (REVIEW && REVIEW.face_slots) || {};
const facePhotos  = () => (REVIEW && REVIEW.face) || [];
const facePhoto   = pid => facePhotos().find(p => p.id === pid) || null;
const faceAll     = () => [...faceCells(), ...faceMirrors()];

function slideName(c){ return c.label || `슬라이드 ${c.slide}`; }
const posName = p => p === "L" ? "좌측" : p === "R" ? "우측" : p === "C" ? "중앙" : "전체";
/* TEMPLATE 탭은 양식에서 그대로 가져오는 슬라이드 전체를 훑는다 — 사진 자리가
   없는 장(표지·구내 개별 등)도 넘겨 보며 계측선을 손볼 수 있어야 하기 때문이다. */
const faceSlideNos = () => ((CASE && CASE.slides && CASE.slides.length)
  ? CASE.slides : [...new Set(faceAll().map(c => c.slide))].sort((a, b) => a - b));
const cellsOf = n => faceAll().filter(c => c.slide === n).sort((a, b) => a.x - b.x);
const linesOf = n => ((CASE && CASE.lines) || {})[n] || [];
const slideLabelOf = n => (((CASE && CASE.labels) || {})[n]) || "";

/* 얼굴 자리 편집기 — 구내(ED)와 같은 규약이다. dx·dy 는 백엔드와 같은 단위
   (창cm × px_per_cm = 1270px 기준)로 들고, 화면 캔버스는 그보다 작게 쓰므로
   그릴 때만 배율 k 를 곱한다. 그래서 캔버스 해상도를 바꿔도 저장값은 그대로다. */
const FED = {cell:null, dx:0, dy:0, scale:1, angle:0, img:null, drag:false, lx:0, ly:0, timer:null};
const faceCanvas = {};                        // cell -> 보드 캔버스
const FACE_EDIT_W = 520, FACE_CELL_W = 240;   // 캔버스 실픽셀 폭

const faceEditors  = () => (REVIEW && REVIEW.face_editors) || {};
const faceEditorOf = k => faceEditors()[k] || {dx:0, dy:0, scale:1, angle:0};
const faceFraming  = () => (REVIEW && REVIEW.face_framing) || {};
const cellOf  = k => faceAll().find(c => c.cell === k) || null;
const facePpc = () => (HEALTH && HEALTH.px_per_cm) || 100;

/* 지금 보고 있는 슬라이드. 사진 자리가 없는 장도 있으므로 선택된 자리(FED.cell)
   와 따로 들고 있어야 한다 — 자리에서 슬라이드를 역산하면 표지 같은 장을 못 연다. */
let FSLIDE = null;

function ensureFocus(){
  const nos = faceSlideNos();
  if(!nos.length) return;
  // 양식의 **첫 장부터** 연다. 사진이 있는 장으로 건너뛰면 표지·환자정보 같은
  // 앞장을 한 번도 안 보고 지나치게 된다 — 거기도 사람이 채울 것이 있다.
  if(!nos.includes(FSLIDE)) FSLIDE = nos[0];
  // 이 슬라이드에 사진 자리가 있으면 하나 켜 둔다(없으면 선만 있는 장이다)
  const here = cellsOf(FSLIDE).filter(c => !c.from);
  if(!here.some(c => c.cell === FED.cell)){
    const slots = faceSlots();
    FED.cell = (here.find(c => slots[c.cell]) || here[0] || {}).cell || null;
  }
}

function drawFaceSeg(){
  const seg = el("face-seg"), slots = faceSlots();
  seg.innerHTML = "";
  for(const n of faceSlideNos()){
    const row = cellsOf(n), nl = linesOf(n).length;
    const done = row.filter(c => slots[c.cell]).length;
    const g = document.createElement("button");
    g.textContent = n;
    const what = [slideLabelOf(n),
                  row.length ? `사진 ${done}/${row.length}` : "",
                  nl ? `선 ${nl}` : ""].filter(Boolean).join(" · ");
    g.title = `슬라이드 ${n}${what ? " — " + what : ""}`;
    // 채울 자리가 있는 장만 '다 됐다'를 표시한다(선만 있는 장은 해당 없음)
    g.className = row.length && done === row.length ? "done" : "";
    g.setAttribute("aria-pressed", String(n === FSLIDE));
    g.onclick = () => { FSLIDE = n; FED.cell = null; drawFace(); };
    seg.appendChild(g);
  }
}

/* 지금 보고 있는 슬라이드. 자리를 고르면 따라 바뀌므로 따로 들고 있지 않는다. */
/* 캔버스를 자리 비율로 맞추고, 백엔드 픽셀 → 캔버스 픽셀 배율 k 를 돌려준다.

   해상도는 **화면에 실제로 보이는 크기 × 화면 배율**로 잡는다. 상수로 박아 두면
   판이 커지거나 HiDPI 화면에서 그만큼 뿌예진다(원본 사진은 /api/thumb 이 원본
   그대로 주므로 손실은 여기서만 생긴다). 창의 렌더 해상도(c.w × px_per_cm)를
   넘겨 봐야 화면에서 더 보이지 않으므로 거기서 자른다. */
function faceFit(cv, c, fallbackW){
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const css = cv.clientWidth || fallbackW;
  const w = Math.max(1, Math.round(Math.min(css * dpr, c.w * facePpc())));
  const h = Math.max(1, Math.round(c.h / c.w * w));
  if(cv.width !== w || cv.height !== h){ cv.width = w; cv.height = h; }
  return {w, h, k: w / (c.w * facePpc())};
}

function drawFaceComposite(ctx, W, H, img, st, k, border, flipV){
  ctx.clearRect(0, 0, W, H); ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, H);
  if(img){
    // 6000x4000을 한 번에 줄여 그리므로 보간 품질을 명시한다(기본값은 브라우저마다 다르다)
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.save();
    ctx.translate(W / 2 + st.dx * k, H / 2 + st.dy * k);
    ctx.rotate(st.angle * Math.PI / 180); ctx.scale(st.scale, st.scale);
    if(flipV) ctx.scale(1, -1);        // 교합면은 뒤집어 본다 (drawComposite 와 같은 규약)
    coverDraw(ctx, img, W, H); ctx.restore();
  }
  if(border){
    ctx.strokeStyle = "rgba(61,144,240,.65)"; ctx.lineWidth = 2;
    ctx.strokeRect(1, 1, W - 2, H - 2);
  }
}

/* ── 구내 한 장짜리 슬라이드(12~16) ──────────────────────────────────────────
   여기는 **배정하는 자리가 아니다.** 구내 검수에서 정한 대표 사진과 구도가 그대로
   온다. 그래도 그려야 한다 — 안 그리면 그 장이 새까맣고, 사진이 안 들어간 것처럼
   보인다.

   창이 십자뷰 슬롯보다 3배 크다. 편집값 dx·dy 는 창 기준의 절대량이라 창 폭
   비율만큼 같이 키워야 잘린 영역이 같아진다 — 백엔드 `_place_intraoral` 과 같은
   환산이다. 여기와 결과물이 어긋나면 미리보기가 거짓말을 하는 것이다. */
const ioCells = () => (CASE && CASE.intraoral) || [];
const ioCanvas = {};

async function renderIoCell(slot){
  const cv = ioCanvas[slot]; if(!cv) return;
  const c = ioCells().find(x => x.slot === slot), p = primaryOf(slot), win = slotWindow(slot);
  if(!c || !p || !win) return;
  const {w, h, k} = faceFit(cv, c, FACE_CELL_W);
  const r = c.w / win.w;                         // 슬롯 창 → 이 창
  const st = {dx: p.editor.dx * r, dy: p.editor.dy * r,
              scale: p.editor.scale, angle: p.editor.angle};
  drawFaceComposite(cv.getContext("2d"), w, h, await getImg(p.thumb), st, k, false, p.flip_v);
}

function drawIoCells(sheet, n){
  for(const c of ioCells()){
    if(c.slide !== n) continue;
    const p = primaryOf(c.slot);
    const d = document.createElement("div");
    d.className = "iocell" + (p ? "" : " empty");
    d.style.left   = `${c.x / CASE.slide_w * 100}%`;
    d.style.top    = `${c.y / CASE.slide_h * 100}%`;
    d.style.width  = `${c.w / CASE.slide_w * 100}%`;
    d.style.height = `${c.h / CASE.slide_h * 100}%`;
    d.title = "구내 검수에서 정한 사진이 그대로 들어갑니다";
    if(p){
      const cv = document.createElement("canvas");
      d.appendChild(cv); ioCanvas[c.slot] = cv;
      renderIoCell(c.slot);
    } else {
      d.textContent = "구내 사진이 아직 없습니다";
    }
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = (SLOTS.find(s => s.key === c.slot) || {}).nm || c.slot;
    d.appendChild(tag);
    sheet.appendChild(d);
  }
}

async function renderFaceCell(key){
  const cv = faceCanvas[key], c = cellOf(key); if(!cv || !c) return;
  const pid = faceSlots()[key], p = pid ? facePhoto(pid) : null;
  const {w, h, k} = faceFit(cv, c, FACE_CELL_W);
  // 편집 중인 자리는 아직 저장 전 값이 있으므로 FED 를 그대로 쓴다
  const st = key === FED.cell ? FED : faceEditorOf(key);
  drawFaceComposite(cv.getContext("2d"), w, h, p ? await getImg(p.thumb) : null, st, k, false);
}

/* 한 번에 한 슬라이드만 상대한다 — 슬라이드는 우측 상단 번호로 넘긴다.
   판은 **슬라이드 자체의 축소판**이다: 자리의 위치·크기를 template.pptx 앵커에서
   읽은 값(cm)을 슬라이드 크기로 나눈 비율로 그대로 얹는다(_face_layout_json).
   여백까지 실물과 같아서, 여기서 맞춘 그림이 슬라이드에서 어떻게 앉을지 바로 보인다. */
function drawFaceBoard(){
  const board = el("face-board"), slots = faceSlots();
  const n = FSLIDE;
  board.innerHTML = "";
  for(const k of Object.keys(faceCanvas)) delete faceCanvas[k];
  for(const k of Object.keys(ioCanvas)) delete ioCanvas[k];
  if(n === null){ board.textContent = "슬라이드가 없습니다"; return; }
  const row = cellsOf(n);
  const sheet = document.createElement("div");
  sheet.className = "fsheet";
  sheet.style.aspectRatio = `${CASE.slide_w}/${CASE.slide_h}`;
  for(const c of row){
    const p = slots[c.cell] ? facePhoto(slots[c.cell]) : null;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "fcell" + (p ? "" : " empty") + (c.from ? " derived" : "") +
                  (c.cell === FED.cell ? " on" : "");
    b.dataset.cell = c.cell;
    b.style.left   = `${c.x / CASE.slide_w * 100}%`;
    b.style.top    = `${c.y / CASE.slide_h * 100}%`;
    b.style.width  = `${c.w / CASE.slide_w * 100}%`;
    b.style.height = `${c.h / CASE.slide_h * 100}%`;
    b.title = c.from ? `${slideName(c)} — 다른 자리를 따라갑니다`
                     : `${slideName(c)} ${posName(c.pos)}`;
    if(p){
      const cv = document.createElement("canvas");
      b.appendChild(cv); faceCanvas[c.cell] = cv;
      renderFaceCell(c.cell);
    } else {
      b.appendChild(document.createTextNode("여기에 넣기"));
    }
    b.insertAdjacentHTML("beforeend",
      `<span class="tag">${c.from ? "←" : ""}${posName(c.pos)}</span>`);
    b.onclick = () => pickFace(c.cell);
    if(!c.from){
      b.ondragover  = e => { e.preventDefault(); b.classList.add("over"); };
      b.ondragleave = () => b.classList.remove("over");
      b.ondrop = e => {
        e.preventDefault(); b.classList.remove("over");
        const pid = e.dataTransfer.getData("text/plain");
        if(pid) assignFace(c.cell, pid);
      };
    }
    sheet.appendChild(b);
  }
  // 양식 도형(캡션 띠)을 사진 위에 얹는다. 이걸 빼고 그리면 사진이 다 보이는
  // 것처럼 착각하게 되는데, 실제로는 가려지므로 얼굴을 그만큼 올려 잡아야 한다.
  for(const r of (CASE.overlays || {})[n] || []){
    const m = document.createElement("div");
    m.className = "fmask";
    m.style.left   = `${r.x / CASE.slide_w * 100}%`;
    m.style.top    = `${r.y / CASE.slide_h * 100}%`;
    m.style.width  = `${r.w / CASE.slide_w * 100}%`;
    m.style.height = `${r.h / CASE.slide_h * 100}%`;
    m.title = "양식의 검은 띠 — 슬라이드에서는 사진이 여기 가려집니다";
    sheet.appendChild(m);
  }
  drawIoCells(sheet, n);
  drawSlideLines(sheet, n);
  board.appendChild(sheet);
  drawInfoBoxes();
  // DOM 에 붙은 뒤에야 clientWidth 가 잡힌다 — 실제 표시 크기로 다시 그린다
  for(const k of Object.keys(faceCanvas)) renderFaceCell(k);
  for(const k of Object.keys(ioCanvas)) renderIoCell(k);
}

/* ── 양식 첫 장(환자정보) ─────────────────────────────────────────────────────
   사진 자리가 없는 장이라 판이 새까맣게 비어 보인다. **확정 뒤 실제로 적힐 글**을
   그대로 얹어, 무엇이 들어가는 장인지 보이게 하고 눌러서 고칠 수 있게 한다.
   자리·글꼴은 양식에서 읽은 값이라 슬라이드에서 앉을 모습 그대로다. */
const infoCfg     = () => (NOTES && NOTES.patient_info) || null;
const infoSlideOn = () => { const i = infoCfg(); return !!(i && i.enabled && FSLIDE === i.slide); };

function drawInfoBoxes(){
  const board = el("face-board"); if(!board) return;
  const sheet = board.querySelector(".fsheet"); if(!sheet) return;
  let layer = sheet.querySelector(".finfo");
  if(!infoSlideOn()){ if(layer) layer.remove(); return; }
  if(!layer){ layer = document.createElement("div"); layer.className = "finfo"; sheet.appendChild(layer); }
  const info = infoCfg();
  layer.innerHTML = "";
  for(const [name, b] of Object.entries(info.boxes || {})){
    const hot = !!b.editable;
    const d = document.createElement(hot ? "button" : "div");
    if(hot){ d.type = "button"; d.title = "눌러서 고치기"; d.onclick = showInfoDock; }
    d.className = "fnote" + (hot ? " on" : "");
    d.style.left   = `${b.x / CASE.slide_w * 100}%`;
    d.style.top    = `${b.y / CASE.slide_h * 100}%`;
    d.style.width  = `${b.w / CASE.slide_w * 100}%`;
    d.style.height = `${b.h / CASE.slide_h * 100}%`;
    const f = b.font || {};
    if(f.size_pt) d.style.setProperty("--nt-pt", f.size_pt);
    if(f.color) d.style.setProperty("--nt-ink", `#${f.color}`);
    if(f.bold) d.style.setProperty("--nt-w", "700");
    d.appendChild(document.createTextNode((info.preview || {})[name] ?? b.text ?? ""));
    layer.appendChild(d);
  }
}

/* TEMPLATE 의 dock 을 사진 편집기 ↔ 환자정보 칸으로 바꾼다.
   환자정보 장에는 사진 자리가 없어 노브를 띄워 봐야 쓸 데가 없다. */
function drawInfoDock(){
  const photo = el("face-dock-photo"), note = el("face-dock-note");
  if(!photo || !note) return;
  const on = infoSlideOn();
  photo.hidden = on; note.hidden = !on;
  if(!on) return;
  el("face-dock-title").firstChild.textContent = "환자정보 · 첫 슬라이드";
  const box = el("fi-fields"), sub = el("fi-sub");
  const keys = (infoCfg().fields) || [];
  sub.textContent = keys.length ? "이 칸들이 왼쪽 장에 적힙니다" : "채울 칸이 없습니다";
  box.innerHTML = "";
  for(const k of keys){
    const f = (NOTES.fields || []).find(x => x.key === k);
    if(f) box.appendChild(noteFieldRow(f));
  }
}

function showInfoDock(){
  drawInfoDock();
  el("fi-fields").querySelector("input,textarea")?.focus();
}

/* 파생 자리(10·11)는 볼 수만 있다 — 슬라이드 4 좌측을 그대로 따라가므로
   여기서 고쳐 봐야 다음 새로고침에 되돌아온다. 조작부를 잠가 그 사실을 알린다. */
function setFaceKnobsEnabled(on){
  for(const id of ["fed-angle", "fed-scale", "fed-tx", "fed-ty", "fed-reset"])
    el(id).disabled = !on;
  el("face-canvas").style.cursor = on ? "move" : "default";
}

/* 양식의 계측선을 판 위에 그리고 끌어 옮길 수 있게 한다.
   길이·각도는 그대로 두고 **자리만** 옮긴다 — 양식이 정한 기울기가 진단 기준이라
   임의로 바뀌면 안 된다. 옮긴 양(cm)만 서버에 남고 확정할 때 도형에 반영된다. */
function drawSlideLines(sheet, n){
  const list = linesOf(n); if(!list.length) return;
  const sl = CASE, W = sl.slide_w, H = sl.slide_h;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "flines");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  for(const ln of list){
    const mv = LINEMOVES[ln.id] || [0, 0];
    const g = document.createElementNS(svg.namespaceURI, "g");
    g.setAttribute("class", "fline" + (LINEMOVES[ln.id] ? " moved" : ""));
    g.dataset.id = ln.id;
    // 굵은 투명선을 밑에 깔아 잡기 쉽게 한다(실제 선은 0.05cm 남짓이라 못 잡는다)
    for(const cls of ["hit", "vis"]){
      const e = document.createElementNS(svg.namespaceURI, "line");
      e.setAttribute("class", cls);
      e.setAttribute("x1", ln.x1 + mv[0]); e.setAttribute("y1", ln.y1 + mv[1]);
      e.setAttribute("x2", ln.x2 + mv[0]); e.setAttribute("y2", ln.y2 + mv[1]);
      if(cls === "vis" && ln.width_pt)
        e.setAttribute("stroke-width", (ln.width_pt * W / 720).toFixed(3));
      g.appendChild(e);
    }
    g.appendChild(document.createElementNS(svg.namespaceURI, "title"))
     .textContent = `${ln.name} — 끌어서 옮기기 · 더블클릭하면 제자리`;
    bindLineDrag(g, ln, sheet);
    svg.appendChild(g);
  }
  sheet.appendChild(svg);
}

let LINEMOVES = {};              // id -> [dx_cm, dy_cm]
let lineSaveTimer = null;

function bindLineDrag(g, ln, sheet){
  let sx = 0, sy = 0, base = null;
  const cm = e => {                       // 화면 px → 슬라이드 cm
    const r = sheet.getBoundingClientRect();
    return [e.clientX / r.width * CASE.slide_w, e.clientY / r.height * CASE.slide_h];
  };
  g.addEventListener("pointerdown", e => {
    e.preventDefault(); e.stopPropagation();
    [sx, sy] = cm(e);
    base = (LINEMOVES[ln.id] || [0, 0]).slice();
    g.setPointerCapture(e.pointerId);
    g.classList.add("drag");
  });
  g.addEventListener("pointermove", e => {
    if(!base) return;
    const [x, y] = cm(e);
    LINEMOVES[ln.id] = [base[0] + x - sx, base[1] + y - sy];
    moveLineEls(g, ln);
  });
  const stop = () => {
    if(!base) return;
    base = null; g.classList.remove("drag"); g.classList.add("moved");
    queueLineSave();
  };
  g.addEventListener("pointerup", stop);
  g.addEventListener("pointercancel", stop);
  g.addEventListener("dblclick", e => {
    e.preventDefault();
    delete LINEMOVES[ln.id];
    g.classList.remove("moved"); moveLineEls(g, ln); queueLineSave();
  });
}

function moveLineEls(g, ln){
  const mv = LINEMOVES[ln.id] || [0, 0];
  for(const e of g.querySelectorAll("line")){
    e.setAttribute("x1", ln.x1 + mv[0]); e.setAttribute("y1", ln.y1 + mv[1]);
    e.setAttribute("x2", ln.x2 + mv[0]); e.setAttribute("y2", ln.y2 + mv[1]);
  }
}

function queueLineSave(){
  clearTimeout(lineSaveTimer);
  lineSaveTimer = setTimeout(async () => {
    if(!SESSION) return;
    const note = el("face-saved");
    if(note) note.textContent = "…";
    try{
      // 되돌린 선은 null 로 보내 서버에서도 지운다
      const moves = {};
      for(const ln of linesOf(FSLIDE)) moves[ln.id] = LINEMOVES[ln.id] || null;
      await api("/api/lines", {method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({session_id: SESSION.session_id, moves})});
      if(note) note.textContent = "저장됨";
    }catch(e){ if(note) note.textContent = e.message; }
  }, 300);
}

async function pickFace(key){
  const c = cellOf(key); if(!c) return;
  const moved = FSLIDE !== c.slide;          // 슬라이드가 바뀌면 판을 다시 짠다
  FSLIDE = c.slide;
  FED.cell = key;
  const st = faceEditorOf(key);
  FED.dx = st.dx; FED.dy = st.dy; FED.scale = st.scale; FED.angle = st.angle;
  const pid = faceSlots()[key];
  FED.img = pid ? await getImg(facePhoto(pid).thumb) : null;
  // 모델이 예측을 기각한 자리는 cover-fit 이라 사람이 잡아 줘야 한다 — 그 사실을 밝힌다
  const how = c.from ? ` · ${posName((cellOf(c.from) || {}).pos)} 자리를 따라갑니다`
            : !pid ? " · 비어 있음"
            : faceFraming()[key] === "model" ? "" : " · 자동 프레이밍 없음";
  el("face-dock-title").firstChild.textContent = `${slideName(c)} · ${posName(c.pos)}${how}`;
  if(moved){ drawFaceBoard(); drawFaceSeg(); }
  else for(const b of el("face-board").querySelectorAll(".fcell"))
    b.classList.toggle("on", b.dataset.cell === key);
  setFaceKnobsEnabled(!c.from && !!pid);
  syncFaceKnobs(); renderFaceEditor();
}

function renderFaceEditor(){
  const cv = el("face-canvas"), c = cellOf(FED.cell); if(!cv || !c) return;
  // 자리 비율을 CSS 에 실어 둔다. object-fit 으로 끼워 맞추면 그려진 그림이
  // 요소 폭보다 좁아져서 드래그 환산(cv.width / cv.clientWidth)이 어긋난다.
  cv.style.aspectRatio = `${c.w}/${c.h}`;
  const {w, h, k} = faceFit(cv, c, FACE_EDIT_W);
  drawFaceComposite(cv.getContext("2d"), w, h, FED.img, FED, k, true);
}

/* 창 크기가 바뀌면 표시 크기도 바뀐다 — 캔버스 해상도를 다시 잡아 준다 */
let faceResizeTimer = null;
addEventListener("resize", () => {
  if(TAB !== "face" || !FED.cell) return;
  clearTimeout(faceResizeTimer);
  faceResizeTimer = setTimeout(() => {
    renderFaceEditor();
    for(const k of Object.keys(faceCanvas)) renderFaceCell(k);
  }, 150);
});

function syncFaceKnobs(){
  el("fed-angle").value = FED.angle.toFixed(1);
  el("fed-scale").value = Math.round(clamp(FED.scale, .5, 2) * 100);
  el("fed-tx").value = Math.round(clamp(FED.dx, -200, 200));
  el("fed-ty").value = Math.round(clamp(FED.dy, -200, 200));
  el("fv-angle").textContent = FED.angle.toFixed(1) + "°";
  el("fv-scale").textContent = Math.round(FED.scale * 100) + "%";
  el("fv-tx").textContent = Math.round(FED.dx);
  el("fv-ty").textContent = Math.round(FED.dy);
}

function afterFaceEdit(){
  if(REVIEW){
    (REVIEW.face_editors = REVIEW.face_editors || {})[FED.cell] =
      {dx: FED.dx, dy: FED.dy, scale: FED.scale, angle: FED.angle};
  }
  syncFaceKnobs(); renderFaceEditor(); renderFaceCell(FED.cell);
  saveFaceEdit();
}

function saveFaceEdit(){
  clearTimeout(FED.timer);
  el("face-saved").textContent = "…";
  FED.timer = setTimeout(async () => {
    try{
      const r = await api("/api/face/adjust", {method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({session_id: SESSION.session_id, cell: FED.cell,
                              dx: FED.dx, dy: FED.dy, scale: FED.scale, angle: FED.angle})});
      // 서버가 cover 조건으로 배율을 되돌릴 수 있다 — 창에 빈틈이 생기지 않게
      if(r.clamped_scale && Math.abs(r.clamped_scale - FED.scale) > 1e-6){
        FED.scale = r.clamped_scale;
        syncFaceKnobs(); renderFaceEditor(); renderFaceCell(FED.cell);
      }
      el("face-saved").textContent = "저장됨";
    }catch(e){ el("face-saved").textContent = "저장 실패"; }
  }, 200);
}

/* 촬영순 순서대로 자리를 다시 채운다(손으로 고친 것도 덮어쓴다) */
async function autoFace(){
  const btn = el("face-auto"); btn.disabled = true;
  try{
    const r = await api("/api/face/auto", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({session_id: SESSION.session_id})});
    REVIEW = r.review; STAGED = r.photos;
    drawFace();
    el("face-saved").textContent =
      `${r.placed}/${r.cells} 자리` + (r.placed ? ` · 자동 프레이밍 ${r.framed}장` : "");
  }catch(e){ el("face-saved").textContent = e.message; }
  finally{ btn.disabled = false; }
}

function bindFaceEditor(){
  const cv = el("face-canvas");
  const has = () => {
    const c = cellOf(FED.cell);
    return !!(c && !c.from && faceSlots()[FED.cell]);   // 파생 자리는 못 고친다
  };
  const knob = (id, set) => el(id).oninput = () => { if(has()){ set(); afterFaceEdit(); } };
  knob("fed-angle", () => FED.angle = +el("fed-angle").value);
  knob("fed-scale", () => FED.scale = +el("fed-scale").value / 100);
  knob("fed-tx",    () => FED.dx = +el("fed-tx").value);
  knob("fed-ty",    () => FED.dy = +el("fed-ty").value);
  el("fed-reset").onclick = () => {
    if(has()){ FED.dx = FED.dy = 0; FED.scale = 1; FED.angle = 0; afterFaceEdit(); }
  };
  el("face-auto").onclick = autoFace;
  cv.addEventListener("pointerdown", e => {
    if(!has()) return;
    FED.drag = true; FED.lx = e.offsetX; FED.ly = e.offsetY; cv.setPointerCapture(e.pointerId);
  });
  cv.addEventListener("pointermove", e => {
    if(!FED.drag) return;
    const c = cellOf(FED.cell); if(!c) return;
    // CSS픽셀 → 캔버스픽셀 → 백엔드픽셀 순으로 되돌린다
    const f = cv.width / cv.clientWidth, k = cv.width / (c.w * facePpc());
    FED.dx += (e.offsetX - FED.lx) * f / k;
    FED.dy += (e.offsetY - FED.ly) * f / k;
    FED.lx = e.offsetX; FED.ly = e.offsetY;
    syncFaceKnobs(); renderFaceEditor(); renderFaceCell(FED.cell);
  });
  cv.addEventListener("pointerup", () => { if(FED.drag){ FED.drag = false; afterFaceEdit(); } });
  cv.addEventListener("wheel", e => {
    if(!has()) return;
    e.preventDefault();
    FED.scale = clamp(FED.scale * (e.deltaY < 0 ? 1.03 : .97), .5, 2);
    afterFaceEdit();
  }, {passive:false});
}

function drawMirrorNote(){
  const slots = faceSlots(), mirrors = faceMirrors();
  const note = el("face-mirror");
  if(!mirrors.length){ note.innerHTML = ""; note.hidden = true; return; }
  note.hidden = false;
  const mp = facePhoto(slots[mirrors[0].cell]);
  const src = faceCells().find(c => c.cell === mirrors[0].from);
  const srcName = src ? `${slideName(src)} ${posName(src.pos)}` : mirrors[0].from;
  // 조사를 붙이지 않는다 — 라벨이 설정에서 오므로 받침을 알 수 없다
  note.innerHTML = (mp ? `<img src="${mp.thumb}" alt="">` : `<span class="sw"></span>`) +
    `<span>슬라이드 ${mirrors.map(m => m.slide).join("·")}` +
    `${mirrors[0].label ? ` (${esc(mirrors[0].label)})` : ""}` +
    ` — <b>${esc(srcName)}</b> 사진을 그대로 씁니다</span>`;
}

function drawPool(){
  const slots = faceSlots();
  const at = {};
  for(const c of faceCells()) if(slots[c.cell]) at[slots[c.cell]] = `${slideName(c)} ${posName(c.pos)}`;
  const ps = facePhotos(), pool = el("face-pool");
  pool.innerHTML = ps.length
    ? ps.map(p => {
        const used = at[p.id];
        return `<figure draggable="true" data-pid="${p.id}"${used ? ' class="used"' : ""}` +
               (used ? ` data-at="${esc(used)}"` : "") +
               ` title="${used ? esc(used) + "에 놓임 · 눌러서 옮기기" : "눌러서 켜진 자리에 넣기"}">` +
               `<img src="${p.thumb}" alt="" draggable="false"></figure>`;
      }).join("")
    : `<p class="empty">얼굴 상자가 비어 있습니다</p>`;
  const cells = faceCells();
  el("face-pool-n").textContent =
    ps.length ? `${cells.filter(c => slots[c.cell]).length}/${cells.length} 자리` : "";
  bindPoolDnD();
}

function drawFace(){
  if(!CASE || !CASE.enabled) return;
  ensureFocus();
  drawFaceSeg(); drawFaceBoard(); drawMirrorNote(); drawPool();
  // 환자정보 장이면 오른쪽을 칸 입력으로 바꾼다(사진 노브는 쓸 데가 없다)
  drawInfoDock();
  if(infoSlideOn()) return;
  if(FED.cell){ pickFace(FED.cell); return; }
  // 사진 자리가 없는 장(표지·계측선만 있는 장) — 편집기를 비워 둔다
  FED.img = null;
  el("face-dock-title").firstChild.textContent =
    `슬라이드 ${FSLIDE}${slideLabelOf(FSLIDE) ? " · " + slideLabelOf(FSLIDE) : ""}` +
    (linesOf(FSLIDE).length ? " · 사진 자리 없음(선만)" : " · 사진 자리 없음");
  setFaceKnobsEnabled(false);
  renderFaceEditor();
}

/* 파생 자리는 스스로 채우지 않으므로 되돌릴 실제 자리를 찾아 준다 */
function ownerCell(pid){
  const slots = faceSlots();
  return faceCells().map(c => c.cell).find(k => slots[k] === pid) || null;
}

function bindPoolDnD(){
  const pool = el("face-pool");
  pool.querySelectorAll("figure").forEach(f => {
    f.ondragstart = e => e.dataTransfer.setData("text/plain", f.dataset.pid);
    f.onclick = () => {
      const c = cellOf(FED.cell);
      if(c && !c.from) assignFace(FED.cell, f.dataset.pid);
    };
  });
  pool.ondragover  = e => { e.preventDefault(); pool.classList.add("over"); };
  pool.ondragleave = () => pool.classList.remove("over");
  pool.ondrop = e => {
    e.preventDefault(); pool.classList.remove("over");
    const pid = e.dataTransfer.getData("text/plain");
    const cell = pid && ownerCell(pid);
    if(cell) assignFace(cell, null);
  };
}

async function assignFace(cell, pid){
  const note = el("face-saved");
  note.textContent = "…";
  try{
    const r = await api("/api/face/assign", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({session_id: SESSION.session_id, cell, photo_id: pid})});
    if(REVIEW){
      REVIEW.face_slots = r.face_slots;
      REVIEW.face_editors = r.face_editors;
      REVIEW.face_framing = r.face_framing;
    }
    note.textContent = "저장됨";
    drawFace();
  }catch(e){ note.textContent = e.message; }
}

/* ══ 차수 노트 ═══════════════════════════════════════════════════════════════
   슬라이드의 텍스트 박스를 직접 타이핑하지 않는다. 칸을 채우면 서버가 서식에
   끼워 박스 텍스트를 만든다 — 차수마다 같은 모양으로 남게 하려는 것이다.
   비워 둔 칸이 만든 줄은 서버가 통째로 버린다("U: " 같은 껍데기를 안 남긴다). */
let NOTES = null;
const NOTE_DIRTY = new Set();   // 사람이 타이핑한 칸 — 저장은 이 칸들만 보낸다
const NOTE_LABEL = {NOTE_DATE:"좌상단 · 날짜/차수", NOTE_STATUS:"우상단 · 와이어/기간",
                    NOTE_SOAP:"좌상단 · s) p)", NOTE_LL:"좌하단 · 메모",
                    NOTE_NEXT:"우하단 · n) 다음"};

/* ── 줄 끝 날짜는 작게 ────────────────────────────────────────────────────────
   양식은 본문(예: 우상단 Tx./Rx.)이 15pt, 줄 끝 괄호에 붙는 날짜가 9pt 다.
   슬라이드에 적을 때도 같은 규칙으로 갈라 쓰므로(backend/case_deck.set_note_text),
   화면도 그대로 갈라 그려야 "보이는 대로 나온다"가 성립한다.
   판정 규칙은 서버의 _TAIL_PAREN 과 같다 — 본문이 있는 줄의 끝 괄호만, 괄호만
   있는 줄은 통째로 본문 크기.

   날짜 칸의 "(초진 A)" 는 날짜가 아니라 차수 표시라 줄이지 않는다 — 어느 박스가
   예외인지는 서버(notes.date_pt_except)가 정한다. */
const TAIL_PAREN = /\s*\([^()]*\)\s*$/;
function noteHtml(text, key){
  const skip = ((NOTES && NOTES.date_pt_except) || []).includes(key);
  const small = NOTES && NOTES.date_pt && !skip;
  return String(text == null ? "" : text).split("\n").map(ln => {
    if(small){
      const m = ln.match(TAIL_PAREN);
      if(m && ln.slice(0, m.index).trim())
        return esc(ln.slice(0, m.index)) + `<span class="sm">${esc(m[0])}</span>`;
    }
    return esc(ln);
  }).join("\n");
}

/* ── 십자뷰 판 위의 노트 오버레이 ────────────────────────────────────────────
   양식(template.pptx)의 텍스트박스 자리를 그대로 판 위에 얹어, 슬라이드에서
   보일 모습 그대로 고칠 수 있게 한다. 판은 슬라이드와 같은 4:3이고 자리는
   %로 환산되므로 판 크기가 바뀌어도 따라간다.

   값은 서식(notes.boxes)이 자동으로 채운다 — 경과 개월·초진 날짜까지. 여기서
   고치면 그 박스만 '통째로 고쳐 쓴 것'으로 서버에 남고 서식을 이긴다. */
function drawNoteOverlay(){
  if(!boardEl) return;
  let layer = boardEl.querySelector(".noteov");
  if(!NOTES || !NOTES.layout || !NOTES.slide || !NOTES.slide.w){
    if(layer) layer.remove();
    return;
  }
  const sl = NOTES.slide;
  if(!layer){ layer = document.createElement("div"); layer.className = "noteov"; boardEl.appendChild(layer); }
  layer.innerHTML = "";
  for(const key of noteOrder()){
    const r = NOTES.layout[key]; if(!r) continue;
    const box = document.createElement("div");
    box.className = "nbox" + ((NOTES.overrides || {})[key] ? " edited" : "");
    box.style.left   = `${r.x / sl.w * 100}%`;
    box.style.top    = `${r.y / sl.h * 100}%`;
    box.style.width  = `${r.w / sl.w * 100}%`;
    box.style.height = `${r.h / sl.h * 100}%`;
    const ta = document.createElement("textarea");
    ta.value = (NOTES.preview || {})[key] || "";
    ta.spellcheck = false;
    ta.title = NOTE_LABEL[key] || key;
    // 글꼴을 양식에서 읽은 값으로 맞춘다. pt는 슬라이드 폭 기준이므로 판 크기에
    // 비례해야 하는데, 여기서 px로 굳히면 안 된다 — 판이 숨겨져 있을 때(탭 전환
    // 전) clientWidth 가 0이라 엉뚱한 크기로 박히고, 다시 그릴 때마다 값이 널뛴다.
    // 크기는 CSS(100cqw 기준)에 맡기고 여기서는 pt 숫자만 넘긴다.
    const f = r.font || {};
    if(f.size_pt) box.style.setProperty("--nt-pt", f.size_pt);
    if(NOTES.date_pt) box.style.setProperty("--nt-sm", NOTES.date_pt);
    if(f.color) box.style.setProperty("--nt-ink", `#${f.color}`);
    if(f.bold) box.style.setProperty("--nt-w", "700");
    // 한 상자 안에서 15pt 본문과 9pt 날짜를 함께 보이려면 textarea 로는 안 된다
    // (글자 크기가 상자마다 하나뿐이다). 그래서 같은 자리에 그린 미리보기를
    // 겹쳐 두고, 고치는 동안(포커스)에만 textarea 를 드러낸다.
    const pv = document.createElement("div");
    pv.className = "nprev";
    pv.innerHTML = noteHtml(ta.value, key);
    ta.oninput = () => {
      box.classList.add("edited");
      pv.innerHTML = noteHtml(ta.value, key);
      queueNoteBox(key, ta.value);
    };
    ta.onfocus = () => { box.classList.add("editing"); showNoteDock(key); };
    ta.onblur = () => box.classList.remove("editing");
    box.appendChild(pv);
    box.appendChild(ta);
    // 번호표 — 사람이 "3번 박스"라고 부를 수 있게. 순서는 서버가 정한다.
    const badge = document.createElement("span");
    badge.className = "nnum"; badge.textContent = noteNo(key);
    badge.title = NOTE_LABEL[key] || key;
    badge.onclick = () => showNoteDock(key);
    box.appendChild(badge);
    // 서식으로 되돌리기 — 자동 계산값을 다시 쓰고 싶을 때
    const rst = document.createElement("button");
    rst.type = "button"; rst.className = "nrst"; rst.textContent = "↺";
    rst.title = "서식(자동 계산)으로 되돌리기";
    rst.onclick = async () => { await saveNoteBoxes({[key]: ""}); drawNoteOverlay(); };
    box.appendChild(rst);
    layer.appendChild(box);
  }
}

const noteOrder = () => (NOTES && NOTES.order) || [];
const noteNo = key => noteOrder().indexOf(key) + 1;

/* dock 을 사진 편집기 ↔ 노트 편집기로 바꾼다.
   노트 박스를 만지는 동안 오른쪽에 사진 노브가 떠 있어 봐야 쓸 데가 없다.

   ★ 예전에는 여기서 서식({칸이름} 이 든 문자열)을 직접 고치게 했는데, 그 문법을
   읽어야 값을 넣을 수 있어서 쓰기가 어려웠다. 지금은 **그 박스를 채우는 칸만**
   보통 입력칸으로 띄운다 — 서식은 config 가 들고 있고 사람은 값만 채운다. */
let NOTE_FOCUS = null;

function showNoteDock(key){
  const photo = el("dock-photo"), note = el("dock-note");
  if(!photo || !note) return;
  NOTE_FOCUS = key;
  photo.hidden = true; note.hidden = false;
  el("dock-title").firstChild.textContent = `${noteNo(key)}. ${NOTE_LABEL[key] || key}`;
  drawNoteDock(key);
}

function showPhotoDock(){
  const photo = el("dock-photo"), note = el("dock-note");
  if(!photo || !note) return;
  NOTE_FOCUS = null;
  photo.hidden = false; note.hidden = true;
}

/* 고른 박스를 채우는 칸만 세로로 늘어놓는다. 칸을 고치면 그 박스의 글이 서식에
   따라 다시 만들어지고, 판 위 글자도 곧바로 바뀐다. */
function drawNoteDock(key){
  const box = el("nt-fields"), sub = el("nt-sub");
  if(!box || !NOTES) return;
  const keys = ((NOTES.box_fields || {})[key]) || [];
  const byKey = k => (NOTES.fields || []).find(f => f.key === k);
  const overridden = !!(NOTES.overrides || {})[key];

  sub.textContent = keys.length
    ? "이 칸들이 왼쪽 박스를 채웁니다"
    : "이 박스는 채울 칸이 없습니다 — 판 위에서 직접 고쳐 쓰세요";
  box.innerHTML = "";
  for(const k of keys){
    const f = byKey(k); if(!f) continue;
    // 병원번호 칸은 폴더명에서 못 읽은 환자에게만 보인다 — 있는 값을 또 묻지 않는다
    if(f.key === "hospital_id" && SESSION && SESSION.ids && SESSION.ids.hospital_id)
      continue;
    box.appendChild(noteFieldRow(f));
  }
  // 직접 고쳐 쓴 박스는 칸을 바꿔도 글이 안 바뀐다 — 그 사실을 알리고 되돌릴 길을 준다
  const rst = el("nt-reset");
  rst.hidden = !overridden;
  rst.onclick = async () => {
    await saveNotes({boxes: {[key]: ""}});
    drawNoteOverlay(); drawNoteDock(key);
  };
  if(overridden)
    sub.textContent = "이 박스는 판 위에서 직접 고쳐 썼습니다 — 칸을 바꿔도 그대로입니다";
}

/* 칸 하나. 노트 dock 과 환자정보 dock 이 같은 모양을 쓴다. */
function noteFieldRow(f){
  const lab = document.createElement("label");
  lab.textContent = f.label;
  const v = (NOTES.values || {})[f.key] || "";
  const input = (f.lines || 1) > 1
    ? document.createElement("textarea") : document.createElement("input");
  if((f.lines || 1) > 1) input.rows = f.lines; else input.type = "text";
  input.value = v;
  input.placeholder = f.hint || "";
  input.spellcheck = false;
  // 사람이 실제로 타이핑한 칸만 표시한다 — 저장할 때 이 칸들만 보낸다.
  // 예전에는 모든 칸을 통째로 보냈고, 서버가 전부를 "사용자 수정"으로 기록해
  // 기본값이 얼어붙었다: 기준일을 바꿔도 이미 굳은 옛 값이 이겨서 노트가 안 변했다.
  input.oninput = () => {
    NOTE_DIRTY.add(f.key);
    NOTES.values[f.key] = input.value; queueNoteValues();
  };
  lab.appendChild(input);

  // 기간 칸(Tx/Rx/App)에는 기준일 선택과 "이전 값 그대로"를 붙인다.
  // 실측 기록을 보면 기준일은 여러 번 바뀌고(재진단·단계 전환), 어떤 기간은
  // 여러 차수에 걸쳐 값이 멈춰 있다(장치 제거 후). 둘 다 사람이 정한다.
  const PERIOD = {tx_period: "tx", rx_period: "rx", app_period: "app"};
  const pk = PERIOD[f.key];
  if(pk){
    const st = (NOTES.periods || {})[pk] || {dates: [], today: ""};
    // 상태는 넷뿐이다 — 자동(기존 기준일) · 이번 차수부터 · 이전 값 그대로 · 안 씀.
    // 날짜 체크박스 나열은 상태가 몇 개인지 안 보여서 세그먼트 버튼으로 바꿨다.
    const mode = st.keep ? "keep"
               : st.start === "none" ? "none"
               : (st.start && st.start === st.today) ? "reset" : "auto";
    const wrap = document.createElement("div");
    wrap.className = "hint pseg-wrap";

    // 저장 뒤 노트를 다시 받아 판 위 글과 dock 을 새로 그린다
    const send = (patch) => api("/api/notes", {method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({session_id: SESSION.session_id,
                              period: {[pk]: patch}})})
      .then(async () => {
        await loadNotes();
        if(NOTE_FOCUS) drawNoteDock(NOTE_FOCUS);
      }).catch(() => {});

    const seg = document.createElement("div");
    seg.className = "seg pseg";
    const MODES = [
      ["auto", "자동", "기존 기준일에서 개월이 계속 늘어납니다",
       () => send({start: "", keep: false})],
      ["reset", "이번 차수부터", "기준일을 이번 촬영일로 새로 시작합니다",
       () => send({start: st.today, keep: false})],
      ["keep", "이전 값 그대로", "개월을 다시 세지 않고 직전 차수 줄을 그대로 씁니다",
       () => send({keep: true})],
      ["none", "안 씀", "날짜 없이 기간만 적습니다",
       () => send({start: "none", keep: false})],
    ];
    for(const [m, nm, tip, act] of MODES){
      if(m === "keep" && !st.last) continue;    // 직전 값이 없으면 의미가 없다
      if(m === "reset" && !st.today) continue;
      const b = document.createElement("button");
      b.type = "button"; b.textContent = nm; b.title = tip;
      b.setAttribute("aria-pressed", m === mode);
      b.onclick = act;
      seg.appendChild(b);
    }
    wrap.appendChild(seg);

    // 기준일 이력이 여럿인 드문 경우만 — 자동 모드에서 과거 기준일을 고른다
    if(mode === "auto" && (st.dates || []).length > 1){
      const sel = document.createElement("select");
      sel.className = "ovsel";
      const cur = st.start || st.dates[0];
      sel.innerHTML = st.dates.map(d =>
        `<option value="${d}"${d === cur ? " selected" : ""}>기준일 ${d}</option>`).join("");
      sel.onchange = () => send({start: sel.value, keep: false});
      wrap.appendChild(sel);
    }
    lab.appendChild(wrap);
  }
  return lab;
}

let noteBoxTimer = null, noteBoxPending = {};
function queueNoteBox(key, val){
  noteBoxPending[key] = val;
  clearTimeout(noteBoxTimer);
  // 타이핑 중에 오버레이를 다시 그리면 커서가 튄다 — 저장만 하고 미리보기만 갱신
  noteBoxTimer = setTimeout(async () => {
    const boxes = noteBoxPending; noteBoxPending = {};
    await saveNoteBoxes(boxes);
  }, 400);
}

/* 서버로 보낸다. payload 를 안 주면 칸 값 전체를 보낸다.

   ★ 여기는 반드시 **한 개**여야 한다. 예전에는 같은 이름의 함수가 아래쪽에 하나
   더 있어서 나중 선언이 이걸 통째로 덮었고, 그쪽은 인자를 무시하고 values 만
   보냈다 — 판 위 오버레이에서 고쳐 쓴 박스(boxes)가 서버에 닿지도 못하고
   버려져, 확정해도 PPT 에 남지 않았다. */
async function saveNotes(payload){
  if(!SESSION) return;
  const marks = [el("ed-saved"), el("face-saved")].filter(Boolean);
  for(const m of marks) m.textContent = "…";
  try{
    NOTES = await api("/api/notes", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({session_id: SESSION.session_id,
                            // 타이핑한 칸만 — 전체를 보내면 서버가 모든 칸을
                            // "사용자 수정"으로 얼려 자동 계산이 다시는 안 바뀐다
                            ...(payload || {values: Object.fromEntries(
                              [...NOTE_DIRTY].map(k =>
                                [k, (NOTES && NOTES.values[k]) || ""]))})})});
    for(const m of marks) m.textContent = "저장됨";
  }catch(e){
    for(const m of marks) m.textContent = "저장 실패";
  }
}

const saveNoteBoxes = boxes => saveNotes({boxes});

async function loadNotes(){
  if(!SESSION) return;
  try{
    NOTES = await api(`/api/notes/${SESSION.session_id}`);
    drawNoteOverlay();
    drawInfoDock();               // 환자정보 장을 보고 있으면 칸도 채워 준다
  }catch(e){ /* 판 위 오버레이가 안 뜰 뿐, 다른 작업은 계속할 수 있다 */ }
}

/* 칸을 타이핑하는 동안 매 글자마다 보내지 않는다. 저장이 끝나면 판 위
   오버레이도 다시 그린다 — 서식이 만든 글이 곧바로 보여야 한다. */
let noteValTimer = null;
function queueNoteValues(){
  clearTimeout(noteValTimer);
  noteValTimer = setTimeout(async () => {
    await saveNotes();
    drawNoteOverlay();
    drawInfoBoxes();              // 환자정보 장의 미리보기도 같이 따라간다
  }, 250);
}

/* 검수·조정 화면의 탭. IntraOral은 십자뷰 편집, FACE는 케이스 슬라이드 배치.
   FACE는 케이스 덱을 만드는 초진에서만 열린다 — 재진은 십자뷰만 이어붙인다. */
let TAB = "io";
function faceTabOpen(){
  return !!(CASE && CASE.enabled && SESSION && SESSION.mode === "first");
}
function showTab(name){
  if(name === "face" && !faceTabOpen()) name = "io";
  TAB = name;
  document.querySelectorAll("#proc-tabs button").forEach(b =>
    b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  document.querySelectorAll('.view[data-view="proc"] .pane').forEach(p =>
    p.classList.toggle("on", p.dataset.pane === name));
  // 헤더 선택기: 슬롯은 십자뷰, 슬라이드는 FACE에서만 쓴다
  segEl.hidden = name !== "io";
  const fseg = el("face-seg"); if(fseg) fseg.hidden = name !== "face";
  if(name === "io" && ED.slot) renderEditor();
  if(name === "face") drawFace();
  // 판이 다시 보이면 노트 오버레이도 다시 얹는다 — 탭을 오가도 사라지지 않게.
  if(name === "io") drawNoteOverlay();
}
function syncTabs(){
  const gate = (name, open, tip) => {
    const b = document.querySelector(`#proc-tabs button[data-tab="${name}"]`);
    if(b){ b.disabled = !open; b.title = tip; }
  };
  gate("face", faceTabOpen(),
       faceTabOpen() ? "케이스 슬라이드에 얼굴 사진 배치"
                     : "초진에서 케이스 덱을 만들 때만 쓸 수 있습니다");
  // 노트는 세션이 열리자마자 받아 둔다 — 자동분류를 마치고 검수·조정에 들어온
  // 순간부터 십자뷰 판 위에 텍스트 박스가 얹혀 있어야 하기 때문이다.
  if(SESSION && !NOTES) loadNotes();
  showTab(TAB);   // 못 여는 탭이면 showTab이 알아서 io로 돌린다
}

document.querySelectorAll("#proc-tabs button").forEach(b =>
  b.onclick = () => showTab(b.dataset.tab));

function showView(v){
  VIEW = v;
  document.querySelectorAll(".view").forEach(x => x.classList.toggle("on", x.dataset.view === v));
  document.querySelectorAll(".nav").forEach(n =>
    n.dataset.view === v ? n.setAttribute("aria-current","page") : n.removeAttribute("aria-current"));
  renderVisitBadges();
}

function setTheme(t){
  document.documentElement.dataset.theme = t;
  try{ localStorage.setItem("crocs-theme", t); }catch(e){}
  syncThemeSeg();
}

addEventListener("keydown", e => {
  if(e.target.tagName === "INPUT") return;
  const s = SLOTS.find(x => x.hk === +e.key);
  if(s && s.cls){ showView("proc"); pick(s.key); }
});

el("btn-new-pt").onclick = openNewDialog;
el("new-cancel").onclick = () => dlg().close();
el("hist-close").onclick = () => el("dlg-hist").close();
el("new-ok").onclick = () => openSession(newIds(), "new-err");
for(const id of ["f-name","f-hosp","f-ortho"]){
  el(id).oninput = syncPreview;
  el(id).onkeydown = e => { if(e.key === "Enter"){ e.preventDefault(); el("new-ok").click(); } };
}
el("btn-root").onclick = openSettings;
el("btn-set").onclick = openSettings;
// 피드백 — 구글 폼을 새 탭으로. 앱 상태와 무관하니 언제든 눌러도 안전하다.
el("btn-fb").onclick = () =>
  window.open("https://forms.gle/k8MRUas5LwGAxFnB9", "_blank", "noopener");

/* ── 폴더 이름 형식 — 블록 조립 ──────────────────────────────────────────────
   [이름]·[병록번호]·[교정번호] 필드 블록과 구분자(-, _, .) 블록을 ‹ › 로 움직여
   형식을 만든다. 아래에 실제 폴더들이 이 형식으로 읽히는지 실시간으로 보인다. */
const PAT = {toks: [], saved: [], timer: null, kind: "folder", def: ""};
const patKey = () => PAT.kind === "ppt" ? "ppt_patterns"
                   : PAT.kind === "label" ? "label_patterns" : "folder_patterns";
const patNoun = () => PAT.kind === "ppt" ? "PPT"
                    : PAT.kind === "label" ? "라벨" : "폴더";

const patRecog = (pat) => /\{(any|all|[dc]\d+-\d+)\}/.test(pat);  // 인식 전용 형식인가
const RECOG_LABEL = v => v === "any" ? "*아무거나"
  : v === "all" ? "**전부(구분자 포함)"
  : (v[0] === "d" ? "숫자" : "문자") + v.slice(1).replace("-", "~");
const patExample = (pat) => pat
  .replace(/\{any\}/g, "…").replace(/\{all\}/g, "…").replace(/\{d\d+-\d+\}/g, "12")
  .replace(/\{c\d+-\d+\}/g, "ab")
  .replace("{date}", "26.08.12").replace("{vkind}", "초진")
  .replace("{visit}", "A")
  .replace("{name}", "홍길동")
  .replace("{hospital_id}", "1".repeat(RULES.hospital_digits || 9))
  .replace("{ortho_id}", "2".repeat(RULES.ortho_digits || 5));

async function patPost(list){
  el("pat-msg").textContent = "…";
  try{
    const p = await api("/api/prefs", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({[patKey()]: list})});
    PAT.saved = p[patKey()] || [];
    el("pat-msg").textContent = "저장됐습니다";
    patRenderSaved(); patCheck();
    if(typeof loadPatients === "function") loadPatients();
  }catch(e){ el("pat-msg").textContent = e.message; }
}

function patRenderSaved(){
  const box = el("pat-saved"); if(!box) return;
  box.innerHTML = "";
  // 등록한 목록이 전부다 — 기본 형식은 목록이 비어 있을 때만 안전망으로
  // 나타난다(— 기본 표시). 형식을 하나라도 등록하면 기본은 어디에도 안 낀다.
  const def = PAT.def || "{name}_{hospital_id}_{ortho_id}";
  const implicit = !PAT.saved.length;
  const rows = implicit ? [def] : PAT.saved.slice();
  rows.forEach((pat, i) => {
    const r = document.createElement("div");
    const star = document.createElement("span");
    star.textContent = i === 0 ? "★" : "";
    const ex = document.createElement("span");
    ex.className = "ex";
    ex.textContent = patExample(pat) + (implicit ? " — 기본" : "");
    ex.title = pat;
    const up = document.createElement("button");
    up.textContent = "▲";
    if(patRecog(pat)){
      up.disabled = true;
      up.title = `인식 전용 형식 — 새 ${patNoun()} 생성에는 못 씁니다`;
    }else{
      up.title = `이 형식으로 새 ${patNoun()} 생성`;
      up.onclick = () => patPost([pat, ...PAT.saved.filter(p => p !== pat)]);
    }
    const bx = document.createElement("button");
    bx.textContent = "×";
    bx.onclick = () => patPost(PAT.saved.filter(p => p !== pat));
    r.append(star, ex, up, bx);
    box.appendChild(r);
  });
}
const PAT_FIELDS = {name: "이름", hospital_id: "병원번호", ortho_id: "교정번호"};
const LABEL_FIELDS = {date: "날짜", vkind: "초진/재진", visit: "차수글자"};
const patFields = () => PAT.kind === "label" ? LABEL_FIELDS : PAT_FIELDS;

function patTokens(pattern){
  const out = [];
  const re = /\{(name|hospital_id|ortho_id|date|vkind|visit)\}|\{(any|all|[dc]\d+-\d+)\}|([^{]+)/g;
  let m;
  while((m = re.exec(pattern))){
    if(m[1]) out.push({t: "f", k: m[1]});
    else if(m[2]) out.push({t: "r", v: m[2]});   // 인식 전용
    else out.push({t: "s", v: m[3]});
  }
  return out;
}
const patString = () =>
  PAT.toks.map(t => t.t === "f" || t.t === "r" ? `{${t.k || t.v}}` : t.v).join("");

function patRender(){
  const row = el("pat-row"); if(!row) return;
  row.innerHTML = "";
  PAT.toks.forEach((t, i) => {
    const c = document.createElement("span");
    c.className = "patchip" + (t.t === "s" ? " sep" : "");
    const label = t.t === "f"
      ? (patFields()[t.k] || LABEL_FIELDS[t.k] || PAT_FIELDS[t.k])
        + (t.k === "hospital_id" ? `(${RULES.hospital_digits||9})`
           : t.k === "ortho_id" ? `(${RULES.ortho_digits||5})` : "")
      : t.t === "r" ? RECOG_LABEL(t.v)
      : t.v === " " ? "␣" : t.v;
    const mv = (d) => { const j = i + d;
      if(j < 0 || j >= PAT.toks.length) return;
      [PAT.toks[i], PAT.toks[j]] = [PAT.toks[j], PAT.toks[i]];
      patRender(); };
    const bl = document.createElement("button"); bl.textContent = "‹";
    bl.onclick = () => mv(-1);
    const br = document.createElement("button"); br.textContent = "›";
    br.onclick = () => mv(1);
    const bx = document.createElement("button"); bx.textContent = "×";
    bx.onclick = () => { PAT.toks.splice(i, 1); patRender(); };
    c.append(bl, document.createTextNode(label), br, bx);
    row.appendChild(c);
  });
  const ex = el("pat-ex");
  const tail = PAT.kind === "ppt" ? ".pptx" : "";   // 확장자는 저장할 때 붙는다
  ex.textContent = (patString() ? patExample(patString()) + tail : "—");
  clearTimeout(PAT.timer);
  PAT.timer = setTimeout(patCheck, 300);
}

async function patCheck(){
  const box = el("pat-list"); if(!box) return;
  // 폴더 인식 미리보기는 폴더 형식에서만 의미가 있다
  if(PAT.kind !== "folder"){ box.hidden = true; return; }
  box.hidden = false;
  try{
    const d = await api("/api/pattern_check?pattern=" +
                        encodeURIComponent(patString()));
    box.innerHTML = "";
    for(const it of d.items){
      const r = document.createElement("div");
      const mark = it.match ? ["✓", "ok"] : it.fallback ? ["↩", "old"] : ["✗", "no"];
      r.innerHTML = `<span class="${mark[1]}">${mark[0]}</span>`;
      r.appendChild(document.createTextNode(it.name));
      box.appendChild(r);
    }
    if(!d.items.length) box.textContent = "폴더가 아직 없습니다";
  }catch(e){ box.textContent = "목록을 읽지 못했습니다"; }
}

async function patLoad(){
  try{
    const p = await api("/api/prefs");
    PAT.saved = p[patKey()] || [];
    PAT.def = (PAT.kind === "ppt" ? p.ppt_pattern_default
               : PAT.kind === "label" ? p.label_pattern_default
               : p.folder_pattern_default)
              || "{name}_{hospital_id}_{ortho_id}";
    PAT.toks = patTokens((PAT.saved[0] || PAT.def).replace(/\.pptx$/i, ""));
  }catch(e){ PAT.saved = []; PAT.toks = patTokens("{name}_{hospital_id}_{ortho_id}"); }
  const kb = el("pat-kind");
  if(kb) for(const b of kb.children){
    b.setAttribute("aria-pressed", b.dataset.k === PAT.kind);
    b.onclick = () => { PAT.kind = b.dataset.k; patLoad(); };
  }
  const desc = el("pat-desc");
  if(desc) desc.textContent = PAT.kind === "ppt"
    ? "등록된 형식 — 전부 인식에 쓰이고, ★ 첫 번째로 새 PPT를 만듭니다"
    : PAT.kind === "label"
    ? "등록된 형식 — 전부 인식에 쓰이고, ★ 첫 번째 형식으로 라벨을 씁니다"
    : "등록된 형식 — 전부 인식에 쓰이고, ★ 첫 번째로 새 폴더를 만듭니다";
  patRenderSaved();
  const pal = el("pat-pal");
  if(pal){
    pal.innerHTML = "";   // 탭(폴더/PPT/날짜차수)마다 블록 구성이 다르다
    for(const [k, nm] of Object.entries(patFields())){
      const b = document.createElement("button");
      b.textContent = "+ " + nm;
      b.onclick = () => { if(!PAT.toks.some(t => t.t === "f" && t.k === k))
        PAT.toks.push({t: "f", k}); patRender(); };
      pal.appendChild(b);
    }
    // 날짜/차수 라벨은 "YY.MM.DD (초진 A)" 꼴만 조립하면 된다 — 블록을 최소로.
    const seps = PAT.kind === "label" ? [" ", "(", ")"]
                                      : ["-", "_", ".", " ", "(", ")"];
    for(const v of seps){
      const b = document.createElement("button");
      b.textContent = v === " " ? "␣ 공백" : v;
      b.onclick = () => { PAT.toks.push({t: "s", v}); patRender(); };
      pal.appendChild(b);
    }
    // 직접 입력 블록 — 모든 이름에 그대로 들어가는 글자라 생성(★)에도 쓴다.
    const bt = document.createElement("button");
    bt.textContent = "+ 글자 입력";
    bt.onclick = () => {
      const v = (prompt("이름에 넣을 글자 (예: 교정, -final)") || "").trim();
      if(!v) return;
      if(/[{}\\/:*?"<>|]/.test(v)){
        el("pat-msg").textContent = '\\ / : * ? " < > | { } 는 글자에 못 씁니다';
        return;
      }
      PAT.toks.push({t: "s", v}); patRender();
    };
    pal.appendChild(bt);
    // 인식 전용 블록 — 손으로 만든 옛 폴더를 읽을 때만 쓰인다 (생성용 불가).
    // 날짜/차수 탭에는 안 낸다 — 라벨 양식은 세 블록 + 공백·괄호면 충분하다.
    const addR = (v) => { PAT.toks.push({t: "r", v}); patRender(); };
    if(PAT.kind !== "label"){
    const br1 = document.createElement("button");
    br1.textContent = "* 이후 아무거나"; br1.onclick = () => addR("any");
    pal.appendChild(br1);
    const br2 = document.createElement("button");
    br2.textContent = "** 전부(구분자 포함)"; br2.onclick = () => addR("all");
    pal.appendChild(br2);
    for(const [c, nm] of [["d", "숫자"], ["c", "문자"]]){
      const b = document.createElement("button");
      b.textContent = `+ ${nm} n~m자리`;
      b.onclick = () => {
        const r = (prompt(`${nm} 자릿수 범위 (예: 1-3)`, "1-3") || "").trim();
        if(/^\d+-\d+$/.test(r)) addR(c + r);
      };
      pal.appendChild(b);
    }
    }
    // 조립한 형식을 목록 맨 앞에 추가 — 곧바로 새 폴더 생성용(★)이 된다
    el("pat-save").onclick = () => {
      const pat = patString(), rest = PAT.saved.filter(p => p !== pat);
      patPost(patRecog(pat) ? [...rest, pat] : [pat, ...rest]);
    };
    el("pat-reset").onclick = () => patPost([]);
  }
  patRender();
}
el("btn-set").addEventListener("click", patLoad);

/* ── 업데이트 로그 · F&Q ─────────────────────────────────────────────────────
   내용은 정적 JSON([{title, date?, body}])이라 항목 추가에 코드 수정이 없다.
   토글은 <details> — 접기/펼치기·키보드 접근이 공짜다. */
const DOCS = {
  log: {title: "업데이트 로그", src: "/static/changelog.json"},
  faq: {title: "F&Q", src: "/static/faq.json"},
};

async function openDoc(kind){
  const d = DOCS[kind];
  el("doc-title").textContent = d.title;
  const box = el("doc-list");
  box.textContent = "불러오는 중…";
  el("dlg-doc").showModal();
  let items;
  try{
    items = await (await fetch(`${d.src}?v=${Date.now()}`)).json();
  }catch(e){ box.textContent = "내용을 불러오지 못했습니다"; return; }
  // 최신순 — 날짜가 있으면 내림차순(YY.MM.DD 라 문자열 비교로 충분). 날짜가 없는
  // 항목(F&Q)은 파일에 적힌 순서 그대로다(sort 는 안정 정렬) — 새 항목을 위에 적으면 된다.
  items.sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  box.innerHTML = "";
  for(const it of items){
    const dt = document.createElement("details");
    const sm = document.createElement("summary");
    sm.textContent = it.title || "";
    if(it.date){
      const w = document.createElement("span");
      w.className = "when"; w.textContent = it.date;
      sm.appendChild(w);
    }
    const bd = document.createElement("div");
    bd.className = "body"; bd.textContent = it.body || "";
    dt.append(sm, bd);
    box.appendChild(dt);
  }
  if(!items.length) box.textContent = "아직 항목이 없습니다";
}

el("btn-log").onclick = () => openDoc("log");
el("btn-faq").onclick = () => openDoc("faq");
el("doc-close").onclick = () => el("dlg-doc").close();

// ── 알림 배너 ───────────────────────────────────────────────────────────────
// 가중치가 없으면 무엇을 어디서 받는지, 새 버전이 있으면 무엇이 바뀌는지 알린다.
// 모달로 막지 않는다 — 가중치 없이도 초진 작업은 되고, 업데이트는 급하지 않다.
function banner(kind, html){
  const b = el("banner");
  b.className = "banner " + kind;
  b.innerHTML = html;
  b.hidden = false;
  // 이전 배너가 클릭 동작(업데이트)을 달아뒀을 수 있다 — 내용이 바뀌면 무효다.
  b.onclick = null; b.title = ""; b.style.cursor = "";
}

async function checkWeights(){
  const w = await api("/api/weights").catch(() => null);
  if(!w || w.ready) return;
  const bad = w.items.filter(i => i.state !== "ok");
  const rows = bad.map(i => `<li>${i.key} — ${i.detail || i.state}</li>`)
    .concat(w.strays.map(s => `<li>${s.name} — ${s.why}</li>`)).join("");
  banner("warn",
    `<b>모델 파일이 준비되지 않았습니다</b>
     <span class="grow">받은 파일을 <code>${w.drop_dir}</code> 에 넣고 다시 시작하세요.</span>
     <ul class="lst">${rows}</ul>`);
}

/* 확인이 실패하면 **사유를 보여준다.** 예전에는 조용히 돌아섰다 — 사용자 눈에는
   '최신입니다'와 똑같아서, 업데이트 통로가 끊긴 걸 아무도 몰랐다. */
async function checkUpdate(){
  const u = await api("/api/update/check").catch(() => null);
  if(!u || !u.ok){
    const why = (u && u.reason) || "서버가 응답하지 않습니다";
    if(!/개발용 설치본/.test(why))
      banner("warn", `<b>업데이트를 확인하지 못했습니다</b>
                      <span class="grow">${why}</span>`);
    return;
  }
  if(!u.has_update) return;
  const ver = u.app_to && u.app_to !== u.app_from
    ? `v${u.app_from} → <b>v${u.app_to}</b>` : `${u.behind}개 변경`;
  const wt = u.weights_changed.length
    ? `<br>모델도 갱신됩니다: ${u.weights_changed.join(", ")} — 업데이트 후 새로 받아야 합니다.` : "";
  const blocked = u.blocked ? `<span class="grow">${u.blocked}</span>` : "";
  banner("info",
    `<b>새 버전이 있습니다</b> <span>${ver}</span>
     ${blocked || '<span class="grow"></span>'}
     ${u.blocked ? (/직접 수정/.test(u.blocked)
        ? '<button class="btn" id="btn-upd-force">백업 후 업데이트</button>' : "")
        : '<button class="btn" id="btn-upd">업데이트</button>'}
     <ul class="lst">${u.log.slice(0,5).map(l => `<li>${l}</li>`).join("")}${wt}</ul>`);
  const b = el("btn-upd");
  if(b) b.onclick = () => doUpdate(false);
  const bf = el("btn-upd-force");
  if(bf) bf.onclick = () => {
    if(confirm("직접 수정한 파일을 백업 폴더로 옮기고 원본으로 되돌린 뒤 " +
               "업데이트합니다. 계속할까요?")) doUpdate(true);
  };
  // 배너 자체도 누르면 업데이트 — 버튼이 작아서 지나치기 쉽다. 차단 사유가
  // 있을 때는 달지 않는다 (누를 수 있는 것처럼 보이면 안 된다).
  if(!u.blocked){
    const bn = el("banner");
    bn.style.cursor = "pointer";
    bn.title = "눌러서 업데이트";
    bn.onclick = doUpdate;
  }
}

async function doUpdate(force){
  if(doUpdate.busy) return;      // 배너와 버튼 양쪽에 달려 있다 — 중복 실행 방지
  doUpdate.busy = true;
  const b = el("btn-upd") || el("btn-upd-force");
  if(b){ b.disabled = true; b.textContent = "받는 중..."; }
  const r = await api("/api/update/apply", {method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({force: !!force})}).catch(() => null);
  if(!r || !r.ok){
    banner("warn", `<b>업데이트 실패</b> <span class="grow">${(r&&r.detail)||"알 수 없는 오류"}</span>`);
    doUpdate.busy = false;
    return;
  }
  banner("info", `<b>업데이트 완료</b> <span class="grow">${r.steps.join(" · ")}</span>
                  <button class="btn" id="btn-rs">다시 시작</button>`);
  el("btn-rs").onclick = async () => {
    await api("/api/update/restart", {method:"POST"}).catch(() => {});
    banner("info", "<b>다시 시작하는 중입니다.</b> <span class=grow>잠시 후 새로고침하세요.</span>");
  };
}
el("set-close").onclick = () => el("dlg-set").close();
/* ── 첫 실행 · 폴더 고르기 ───────────────────────────────────────────────
   저장 위치를 **묻고 시작한다.** 안 물으면 사용자는 자기 자료가 어디 쌓이는지
   모른 채 쓰기 시작하고, 나중에 백업하려 할 때 찾지 못한다. */
function firstRun(h){
  el("first-path").value = h.root || "";
  el("dlg-first").showModal();
}

/* 운영체제 폴더 창을 먼저 쓴다 — 사용자가 아는 그 창이다. 못 띄우는 환경
   (WSL 처럼 창이 다른 쪽에 뜨는 경우)에서는 서버가 즉시 실패를 돌려주고,
   호출부가 앱 안 폴더 트리로 물러난다. 매달려 있으면 "반응 없음" 으로 보인다.
   반환: 경로 | null(사용자 취소) | undefined(못 띄움 → 대체 화면) */
async function pickFolder(start){
  const r = await api("/api/pick-folder?start=" + encodeURIComponent(start || ""))
                  .catch(() => null);
  if(r && r.ok) return r.path;
  if(r && r.cancelled) return null;
  return undefined;
}

el("first-change").onclick = async () => {
  const got = await pickFolder(el("first-path").value);
  if(got === null) return;
  if(got === undefined){
    const host = el("set-picker");
    el("dlg-first").close();
    openSettings();
    host.hidden = false; drawRootPicker("", host);
    return;
  }
  el("first-path").value = got;
};

el("first-ok").onclick = async () => {
  const p = el("first-path").value.trim();
  if(!p){ alert("저장할 폴더를 골라주세요"); return; }
  try{
    const r = await api("/api/root", {method:"POST",
      headers:{"Content-Type":"application/json"}, body: JSON.stringify({path:p})});
    if(HEALTH) HEALTH.root = r.root;
    el("dlg-first").close();
    loadPatients();
  }catch(e){ alert("그 위치를 쓸 수 없습니다: " + (e.message || e)); }
};

el("set-change").onclick = async () => {
  // 첫 실행과 같은 네이티브 창. 못 띄우면 앱 안 폴더 트리로 물러난다.
  const got = await pickFolder(HEALTH && HEALTH.root);
  if(got === null) return;
  if(got === undefined){
    const host = el("set-picker"); host.hidden = false; drawRootPicker("", host);
    return;
  }
  try{
    const r = await api("/api/root", {method:"POST",
      headers:{"Content-Type":"application/json"}, body: JSON.stringify({path:got})});
    if(HEALTH) HEALTH.root = r.root;
    resetSession(); loadPatients(); loadMaint();
    alert("저장 위치를 바꿨습니다:\n" + r.root);
  }catch(e){ alert("그 위치를 쓸 수 없습니다: " + (e.message || e)); }
};
for(const b of el("set-theme").children) b.onclick = () => setTheme(b.dataset.t);
el("btn-refresh").onclick = loadPatients;
/* 정합·프레이밍은 **여기서** 돈다.

   분류 직후가 아니라 이 버튼에서 도는 이유: 정합은 "이 사진이 어느 자리에
   들어가는가"에 딸린 계산이고, 기준영상이 자리마다 다르다. 분류 직후에 돌리면
   사람이 좌·우를 고쳐도 틀린 자리에 맞춘 배치가 남는다. 배정이 확정되는 시점이
   바로 이 버튼이다.

   계산이 끝난 뒤에 화면을 넘긴다. 먼저 넘기고 뒤에서 채우면 더 빨라 보이지만,
   아직 계산 안 된 칸을 사용자가 손대는 순간 정합 결과가 그 편집을 덮는다.

   자리를 하나씩 부르는 것은 진행을 보이기 위해서다 — 서버도 한 번에 한 장씩
   추론하므로 묶어 보내도 빨라지지 않는다. 이미 계산한 자리는 서버가 건너뛴다. */
el("btn-toproc").onclick = async () => {
  const b = el("btn-toproc"), label = b.textContent;
  const todo = SLOTS.map(s => s.key).filter(k => primaryOf(k));
  b.disabled = true;
  try{
    for(let i = 0; i < todo.length; i++){
      b.textContent = `정합 중… (${i + 1}/${todo.length})`;
      const r = await api(`/api/register/${SESSION.session_id}`,
        {method: "POST", headers: {"Content-Type": "application/json"},
         body: JSON.stringify({slots: [todo[i]]})});
      REVIEW = r.review; STAGED = r.photos;
    }
  }catch(e){
    preMsg(`정합에 실패했습니다: ${e.message}`, "err");
  }finally{
    b.disabled = false; b.textContent = label;
  }
  renderVisitBadges();   // 예상 기준 → 실제로 정합에 쓰인 기준으로 갱신
  showView("proc");
  // 판을 그리기 전에 노트를 확보해 두면 십자뷰가 뜨는 순간부터 박스가 얹혀 있다.
  if(!NOTES) loadNotes();
  drawBoard();
};

/* ══ Finalize & Save ═════════════════════════════════════════════════════════
   저장 전에 "무엇이 어떤 이름으로 생기는지"를 서버(_build_plan)에서 그대로 받아
   보여준다. 화면이 파일명을 따로 조립하지 않는다 — 갈라지면 검토가 거짓말이 된다. */
const slotNm = k => (SLOTS.find(s => s.key === k) || {}).nm || k;

async function loadPlan(){
  const body = el("fin-body"), err = el("fin-err"), btn = el("btn-commit");
  err.textContent = ""; btn.disabled = true;
  if(!SESSION){ body.innerHTML = `<div class="ph">세션이 없습니다</div>`; return; }
  body.innerHTML = `<div class="ph">불러오는 중…</div>`;
  try{
    const p = await api(`/api/plan/${SESSION.session_id}`);
    renderVisitBadges();
    const items = [];
    for(const s of p.slots){
      if(s.empty){ items.push(`<li class="miss"><span class="k">${slotNm(s.slot)}</span>비어 있음</li>`); continue; }
      items.push(`<li><span class="k">${slotNm(s.slot)}</span><code>${esc(s.file)}</code>`
               + `<span class="aux">${esc(s.label)}</span></li>`);
      for(const x of s.extras)
        items.push(`<li class="sub"><span class="k">추가</span><code>${esc(x.file)}</code>`
                 + `<span class="aux">${esc(x.label)}</span></li>`);
    }
    for(const f of p.faces)
      items.push(`<li><span class="k">얼굴</span><code>${esc(f.file)}</code>`
               + `<span class="aux">${esc(f.label)} · PPT 미삽입</span></li>`);
    body.innerHTML =
        `<div class="finsec"><span class="eyebrow">저장 위치</span><code>${esc(p.patient_dir)}</code></div>`
      + `<div class="finsec"><span class="eyebrow">프레젠테이션</span><code>${esc(p.ppt)}</code>`
      + `<span class="aux">${p.ppt_exists ? "기존 파일에 슬라이드 추가" : "새로 만듦"}</span></div>`
      + `<ul class="finlist">${items.join("")}</ul>`;
    if(p.missing.length)
      err.textContent = `빈 슬롯 ${p.missing.length}곳 — ${p.missing.map(slotNm).join(", ")}. `
                      + `채우고 오거나, 이대로 확정할 수 있습니다.`;
    btn.disabled = false;
  }catch(e){
    body.innerHTML = `<div class="ph">불러오지 못했습니다</div>`;
    err.textContent = e.message;
  }
}

el("btn-tofin").onclick = () => { showView("fin"); loadPlan(); };

el("btn-commit").onclick = async () => {
  const err = el("fin-err"), btn = el("btn-commit");
  err.textContent = ""; btn.disabled = true; btn.textContent = "저장 중…";
  try{
    let r;
    try{
      r = await api(`/api/commit/${SESSION.session_id}`, {method:"POST"});
    }catch(e){
      // 빈 슬롯은 막지 않는다 — 사진이 없는 날도 있다. 대신 반드시 되묻는다.
      if(e.status === 409 && e.data?.error === "missing_slots"){
        const nm = (e.data.missing || []).map(slotNm).join(", ");
        if(!confirm(`빈 슬롯이 있습니다 — ${nm}\n\n이대로 저장할까요?`)){
          btn.textContent = "확정 저장"; btn.disabled = false; return;
        }
        r = await api(`/api/commit/${SESSION.session_id}?allow_missing=true`, {method:"POST"});
      }else throw e;
    }
    el("fin-body").innerHTML =
        `<div class="finsec"><span class="eyebrow">저장 완료</span><code>${esc(r.patient_dir)}</code></div>`
      + `<ul class="finlist">${(r.files || []).map(f => `<li><code>${esc(f)}</code></li>`).join("")}</ul>`;
    el("fin-visit").hidden = false;
    el("fin-visit").dataset.tone = "done";
    el("fin-visit").textContent = `차수 ${r.visit} 저장됨`;
    setStep("fin", "done", "완료");
    // 서버가 세션을 정리했다. 화면도 같이 비워야 유령 상태가 남지 않는다.
    resetSession();
    btn.textContent = "확정 저장";
    loadPatients();
  }catch(e){
    err.textContent = e.message || "저장 실패";
    btn.textContent = "확정 저장"; btn.disabled = false;
  }
};
addEventListener("paste", e => {
  if(VIEW !== "setup" || !picked) return;
  const files = [...(e.clipboardData?.files || [])].filter(f => f.type.startsWith("image/"));
  if(files.length){ e.preventDefault(); addFiles(files); }
});
el("find").oninput = drawList;

syncThemeSeg();
bindEditor();
bindFaceEditor();
drawDetail();
loadPatients();
showView("setup");
api("/api/health").then(h => { HEALTH = h; if(h.needs_setup) firstRun(h); }).catch(() => {});
checkWeights();
setTimeout(checkUpdate, 3000);   // 네트워크를 쓰므로 첫 화면을 막지 않는다
api("/api/case/layout").then(l => { CASE = l; syncTabs(); }).catch(() => {});
