"use strict";
/* CRoCs Fastest Lap — 단일 페이지.
   업로드(기준/현재) → 자동 분류·짝맞춤 → 검수·조정 → 이미지 저장.
   환자 목록도 PPT 도 없다. 세션은 사진을 처음 넣을 때 만들어진다. */

let SESSION = null;
const STEPS = [
  {v:"setup", n:1, nm:"업로드",        code:"Upload",              st:"",     state:""},
  {v:"pre",   n:2, nm:"자동 분류",     code:"Pre-processing (AI)", st:"대기", state:""},
  {v:"proc",  n:3, nm:"검수·조정",     code:"Process (User)",      st:"대기", state:""},
  {v:"fin",   n:4, nm:"저장",          code:"Finalize & Save",     st:"대기", state:""},
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

/* ══ 공통 ═════════════════════════════════════════════════════════════════ */
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
const post = (url, body) => api(url, {method:"POST",
  headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)});

function el(id){ return document.getElementById(id); }
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

let PHOTOS = [], VIEW = "setup", REVIEW = null, HEALTH = null, PREFS = null;
/* 업로드 화면 입력값. 세션보다 먼저 존재할 수 있다(사진부터 넣는 흐름). */
const UP = {folder:"", prefix:"", pfxCustom:false, lastZone:"cur", namesTimer:null};

const photoOf = pid => PHOTOS.find(p => p.id === pid) || null;

/* 서버는 확정되지 않은 세션을 48시간 뒤 임시 업로드째로 걷어간다(410 Gone). */
function onSessionExpired(){
  if(!SESSION) return;
  resetSession();
  showView("setup");
  alert("장시간 사용하지 않아 세션이 종료되었습니다.\n\n" +
        "업로드했던 사진은 저장되지 않았습니다. 다시 시작해 주세요.");
}

/* ══ 캔버스 공통 ══════════════════════════════════════════════════════════ */
const imgCache = new Map();
function getImg(url){
  if(imgCache.has(url)) return imgCache.get(url);
  const pr = new Promise(res => { const im = new Image();
    im.onload = () => res(im); im.onerror = () => res(null); im.src = url; });
  imgCache.set(url, pr); return pr;
}

/* 창을 사진으로 빈틈없이 덮는다 — 저장 베이크의 cover-fit 과 같은 규약 */
function coverDraw(c, img, W, H){
  const k = Math.max(W / img.width, H / img.height);
  c.drawImage(img, -img.width * k / 2, -img.height * k / 2, img.width * k, img.height * k);
}
/* 반전은 서버가 픽셀로 처리해 내려준다(/api/thumb) — 화면에는 flip 계산이 없다. */
function drawComposite(c, W, H, img, st, border){
  c.clearRect(0, 0, W, H); c.fillStyle = LETTERBOX; c.fillRect(0, 0, W, H);
  if(img){
    c.save(); c.translate(W / 2 + st.dx, H / 2 + st.dy);
    c.rotate(st.angle * Math.PI / 180); c.scale(st.scale, st.scale);
    coverDraw(c, img, W, H); c.restore();
  }
  if(border){
    c.strokeStyle = "rgba(61,144,240,.65)"; c.lineWidth = 2;
    c.strokeRect(1, 1, W - 2, H - 2);
  }
}

/* 슬롯 창 좌표(cm)는 서버 config 가 진실이다. FACE:* 는 얼굴 검수 창을 쓴다. */
function slotWindow(key){
  if(key && key.startsWith("FACE"))
    return (SESSION && SESSION.face_window)
        || (HEALTH && HEALTH.face_window) || {w: 8.4, h: 11.2};
  // 세션이 열릴 때의 비율 설정이 창을 정한다 — 세션 것이 먼저다
  return (SESSION && SESSION.windows && SESSION.windows[key])
      || (HEALTH && HEALTH.windows && HEALTH.windows[key]) || null;
}
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

/* ══ 검수·조정: 십자 보드 + 편집기 ════════════════════════════════════════ */
const SLOTS = [
  {key:"SLOT_FRONT", nm:"정면", area:"f", hk:1},
  {key:"SLOT_LEFT",  nm:"우측", area:"l", hk:2},
  {key:"SLOT_RIGHT", nm:"좌측", area:"r", hk:3},
  {key:"SLOT_UPPER", nm:"상악", area:"u", hk:4},
  {key:"SLOT_LOWER", nm:"하악", area:"b", hk:5},
];
const primaryOf = key => (REVIEW && REVIEW.bins && REVIEW.bins[key] || [])[0] || null;

const boardEl = document.getElementById("board"), segEl = document.getElementById("seg");
const slotCanvas = {};
const ED = {slot:null, dx:0, dy:0, scale:1, angle:0, img:null, drag:false, lx:0, ly:0, timer:null};
let TAB = "io";                        // "io" | "FACE:<pid>"

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
  if(TAB === "io"){
    if(ED.slot && !ED.slot.startsWith("FACE") && primaryOf(ED.slot)) pick(ED.slot);
    else { const f = SLOTS.find(x => primaryOf(x.key)); if(f) pick(f.key); }
  }
  drawPeekBadge();
}

async function renderSlot(key){
  const cv = slotCanvas[key], p = primaryOf(key);
  if(!cv || !p) return;
  const img = await getImg(p.thumb);
  const {w, h} = fitCanvas(cv, key);
  const ctx = cv.getContext("2d");
  // Tab 대보기 — 이 칸을 기준 그림 **그대로** 채운다. 기준 그림은 이미 창
  // 좌표라 지금 조정값을 얹지 않는다. 얹으면 기준이 아니게 된다.
  if(PEEK.on){
    const ref = await boardRefImg(key);
    if(ref){
      drawComposite(ctx, w, h, ref, {dx:0, dy:0, scale:1, angle:0}, false);
      paintGrid(ctx, cv, w, h, slotWindow(key));   // 기준도 같은 창 좌표다
      return;
    }
  }
  // 겹쳐보기가 켜져 있으면 다섯 슬롯 전부 아나글리프로 그린다.
  if(OV.on){
    const ref = await boardRefImg(key);
    if(ref){
      drawAnaglyph(ctx, w, h, ref, img, p.editor);
      paintGrid(ctx, cv, w, h, slotWindow(key));
      return;
    }
  }
  drawComposite(ctx, w, h, img, p.editor, false);
  paintGrid(ctx, cv, w, h, slotWindow(key));
}

/* 슬롯의 기준영상 — 기준 사진을 창에 구워 낸 것. 슬롯당 하나다. */
async function boardRefImg(slot){
  if(!SESSION || !OV.list[slot]) return null;
  const c = OV.board[slot];
  if(c !== undefined) return c;
  let img = null;
  try{ img = await getImg(`/api/reference/${SESSION.session_id}/${slot}`); }
  catch(e){ img = null; }
  OV.board[slot] = img;
  return img;
}

/* ── 기준 대보기 (Tab) · 크게 보기 (Space) ───────────────────────────────── */
const PEEK = {on: false, timer: null};
const ZOOM = {on: false};
const zoomHost = () => boardEl && boardEl.parentElement;

function toggleZoom(){
  if(TAB !== "io") return;                    // FACE 탭은 이미 한 장 크게다
  if(!ZOOM.on && !ED.slot) return;
  ZOOM.on = !ZOOM.on;
  const cv = el("ed-canvas"), host = zoomHost();
  if(!cv || !host){ ZOOM.on = false; return; }
  if(ZOOM.on){
    host.appendChild(cv);
    cv.classList.add("zoom");
    boardEl.hidden = true;
  }else{
    el("ed-fit").appendChild(cv);
    cv.classList.remove("zoom");
    boardEl.hidden = false;
  }
  drawPeekBadge();
  renderEditor();
  if(!ZOOM.on) redrawBoardSlots();
}
function exitZoom(){ if(ZOOM.on) toggleZoom(); }

function drawPeekBadge(){
  const host = zoomHost();
  if(!host || !boardEl) return;
  boardEl.classList.toggle("peeking", PEEK.on && !ZOOM.on);
  let b = host.querySelector(".peekbadge");
  if(!PEEK.on && !ZOOM.on){ if(b) b.remove(); return; }
  if(!b){
    b = document.createElement("div");
    b.className = "peekbadge";
    host.appendChild(b);
  }
  if(PEEK.on) b.innerHTML = `<b>기준 사진</b> 보는 중 · <kbd>Tab</kbd> 으로 해제`;
  else b.innerHTML = `크게 보기 · <kbd>Space</kbd> 로 판으로 · <kbd>Tab</kbd> 기준 대보기`;
}

function flashPeekNote(text){
  const host = zoomHost();
  if(!host) return;
  let b = host.querySelector(".peekbadge");
  if(!b){
    b = document.createElement("div");
    b.className = "peekbadge";
    host.appendChild(b);
  }
  b.textContent = text;
  clearTimeout(PEEK.timer);
  PEEK.timer = setTimeout(() => { if(!PEEK.on) drawPeekBadge(); }, 1800);
}

async function togglePeek(){
  const has = Object.keys(OV.list || {}).length > 0;
  if(!PEEK.on && !has){
    flashPeekNote("기준 사진이 없습니다 — 대볼 기준이 없습니다");
    return;
  }
  PEEK.on = !PEEK.on;
  clearTimeout(PEEK.timer);
  drawPeekBadge();
  if(ZOOM.on){
    if(PEEK.on && !OV.img) await loadOverlayImg();
    renderEditor();
  }else{
    redrawBoardSlots();
  }
}

function redrawBoardSlots(){
  for(const k of Object.keys(slotCanvas)) renderSlot(k);
}

async function pick(key){
  const p = primaryOf(key); if(!p) return;
  const meta = SLOTS.find(x => x.key === key);
  ED.slot = key;
  ED.dx = p.editor.dx; ED.dy = p.editor.dy; ED.scale = p.editor.scale; ED.angle = p.editor.angle;
  ED.img = await getImg(p.thumb);
  [...boardEl.children].forEach(c => c.setAttribute("aria-pressed", c.style.gridArea === meta.area));
  [...segEl.children].forEach(g => g.setAttribute("aria-pressed", g.dataset.key === key));
  el("dock-title").firstChild.textContent =
    `${meta.nm} · ${p.label || "—"} ${Math.round((p.confidence || 0) * 100)}%`;
  syncKnobs(); renderEditor();
  await syncOverlayBar(); renderEditor();   // 기준영상이 늦게 오면 한 번 더 그린다
}

/* FACE 탭 — 사진 한 장이 슬라이드 하나다. 같은 편집기를 얼굴 창으로 쓴다. */
async function pickFace(pid){
  const p = photoOf(pid); if(!p) return;
  ED.slot = "FACE:" + pid;
  ED.dx = p.editor.dx; ED.dy = p.editor.dy; ED.scale = p.editor.scale; ED.angle = p.editor.angle;
  ED.img = await getImg(p.thumb);
  el("dock-title").firstChild.textContent =
    `FACE · ${Math.round((p.confidence || 0) * 100)}%`;
  el("ov-bar").hidden = true;
  syncKnobs(); renderEditor();
}

el("ov-on").onchange = async e => {
  OV.on = e.target.checked;
  if(OV.on && !OV.img) await loadOverlayImg();
  renderEditor();
  redrawBoardSlots();
};

/* 어느 슬롯에 기준이 있나. 세션마다 register 뒤에 받아 둔다. */
async function loadRefList(){
  OV.list = {}; OV.img = null; OV.slot = null; OV.board = {};
  if(!SESSION) return;
  OV.list = await api(`/api/references/${SESSION.session_id}`).catch(() => ({})) || {};
}

/* ── 기준 겹쳐보기 (아나글리프) — 기준은 빨강, 현재는 청록 ─────────────────── */
const OV = {on: false, img: null, slot: null, list: {}, board: {}};
let LETTERBOX = "#000";

function equalize(d){                     // 8비트 그레이 히스토그램 평활화
  const n = d.length / 4, hist = new Uint32Array(256);
  const g = new Uint8ClampedArray(n);
  for(let i = 0; i < n; i++){
    const v = (d[i*4] * 0.299 + d[i*4+1] * 0.587 + d[i*4+2] * 0.114) | 0;
    g[i] = v; hist[v]++;
  }
  const lut = new Uint8ClampedArray(256);
  let acc = 0;
  for(let v = 0; v < 256; v++){ acc += hist[v]; lut[v] = acc * 255 / n; }
  for(let i = 0; i < n; i++) g[i] = lut[g[i]];
  return g;
}

function drawAnaglyph(ctx, W, H, refImg, curImg, st){
  const a = document.createElement("canvas"), b = document.createElement("canvas");
  a.width = b.width = W; a.height = b.height = H;
  drawComposite(a.getContext("2d"), W, H, refImg, {dx:0, dy:0, scale:1, angle:0}, false);
  drawComposite(b.getContext("2d"), W, H, curImg, st, false);
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
  const has = ED.slot && !ED.slot.startsWith("FACE") && OV.list[ED.slot];
  bar.hidden = !has;
  // 대보기 안내도 같이 — 기준이 없으면 Tab 이 할 일이 없다. Space·Shift 줄은
  // 기준이 없을 때도 쓰는 키라 안내 블록 자체는 늘 떠 있다.
  const peekHint = el("hint-peek");
  if(peekHint) peekHint.hidden = bar.hidden;
  if(!has){ OV.img = null; return; }
  if(OV.slot !== ED.slot || !OV.img){
    OV.slot = ED.slot; OV.img = null;
    if(OV.on || PEEK.on) await loadOverlayImg();
  }
}

async function loadOverlayImg(){
  if(!SESSION || !OV.slot) return;
  try{ OV.img = await getImg(`/api/reference/${SESSION.session_id}/${OV.slot}`); }
  catch(e){ OV.img = null; }
}

function renderEditor(){
  const cv = el("ed-canvas"); if(!cv || !ED.slot) return;
  const {w, h} = fitCanvas(cv, ED.slot);
  const ctx = cv.getContext("2d");
  if(ZOOM.on && PEEK.on && OV.img && OV.slot === ED.slot){
    drawComposite(ctx, w, h, OV.img, {dx:0, dy:0, scale:1, angle:0}, false);
  }else if(OV.on && OV.img && OV.slot === ED.slot){
    drawAnaglyph(ctx, w, h, OV.img, ED.img, ED);
  }else{
    drawComposite(ctx, w, h, ED.img, ED, true);
  }
  paintGrid(ctx, cv, w, h, slotWindow(ED.slot));
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/* 값 칸에 숫자를 써 넣는다 — **지금 그 칸에 타이핑 중이면 건드리지 않는다.**
   드래그와 휠은 움직이는 내내 syncKnobs 를 부르는데, 그때마다 덮어쓰면 커서가
   튀고 `-` 나 `1` 만 친 중간 상태가 지워진다. */
function setNum(id, v){
  const n = el(id);
  if(n && n !== document.activeElement) n.value = v;
}

/* 값 칸 하나를 편집기에 잇는다.

   반영은 치는 도중(input)이 아니라 **다 치고 났을 때**(change) 한다 — `-` 만
   친 순간에 사진이 왼쪽 끝으로 튀면 안 된다. Enter 는 포커스를 놓아 같은 길로
   보낸다. 값이 헛것이면 실제 값을 다시 써 준다. */
function bindNum(id, lo, hi, step, get, set, apply, resync, editable){
  const n = el(id); if(!n) return;
  n.onchange = () => {
    // syncKnobs 는 타이핑 중인 칸을 건드리지 않는다 — 포커스를 먼저 놓아 주어야
    // 잘라 낸 값·되돌린 값이 칸에 실제로 써진다.
    n.blur();
    if(!editable()){ resync(); return; }
    const v = parseFloat(n.value);
    if(!Number.isFinite(v)){ resync(); return; }
    set(clamp(v, lo, hi));
    apply();
  };
  n.onkeydown = e => { if(e.key === "Enter"){ e.preventDefault(); n.blur(); } };
  // ◀ ▶ — 한 칸씩 민다. 눈금에 **다시 맞춰** 놓는 것이 요령이다: 0.1 을 더하기만
  // 하면 부동소수 찌꺼기가 쌓여 3.0000000000000004° 같은 값이 칸에 뜬다.
  const bump = dir => {
    if(!editable()) return;
    const v = Math.round((get() + dir * step) / step) * step;
    set(clamp(+v.toFixed(4), lo, hi));
    apply();
  };
  holdRepeat(el(id + "-dn"), () => bump(-1));
  holdRepeat(el(id + "-up"), () => bump(+1));
}

/* 버튼을 누르고 있으면 이어서 눌린다.

   한 칸이 0.1°·1px 이라 한 번씩만 먹으면 조금 옮기는 데도 손이 여러 번 간다.
   `click` 이 아니라 `pointerdown` 에서 시작하는 이유가 그것이다 — click 은 길게
   눌러도 한 번뿐이다. 창 어디서 손을 떼든 멈추도록 문서에도 걸어 둔다. */
function holdRepeat(btn, fn){
  if(!btn) return;
  let wait = null, tick = null;
  const stop = () => { clearTimeout(wait); clearInterval(tick); wait = tick = null; };
  btn.addEventListener("pointerdown", e => {
    if(btn.disabled || e.button) return;
    e.preventDefault();          // 포커스를 주지 않는다 — Space 가 '크게 보기'와 겹친다
    fn();
    wait = setTimeout(() => { tick = setInterval(fn, 60); }, 400);
  });
  for(const ev of ["pointerup", "pointercancel", "pointerleave"])
    btn.addEventListener(ev, stop);
  addEventListener("pointerup", stop);
  addEventListener("pointercancel", stop);
}

function syncKnobs(){
  el("ed-angle").value = ED.angle.toFixed(1);
  el("ed-scale").value = Math.round(clamp(ED.scale, .5, 2) * 100);
  el("ed-tx").value = Math.round(clamp(ED.dx, -200, 200));
  el("ed-ty").value = Math.round(clamp(ED.dy, -200, 200));
  setNum("v-angle", ED.angle.toFixed(1));
  setNum("v-scale", Math.round(ED.scale * 100));
  setNum("v-tx", Math.round(ED.dx));
  setNum("v-ty", Math.round(ED.dy));
}

function edPhoto(){
  if(!ED.slot) return null;
  return ED.slot.startsWith("FACE:") ? photoOf(ED.slot.slice(5)) : primaryOf(ED.slot);
}

function afterEdit(){
  const p = edPhoto();
  if(p) p.editor = {dx: ED.dx, dy: ED.dy, scale: ED.scale, angle: ED.angle};
  syncKnobs(); renderEditor();
  if(!ED.slot.startsWith("FACE")) renderSlot(ED.slot);
  saveEdit();
}

function saveEdit(){
  clearTimeout(ED.timer);
  el("ed-saved").textContent = "…";
  ED.timer = setTimeout(async () => {
    try{
      const r = await post("/api/adjust", {session_id: SESSION.session_id, slot: ED.slot,
                                           dx: ED.dx, dy: ED.dy, scale: ED.scale, angle: ED.angle});
      // 서버가 cover 조건으로 배율을 되돌릴 수 있다 — 창에 빈틈이 생기지 않게
      if(r.clamped_scale && Math.abs(r.clamped_scale - ED.scale) > 1e-6){
        ED.scale = r.clamped_scale;
        const p = edPhoto(); if(p) p.editor.scale = ED.scale;
        syncKnobs(); renderEditor();
        if(!ED.slot.startsWith("FACE")) renderSlot(ED.slot);
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
  // 값 칸과 ◀ ▶ 는 **조절 바와 같은 눈금**을 쓴다(회전 0.1° · 배율 1% · 이동 1px).
  // 배율만 칸의 단위가 % 라, 읽고 쓸 때 100 을 곱하고 나눈다.
  const onSlot = () => !!(ED.slot && edPhoto());
  bindNum("v-angle", -10, 10, .1, () => ED.angle, v => ED.angle = v,
          afterEdit, syncKnobs, onSlot);
  bindNum("v-scale", 50, 200, 1, () => ED.scale * 100, v => ED.scale = v / 100,
          afterEdit, syncKnobs, onSlot);
  bindNum("v-tx", -200, 200, 1, () => ED.dx, v => ED.dx = v,
          afterEdit, syncKnobs, onSlot);
  bindNum("v-ty", -200, 200, 1, () => ED.dy, v => ED.dy = v,
          afterEdit, syncKnobs, onSlot);
  /* **initial-fit** = 자동으로 잡아 준 첫 구도. 기준이 있으면 정합 결과, 없으면
     프레이밍 모델의 예측이고, 둘 다 못 쓴 자리에서만 cover-fit 이다.
     예전에는 무조건 cover-fit(가운데·무회전)으로 돌아가서, 손이 미끄러졌을 때
     되돌리면 **정합까지 함께 버려졌다** — 그 자리를 다시 잡으려면 눈대중으로
     맞추는 수밖에 없었다. 서버가 그 값을 editor0 로 함께 내려 준다. */
  el("ed-reset").onclick = () => {
    const p = edPhoto(); if(!p) return;
    const z = p.editor0 || {dx: 0, dy: 0, scale: 1, angle: 0};
    ED.dx = z.dx; ED.dy = z.dy; ED.scale = z.scale; ED.angle = z.angle;
    afterEdit();
  };
  cv.addEventListener("pointerdown", e => {
    if(!ED.slot) return;
    ED.drag = true; ED.lx = e.offsetX; ED.ly = e.offsetY; cv.setPointerCapture(e.pointerId);
  });
  cv.addEventListener("pointermove", e => {
    if(!ED.drag) return;
    const f = cv.width / cv.clientWidth;
    ED.dx += (e.offsetX - ED.lx) * f; ED.dy += (e.offsetY - ED.ly) * f;
    ED.lx = e.offsetX; ED.ly = e.offsetY;
    syncKnobs(); renderEditor();
    if(!ED.slot.startsWith("FACE")) renderSlot(ED.slot);
  });
  cv.addEventListener("pointerup", () => { if(ED.drag){ ED.drag = false; afterEdit(); } });
  cv.addEventListener("wheel", e => {
    if(!ED.slot) return;
    e.preventDefault();
    ED.scale = clamp(ED.scale * (e.deltaY < 0 ? 1.03 : .97), .5, 2);
    afterEdit();
  }, {passive:false});
}

/* ══ 격자 보기 (Shift) ═══════════════════════════════════════════════════════
   구도를 **재는** 도구다. 기울었는가, 가운데인가, 기준과 배율이 같은가 —
   눈대중으로는 안 되는 판단들이라 자를 대야 한다.

   격자는 **창에 고정**된다. 사진을 따라 돌면 기준이 함께 기울어 항상 맞아
   보이므로, 기울었는지를 영영 알 수 없다. 사진만 그 아래에서 움직인다.

   저장되는 사진에는 들어가지 않는다 — 여기는 화면을 그리는 길이고, 굽는 길은
   따로다. 막을 것이 없다. */
const GRID = {on: false};

/* 격자 간격(cm). **짧은 변이 8칸을 넘지 않는 가장 촘촘한 값**을 고른다.

   창이 자리마다 다르기 때문이다: 구내 슬롯과 얼굴 자리는 크기가 다르다.
   한 값으로 못박으면 한쪽은 방충망이 되고 다른 쪽은 성기다. 이 규칙이면
   8.4×6.3 창은 1cm(8×6칸)가 되어 눈에 보이는 밀도가 알맞고, 비율 설정으로
   창이 바뀌어도 같은 규칙으로 따라간다. */
function gridStep(win){
  const short = Math.min(win.w, win.h);
  for(const cm of [1, 2, 5]) if(short / cm <= 8) return cm;
  return 5;
}

/* 중심에서 바깥으로 그은 선들의 좌표.

   **가장자리가 아니라 중심에 문다.** 8.4cm 창의 절반은 4.2cm 라, 가장자리에서
   1cm 씩 세면 4.0 과 5.0 에 선이 가고 정작 가장 중요한 중심선이 격자에서
   빗나간다. 중심을 첫 선으로 두면 그 일이 없다(반환값 [0]번이 중심). */
function gridLines(center, size, step){
  const out = [];
  for(let v = center; v > 0; v -= step) out.push(v);
  for(let v = center + step; v < size; v += step) out.push(v);
  return out;
}

/* 캔버스 위에 격자를 얹는다. 켜져 있지 않거나 창을 모르면 아무것도 안 한다.

   굵기를 화면 기준으로 환산하는 것이 요령이다. 캔버스 고유 크기(840px)와 화면
   표시 크기(≈320px)가 다르므로, 캔버스 1px 선은 화면에서 0.4px 가 되어 사라진다.
   드래그 환산이 쓰는 그 배율을 그대로 쓰면 판·편집기·크게 보기 어디서든 화면에서
   같은 굵기로 보인다.

   선을 두 번 긋는 이유 — 구내 사진 한 장에 붉은 잇몸·흰 치아·검은 여백이 함께
   있어서 한 가지 색으로는 어딘가에서 반드시 묻힌다. 검은 선을 한 픽셀 밀어
   깔고 그 위에 흰 선을 올리면 어느 바탕에서도 남는다. */
function paintGrid(ctx, cv, W, H, win){
  if(!GRID.on || !win || !win.w || !win.h) return;
  const step = gridStep(win) * (W / win.w);
  if(!(step > 2)) return;                    // 너무 촘촘하면 그리지 않는다
  // 화면에서의 굵기로 환산한다 — 격자 1.5px, 중심 십자 2.5px.
  // 그림자 오프셋은 **그 선의 굵기와 같게** 둔다. 굵기와 따로 놀면 번진 것처럼
  // 보이고, 굵어질수록 그 어긋남이 눈에 띈다.
  // **화면에 안 붙어 있으면 그리지 않는다.** 숨은 칸은 clientWidth 가 0 이라
  // 배율을 알 수 없고, 그대로 그리면 선이 제 굵기의 1/4 로 얇게 박힌다.
  // 탭이 열릴 때 그 판을 다시 그리므로(showTab) 빠뜨리지 않는다.
  if(!cv.clientWidth) return;
  const px = cv.width / cv.clientWidth;
  const thin = px * 1.5, thick = px * 2.5;
  const cx = W / 2, cy = H / 2;
  const xs = gridLines(cx, W, step), ys = gridLines(cy, H, step);

  ctx.save();
  for(const [color, off] of [["rgba(0,0,0,.40)", thin], ["rgba(255,255,255,.32)", 0]]){
    ctx.strokeStyle = color; ctx.lineWidth = thin;
    ctx.beginPath();
    for(let i = 1; i < xs.length; i++){ ctx.moveTo(xs[i] + off, 0); ctx.lineTo(xs[i] + off, H); }
    for(let i = 1; i < ys.length; i++){ ctx.moveTo(0, ys[i] + off); ctx.lineTo(W, ys[i] + off); }
    ctx.stroke();
  }
  // 중심 십자 — 지금 무엇을 기준으로 보는지가 한눈에 잡혀야 한다
  for(const [color, off] of [["rgba(0,0,0,.35)", thick], ["rgba(61,144,240,.9)", 0]]){
    ctx.strokeStyle = color; ctx.lineWidth = thick;
    ctx.beginPath();
    ctx.moveTo(cx + off, 0); ctx.lineTo(cx + off, H);
    ctx.moveTo(0, cy + off); ctx.lineTo(W, cy + off);
    ctx.stroke();
  }
  ctx.restore();
}

/* 창 크기가 바뀌면 캔버스가 화면에서 차지하는 크기도 바뀐다. 격자 굵기는 그
   비율로 정해지므로 다시 그려야 굵기가 유지된다 — 사진은 CSS 가 늘려 주지만
   격자는 캔버스에 박힌 그림이라 저절로 따라오지 않는다. */
let gridResizeTimer = null;
addEventListener("resize", () => {
  if(!GRID.on || VIEW !== "proc") return;
  clearTimeout(gridResizeTimer);
  gridResizeTimer = setTimeout(() => {
    if(ED.slot) renderEditor();
    redrawBoardSlots();
  }, 150);
});

function toggleGrid(){
  GRID.on = !GRID.on;
  renderEditor();
  redrawBoardSlots();
}

/* Shift 톡 — **단독으로 눌렀다 뗐을 때만** 반응한다.

   사이에 다른 키나 마우스가 끼면 그건 Shift+Tab(포커스를 빼내는 길, 일부러
   브라우저에 넘겨준다)이거나 Shift+클릭이지 격자를 켜라는 뜻이 아니다.
   창이 포커스를 잃으면 추적을 접는다 — 떼는 신호를 못 받고 남은 상태로 다음
   Shift 가 엉뚱하게 반응하는 것을 막는다. */
let shiftAlone = false;
addEventListener("keydown", e => {
  if(e.key === "Shift"){ if(!e.repeat) shiftAlone = true; return; }
  shiftAlone = false;
}, true);
addEventListener("keyup", e => {
  if(e.key !== "Shift") return;
  const alone = shiftAlone; shiftAlone = false;
  if(!alone || VIEW !== "proc") return;
  const t = document.activeElement;   // 글 치는 중이면 격자가 튀어나오면 안 된다
  if(t && (t.isContentEditable || t.tagName === "TEXTAREA" ||
           (t.tagName === "INPUT" && t.type !== "range"))) return;
  toggleGrid();
}, true);
addEventListener("pointerdown", () => { shiftAlone = false; }, true);
addEventListener("blur", () => { shiftAlone = false; });

/* 키보드: 1~5 슬롯, 방향키 이동, Q/E 회전, A/D 배율 (e.code라 한/영 무관) */
addEventListener("keydown", e => {
  if(VIEW !== "proc") return;
  const t = e.target;
  if(t && (t.isContentEditable || t.tagName === "TEXTAREA" ||
           (t.tagName === "INPUT" && t.type !== "range"))) return;
  const code = e.code;
  if(code === "Tab" && TAB === "io" && !e.shiftKey){
    e.preventDefault(); togglePeek(); return;
  }
  if(code === "Space" && TAB === "io"){
    e.preventDefault(); toggleZoom(); return;
  }
  if(TAB === "io" && (code.startsWith("Digit") || code.startsWith("Numpad"))){
    const s = SLOTS.find(x => x.hk === +code.slice(-1));
    if(s && primaryOf(s.key)){ pick(s.key); e.preventDefault(); }
    return;
  }
  if(!ED.slot || !edPhoto()) return;
  // 한 번 누를 때 움직이는 양 = **조절 바 한 칸**. 이동 1px(0.1mm) · 회전 0.1°
  // · 배율 1%. 키와 슬라이더가 다른 눈금을 쓰면 "슬라이더로 1 올렸다가 키로
  // 되돌리기"가 안 되고, 같은 화면의 두 도구가 서로 다른 자를 들게 된다.
  // 크게 옮길 일은 드래그와 휠이 맡는다(눌러 두면 키가 자동 반복된다).
  const mv = 1, rot = .1, sc = .01;
  let hit = true;
  switch(code){
    case "ArrowLeft":  ED.dx = clamp(ED.dx - mv, -200, 200); break;
    case "ArrowRight": ED.dx = clamp(ED.dx + mv, -200, 200); break;
    case "ArrowUp":    ED.dy = clamp(ED.dy - mv, -200, 200); break;
    case "ArrowDown":  ED.dy = clamp(ED.dy + mv, -200, 200); break;
    case "KeyQ": ED.angle = clamp(ED.angle - rot, -10, 10); break;
    case "KeyE": ED.angle = clamp(ED.angle + rot, -10, 10); break;
    case "KeyA": ED.scale = clamp(ED.scale - sc, .5, 2); break;
    case "KeyD": ED.scale = clamp(ED.scale + sc, .5, 2); break;
    default: hit = false;
  }
  if(hit){ e.preventDefault(); afterEdit(); }
});

/* ── 검수 탭 — 십자뷰 + FACE 사진별 슬라이드 ─────────────────────────────── */
function buildTabs(){
  const bar = el("proc-tabs"); if(!bar) return;
  const faces = (REVIEW && REVIEW.bins && REVIEW.bins.FACE) || [];
  let html = `<button type="button" role="tab" data-tab="io" aria-selected="${TAB === "io"}">IntraOral</button>`;
  faces.forEach((p, i) => {
    html += `<button type="button" role="tab" data-tab="FACE:${p.id}"` +
      ` aria-selected="${TAB === "FACE:" + p.id}">FACE ${i + 1}</button>`;
  });
  bar.innerHTML = html;
  for(const b of bar.children) b.onclick = () => showTab(b.dataset.tab);
}

function showTab(name){
  if(name !== "io" && !photoOf(name.slice(5))) name = "io";
  const face = name !== "io";
  if(face) exitZoom();          // TAB 이 아직 io 일 때 크게 보기를 접는다
  TAB = name;
  document.querySelectorAll("#proc-tabs button").forEach(b =>
    b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  // FACE 도 IntraOral 과 같은 배치다 — 왼쪽 판 자리에 사진이 크게, 오른쪽
  // 독에 슬라이더. 캔버스를 판 자리로 옮기는 방식은 Space 크게 보기와 같다.
  const cv = el("ed-canvas");
  boardEl.hidden = face;        // 십자 판 자체만 감춘다 — 자리는 캔버스가 쓴다
  segEl.hidden = face;
  if(face){
    zoomHost().appendChild(cv);
    cv.classList.add("zoom");
    el("ov-bar").hidden = true;
    pickFace(name.slice(5));
  }else{
    el("ed-fit").appendChild(cv);
    cv.classList.remove("zoom");
    el("ov-bar").hidden = true;
    if(ED.slot && ED.slot.startsWith("FACE")) ED.slot = null;
    drawBoard();
  }
}

/* ══ 자동 분류 · 짝맞춤 ══════════════════════════════════════════════════ */
const CAT_ROWS = [
  {slot:"FACE",       label:"FACE"},
  {slot:"SLOT_FRONT", label:"IO_FRONT"},
  {slot:"SLOT_LEFT",  label:"IO_RIGHT"},
  {slot:"SLOT_RIGHT", label:"IO_LEFT"},
  {slot:"SLOT_UPPER", label:"IO_UPPER"},
  {slot:"SLOT_LOWER", label:"IO_LOWER"},
];

async function runClassify(){
  showView("pre");
  preMsg("분류 중…", "busy");
  try{
    const r = await api(`/api/classify/${SESSION.session_id}`, {method:"POST"});
    REVIEW = r.review; PHOTOS = r.photos;
    preMsg("");
    drawPairs();
    renderVisitBadges();
  }catch(e){ preMsg(e.message, "err"); }
}
function preMsg(text, kind){
  const n = el("pre-msg"); if(!n) return;
  n.textContent = text || ""; n.dataset.kind = kind || "";
}

const othersOf = pool => PHOTOS.filter(p => !p.slot && p.pool === pool);

function photoCard(p, isPrimary, binKey){
  const low = p.confidence < 0.75;
  return `<figure class="ph-card${isPrimary ? " primary" : ""}" draggable="true" data-pid="${p.id}">` +
    `<div class="pcimg">` +
      `<img src="${p.card || p.thumb}" alt="" draggable="false" loading="lazy">` +
      (isPrimary && binKey && binKey !== "FACE" ? `<span class="tagp">대표</span>` : "") +
      `<button type="button" class="flipbar${p.flip ? " on" : ""}" data-pid="${p.id}"` +
        ` draggable="false" title="사진을 위아래로 뒤집습니다">` +
        `↕ 상하반전${p.flip ? " 켜짐" : ""}</button>` +
    `</div>` +
    `<figcaption${low ? ` class="low"` : ""}>${p.label || "—"} ${Math.round((p.confidence || 0) * 100)}%</figcaption>` +
    `</figure>`;
}

function binHtml(row, pool, list){
  const face = row.slot === "FACE";
  return `<div class="bin${face ? " face" : ""}" data-slot="${row.slot}" data-pool="${pool}">` +
    `<div class="bin-h">${row.label}` +
      (pool === "ref" ? ` <span class="poolmark">기준</span>` : "") +
      `<span class="hr">` +
      (face && pool === "cur" ? `<button type="button" class="minibtn" id="face-sort"` +
              ` title="EXIF 촬영 시각 순서로 세웁니다">촬영순</button>` : "") +
      `<span class="cnt">${list.length || ""}</span></span></div>` +
    `<div class="bin-body${face ? " grid3" : ""}">` +
      (list.length ? list.map((p, i) => photoCard(p, i === 0, row.slot)).join("")
                   : `<p class="bin-empty">비어 있음</p>`) +
    `</div></div>`;
}

function drawPairs(){
  if(!REVIEW) return;
  const revisit = REVIEW.mode === "revisit";
  el("pair-hint").hidden = !revisit;
  const box = el("pairs");
  box.classList.toggle("solo", !revisit);
  // 상단 = 기준 사진 한 줄, 점선 짝 연결, 하단 = 현재 사진 한 줄.
  // 카테고리가 열(column)이 되어 위아래로 짝이 맞는다. 초진은 아래 줄만.
  const cell = (html, col, row) =>
    html.replace(/^<div /, `<div style="grid-column:${col};grid-row:${row}" `);
  let out = "";
  CAT_ROWS.forEach((row, i) => {
    const col = i + 1;
    const cur = (REVIEW.bins && REVIEW.bins[row.slot]) || [];
    const ref = (REVIEW.ref_bins && REVIEW.ref_bins[row.slot]) || [];
    if(revisit){
      out += cell(binHtml(row, "ref", ref), col, 1);
      let link;
      if(row.slot === "FACE")
        link = `<div class="pairlink excl" title="얼굴은 정합하지 않습니다 — 프레이밍만">정합 제외</div>`;
      else if(ref.length && cur.length)
        link = `<div class="pairlink ok" title="기준·현재가 짝을 이뤄 정합됩니다">⇣ 정합</div>`;
      else
        link = `<div class="pairlink miss" title="한쪽이 비어 있어 프레이밍만 합니다">짝 없음</div>`;
      out += cell(link, col, 2);
      out += cell(binHtml(row, "cur", cur), col, 3);
    }else{
      out += cell(binHtml(row, "cur", cur), col, 1);
    }
  });
  box.innerHTML = out;
  const sortBtn = el("face-sort");
  if(sortBtn) sortBtn.onclick = sortFace;

  const others = othersOf("cur");
  el("bin-others").innerHTML =
    `<div class="bin-h">OTHERS<span class="cnt">${others.length || ""}</span></div>` +
    `<div class="bin-body row">` +
      (others.length ? others.map(p => photoCard(p, false, null)).join("")
                     : `<p class="bin-empty">비어 있음</p>`) +
    `</div>`;
  const refOthers = othersOf("ref");
  const ro = el("bin-others-ref");
  ro.hidden = !revisit || !refOthers.length;
  if(!ro.hidden)
    ro.innerHTML =
      `<div class="bin-h">OTHERS <span class="poolmark">기준</span>` +
      `<span class="cnt">${refOthers.length}</span></div>` +
      `<div class="bin-body row">${refOthers.map(p => photoCard(p, false, null)).join("")}</div>`;

  const nCur = PHOTOS.filter(p => p.pool === "cur").length;
  const missing = CAT_ROWS.filter(r => r.slot !== "FACE" && !((REVIEW.bins[r.slot] || []).length));
  el("pre-n").textContent = `${nCur}장` + (missing.length ? ` · 빈 슬롯 ${missing.length}` : "");
  el("btn-toproc").disabled = !Object.values(REVIEW.bins || {}).some(l => l.length);
  bindBinDnD();
}

/* 떨어뜨린 위치가 순서를 정한다 — 위쪽에 놓으면 대표가 된다. */
function dropIndex(bin, e){
  const cards = [...bin.querySelectorAll(".ph-card")];
  const body = bin.querySelector(".bin-body");
  const row = !!body && body.classList.contains("row");
  const grid = !!body && body.classList.contains("grid3");
  for(let i = 0; i < cards.length; i++){
    const r = cards[i].getBoundingClientRect();
    if(grid){
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
  for(const f of document.querySelectorAll(".ph-card")){
    f.ondragstart = e => e.dataTransfer.setData("pid", f.dataset.pid);
  }
  for(const b of document.querySelectorAll(".flipbar")){
    b.onclick = e => { e.stopPropagation(); toggleFlip(b.dataset.pid); };
  }
  for(const bin of document.querySelectorAll(".bin")){
    bin.ondragover = e => { e.preventDefault(); bin.classList.add("over"); };
    bin.ondragleave = () => bin.classList.remove("over");
    bin.ondrop = e => {
      e.preventDefault(); bin.classList.remove("over");
      const pid = e.dataTransfer.getData("pid");
      if(!pid) return;
      const p = photoOf(pid);
      // 기준↔현재는 옮길 수 없다 — 열이 곧 풀이다.
      if(p && bin.dataset.pool && p.pool !== bin.dataset.pool){
        preMsg("기준 사진과 현재 사진 사이로는 옮길 수 없습니다", "warn");
        return;
      }
      assign(pid, bin.dataset.slot || null, dropIndex(bin, e));
    };
  }
}

async function toggleFlip(pid){
  const p = photoOf(pid); if(!p) return;
  try{
    const r = await post("/api/flip", {session_id: SESSION.session_id,
                                       photo_id: pid, on: !p.flip});
    // 픽셀이 뒤집혔다 — 이 사진의 썸네일 캐시는 전부 죽었다.
    for(const url of [...imgCache.keys()]) if(url.includes(`/${pid}`)) imgCache.delete(url);
    REVIEW = r.review;
    const i = PHOTOS.findIndex(x => x.id === pid);
    if(i >= 0) PHOTOS[i] = r.photo;
    drawPairs();
  }catch(e){ preMsg(e.message, "err"); }
}

async function sortFace(){
  const btn = el("face-sort");
  if(btn) btn.disabled = true;
  try{
    const r = await post("/api/sort", {session_id: SESSION.session_id, slot: "FACE", pool: "cur"});
    REVIEW = r.review; PHOTOS = r.photos;
    drawPairs();
    preMsg(r.with_time === r.n ? `촬영순 정렬 · ${r.n}장`
      : `촬영순 정렬 · ${r.n}장 (EXIF 시각 ${r.with_time}장, 나머지는 파일명 순)`,
      r.with_time === r.n ? "ok" : "warn");
  }catch(e){ preMsg(e.message, "err"); }
  finally{ const b = el("face-sort"); if(b) b.disabled = false; }
}

async function assign(pid, slot, at){
  try{
    const r = await post("/api/assign", {session_id: SESSION.session_id,
                                         photo_id: pid, slot, at});
    REVIEW = r.review; PHOTOS = r.photos;
    drawPairs();
  }catch(e){ preMsg(e.message, "err"); }
}

/* ══ 업로드 ═══════════════════════════════════════════════════════════════ */
async function ensureSession(){
  if(SESSION) return SESSION;
  const r = await post("/api/session", {folder: UP.folder, prefix: UP.pfxCustom ? UP.prefix : ""});
  SESSION = r;
  setStep("setup", "", UP.folder || "");
  return r;
}

/* 폴더·접두어를 서버 세션에 반영한다. 세션이 없으면(아직 사진 전) 나중에 간다. */
function queueNames(){
  clearTimeout(UP.namesTimer);
  UP.namesTimer = setTimeout(pushNames, 300);
}
async function pushNames(){
  if(!SESSION || !UP.folder) return;
  try{
    const r = await api(`/api/session/${SESSION.session_id}/names`, {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({folder: UP.folder, prefix: UP.pfxCustom ? UP.prefix : ""})});
    el("up-msg").textContent = r.folder_exists
      ? "같은 이름의 폴더가 이미 있습니다 — 저장 검토에서 파일 충돌을 확인합니다" : "";
  }catch(e){ el("up-msg").textContent = e.message; }
}

async function addFiles(files, pool){
  upMsg(pool, `${files.length}장 올리는 중…`);
  try{
    await ensureSession();
    const fd = new FormData();
    for(const f of files) fd.append("files", f);
    const r = await api(`/api/photos/${SESSION.session_id}?pool=${pool}`,
                        {method:"POST", body: fd});
    PHOTOS = r.photos;
    upMsg(pool, "");
    drawStaged();
    pushNames();
  }catch(e){ upMsg(pool, e.message); }
}

function upMsg(pool, text){
  const n = el(pool === "ref" ? "n-ref" : "n-cur");
  if(n && text) n.textContent = text;
}

async function dropStaged(pid){
  try{
    const r = await api(`/api/photos/${SESSION.session_id}/${pid}`, {method:"DELETE"});
    PHOTOS = r.photos; drawStaged();
  }catch(e){ /* 이미 없는 사진 — 다시 그린다 */ drawStaged(); }
}

function drawStaged(){
  for(const pool of ["ref", "cur"]){
    const list = PHOTOS.filter(p => p.pool === pool);
    const box = el("thumbs-" + pool), empty = el(`dz-${pool}-empty`);
    if(!box) continue;
    if(empty) empty.hidden = list.length > 0;
    el("dz-" + pool).classList.toggle("filled", list.length > 0);
    box.innerHTML = list.map(p =>
      `<figure class="th"><img src="${p.card || p.thumb}" alt="" loading="lazy">` +
      `<button class="x" data-pid="${p.id}" title="빼기">×</button></figure>`).join("") +
      (list.length ? `<button class="th add" data-pool="${pool}" title="사진 더 추가">＋</button>` : "");
    for(const b of box.querySelectorAll(".x"))
      b.onclick = e => { e.stopPropagation(); dropStaged(b.dataset.pid); };
    const add = box.querySelector(".th.add");
    if(add) add.onclick = e => { e.stopPropagation(); el("file-" + pool).click(); };
    const n = el("n-" + pool); if(n) n.textContent = list.length ? `${list.length}장` : "";
  }
  const nRef = PHOTOS.filter(p => p.pool === "ref").length;
  const nCur = PHOTOS.filter(p => p.pool === "cur").length;
  el("setup-tip").innerHTML = !nCur ? "" : nRef
    ? `기준 사진이 있어 <b>재진</b>으로 진행합니다 — 양쪽 모두 자동 분류한 뒤 짝을 맞춰 정합합니다`
    : `기준 사진이 없어 <b>초진</b>으로 진행합니다 — 자동 분류 후 바로 검수로 갑니다`;
  updateGo();
  setStep("setup", nCur ? "done" : "", UP.folder || "");
  setStep("pre", "", nCur ? `${nCur}장 대기` : "대기");
  renderVisitBadges();
}

function updateGo(){
  // 폴더 이름이 없어도 버튼은 눌리게 둔다 — 왜 안 되는지는 눌렀을 때 말해 준다.
  // 회색으로 죽어 있으면 사람이 이유를 찾아 헤맨다.
  const nCur = PHOTOS.filter(p => p.pool === "cur").length;
  const go = el("btn-go");
  go.disabled = !nCur;
  go.title = !nCur ? "현재 사진을 넣어 주세요" : "";
}

/* 폴더 이름 없이 진행하려 하면 — 입력칸을 짚어 준다 */
function needFolderName(){
  const inp = el("up-folder"), msg = el("up-msg");
  msg.textContent = "저장 폴더 이름을 작성해주세요";
  inp.classList.add("attn");
  inp.focus();
}

function bindUpload(){
  const folder = el("up-folder"), pfxOn = el("up-pfx-on"), pfx = el("up-prefix");
  folder.oninput = () => {
    UP.folder = folder.value.trim();
    if(UP.folder){                       // 입력이 시작되면 경고를 걷는다
      folder.classList.remove("attn");
      if(el("up-msg").textContent === "저장 폴더 이름을 작성해주세요")
        el("up-msg").textContent = "";
    }
    if(!UP.pfxCustom) pfx.placeholder = UP.folder || "폴더 이름과 동일";
    queueNames(); updateGo();
  };
  pfxOn.onchange = () => {
    UP.pfxCustom = pfxOn.checked;
    pfx.disabled = !UP.pfxCustom;
    if(UP.pfxCustom){ pfx.value = UP.prefix || UP.folder; UP.prefix = pfx.value; pfx.focus(); }
    queueNames();
  };
  pfx.oninput = () => { UP.prefix = pfx.value.trim(); queueNames(); };

  for(const pool of ["ref", "cur"]){
    const dz = el("dz-" + pool), fi = el("file-" + pool);
    const pickBtn = el("pick-" + pool);
    if(pickBtn) pickBtn.onclick = e => { e.stopPropagation(); fi.click(); };
    dz.onclick = () => { UP.lastZone = pool; fi.click(); };
    fi.onchange = () => { if(fi.files.length) addFiles([...fi.files], pool); fi.value = ""; };
    for(const ev of ["dragover","dragenter"])
      dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add("over"); UP.lastZone = pool; });
    for(const ev of ["dragleave","drop"])
      dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove("over"); });
    dz.addEventListener("drop", e => {
      const files = [...e.dataTransfer.files].filter(f => f.type.startsWith("image/"));
      if(files.length) addFiles(files, pool);
    });
  }
  el("btn-go").onclick = async () => {
    if(!UP.folder){ needFolderName(); return; }
    await pushNames();
    runClassify();
  };
}

addEventListener("paste", e => {
  if(VIEW !== "setup") return;
  const files = [...(e.clipboardData?.files || [])].filter(f => f.type.startsWith("image/"));
  if(files.length){ e.preventDefault(); addFiles(files, UP.lastZone); }
});

/* ══ 정합 → 검수 ═════════════════════════════════════════════════════════ */
/* 슬롯별 병렬 정합의 라벨별 진행 — 서버 progress 를 폴링해 그린다. */
const REG_STATE = {
  wait:     ["대기", ""],
  refs:     ["기준영상 준비 중", "busy"],
  run:      ["정합 중…", "busy"],
  reg:      ["✓ 정합됨", "ok"],
  frame:    ["✓ 자동 구도", "ok"],
  fallback: ["⚠ 정합 실패 — 자동 구도", "warn"],
};
function regLabel(key){
  if(key.startsWith("FACE:")){
    const list = (REVIEW && REVIEW.bins && REVIEW.bins.FACE) || [];
    const i = list.findIndex(p => p.id === key.slice(5));
    return `FACE${i >= 0 ? "_" + (i + 1) : ""}`;
  }
  const r = CAT_ROWS.find(x => x.slot === key);
  return r ? r.label : key;      // IO_FRONT 식 카테고리 라벨
}
function renderRegProgress(progress){
  const box = el("reg-progress"); if(!box) return;
  const keys = Object.keys(progress);
  box.hidden = !keys.length;
  if(!keys.length) return;
  const order = [...SLOTS.map(s => s.key), ...keys.filter(k => k.startsWith("FACE:"))];
  box.innerHTML = order.filter(k => progress[k]).map(k => {
    const [txt, kind] = REG_STATE[progress[k]] || [progress[k], ""];
    return `<span class="regitem" data-kind="${kind}"><b>${regLabel(k)}</b> ${txt}</span>`;
  }).join("");
}

el("btn-toproc").onclick = async () => {
  const b = el("btn-toproc"), label = b.textContent;
  b.disabled = true;
  b.textContent = REVIEW && REVIEW.mode === "revisit" ? "정합 중… (슬롯별 병렬)" : "구도 잡는 중…";
  const poll = setInterval(async () => {
    const st = await api(`/api/register/${SESSION.session_id}/status`).catch(() => null);
    if(st) renderRegProgress(st.progress);
  }, 400);
  try{
    const r = await post(`/api/register/${SESSION.session_id}`, {});
    REVIEW = r.review; PHOTOS = r.photos;
  }catch(e){
    clearInterval(poll);
    preMsg(`정합에 실패했습니다: ${e.message}`, "err");
    b.disabled = false; b.textContent = label;
    return;
  }
  clearInterval(poll);
  const st = await api(`/api/register/${SESSION.session_id}/status`).catch(() => null);
  if(st) renderRegProgress(st.progress);   // 최종 상태(✓/⚠)를 한 번 보여주고 넘어간다
  b.disabled = false; b.textContent = label;
  await loadRefList();
  renderVisitBadges();
  setStep("pre", "done", "");
  setStep("proc", "", "검수 중");
  showView("proc");
  TAB = "io";
  buildTabs();
  showTab("io");
};

/* ══ 저장 검토 · 확정 ═════════════════════════════════════════════════════ */
const FIN = {overwrite: new Set(), plan: null};

async function loadPlan(){
  const body = el("fin-body"), err = el("fin-err"), btn = el("btn-commit");
  err.textContent = ""; btn.disabled = true;
  if(!SESSION){ body.innerHTML = `<div class="ph">세션이 없습니다</div>`; return; }
  body.innerHTML = `<div class="ph">불러오는 중…</div>`;
  try{
    await pushNames();
    const p = await api(`/api/plan/${SESSION.session_id}`);
    FIN.plan = p;
    renderVisitBadges();
    renderPlan();
    btn.disabled = false;
  }catch(e){
    body.innerHTML = `<div class="ph">불러오지 못했습니다</div>`;
    err.textContent = e.message;
  }
}

const catNm = c => c;      // 파일명 별칭과 별개로, 검토 화면은 카테고리 원문을 보인다

function renderPlan(){
  const p = FIN.plan; if(!p) return;
  const body = el("fin-body"), err = el("fin-err");
  const items = p.files.map((f, i) => {
    const name = FIN.overwrite.has(f.base) ? f.base : f.file;
    const seg = f.exists
      ? `<span class="seg finseg" data-i="${i}">` +
        `<button data-w="0" aria-pressed="${!FIN.overwrite.has(f.base)}">번호 붙임</button>` +
        `<button data-w="1" aria-pressed="${FIN.overwrite.has(f.base)}">덮어쓰기</button></span>`
      : "";
    return `<li${f.extra ? ` class="sub"` : ""}>` +
      `<span class="k">${catNm(f.category)}${f.extra ? " 여분" : ""}</span>` +
      `<code>${esc(name)}</code>` +
      (f.exists ? `<span class="aux warn">같은 이름 있음</span>` : "") + seg +
      (f.raw ? `<span class="aux">원본 → ${esc(f.raw)}</span>` : "") +
      `</li>`;
  });
  for(const m of p.missing)
    items.push(`<li class="miss"><span class="k">${catNm(m)}</span>비어 있음</li>`);
  body.innerHTML =
      `<div class="finsec"><span class="eyebrow">저장 위치</span><code>${esc(p.dir)}</code></div>`
    + `<div class="finsec"><span class="eyebrow">구성</span>`
    + `<span class="aux">${p.mode === "revisit" ? "재진 (정합됨)" : "초진"} · ${p.format.toUpperCase()} ${p.px_per_cm}px/cm`
    + `${p.save_raw ? " · 원본 함께 저장" : ""}</span></div>`
    + `<ul class="finlist">${items.join("")}</ul>`;
  if(p.missing.length)
    err.textContent = `빈 카테고리 ${p.missing.length}곳 — ${p.missing.join(", ")}. `
                    + `채우고 오거나, 이대로 확정할 수 있습니다.`;
  for(const seg of body.querySelectorAll(".finseg")){
    const f = p.files[+seg.dataset.i];
    for(const b of seg.children)
      b.onclick = () => {
        if(b.dataset.w === "1") FIN.overwrite.add(f.base);
        else FIN.overwrite.delete(f.base);
        renderPlan();
      };
  }
}

el("btn-tofin").onclick = () => { showView("fin"); loadPlan(); };

el("btn-commit").onclick = async () => {
  const err = el("fin-err"), btn = el("btn-commit");
  err.textContent = ""; btn.disabled = true; btn.textContent = "저장 중…";
  const body = {overwrite: [...FIN.overwrite]};
  try{
    let r;
    try{
      r = await post(`/api/commit/${SESSION.session_id}`, body);
    }catch(e){
      // 빈 카테고리는 막지 않는다 — 사진이 없는 날도 있다. 대신 반드시 되묻는다.
      if(e.status === 409 && e.data?.error === "missing_slots"){
        const nm = (e.data.missing || []).join(", ");
        if(!confirm(`빈 카테고리가 있습니다 — ${nm}\n\n이대로 저장할까요?`)){
          btn.textContent = "확정 저장"; btn.disabled = false; return;
        }
        r = await api(`/api/commit/${SESSION.session_id}?allow_missing=true`,
          {method:"POST", headers:{"Content-Type":"application/json"},
           body: JSON.stringify(body)});
      }else throw e;
    }
    el("fin-body").innerHTML =
        `<div class="finsec"><span class="eyebrow">저장 완료</span><code>${esc(r.dir)}</code></div>`
      + `<ul class="finlist">${(r.files || []).map(f => `<li><code>${esc(f)}</code></li>`).join("")}</ul>`
      + `<p class="row" style="gap:8px;margin-top:12px">`
      + `<button class="btn" id="btn-open-dir">폴더 열기</button>`
      + `<button class="btn primary" id="btn-next">새 작업 시작 ▶</button></p>`;
    el("btn-open-dir").onclick = () => post("/api/open", {path: r.dir}).catch(() => {});
    el("btn-next").onclick = startNext;
    el("fin-visit").hidden = false;
    el("fin-visit").dataset.tone = "done";
    el("fin-visit").textContent = "저장됨";
    setStep("fin", "done", "완료");
    SESSION = null;               // 서버가 세션을 정리했다
    btn.textContent = "확정 저장";
    if(r.after && r.after.auto_next) setTimeout(startNext, 1200);
  }catch(e){
    err.textContent = e.message || "저장 실패";
    btn.textContent = "확정 저장"; btn.disabled = false;
  }
};

function startNext(){
  resetSession();
  showView("setup");
}

/* 저장이 끝났거나 위치를 바꿨다 — 화면을 처음 상태로 */
function resetSession(){
  if(SESSION) api(`/api/session/${SESSION.session_id}`, {method:"DELETE"}).catch(() => {});
  SESSION = null;
  PHOTOS = []; REVIEW = null; ED.slot = null; TAB = "io";
  FIN.overwrite.clear(); FIN.plan = null;
  OV.on = false; OV.img = null; OV.list = {}; OV.board = {};
  boardEl.innerHTML = ""; segEl.innerHTML = "";
  exitZoom();
  PEEK.on = false; clearTimeout(PEEK.timer);
  GRID.on = false;      // Tab 대보기와 같은 규칙 — 새 작업은 깨끗한 화면에서
  UP.folder = ""; UP.prefix = ""; UP.pfxCustom = false;
  const f = el("up-folder"), x = el("up-prefix"), c = el("up-pfx-on");
  if(f) f.value = "";
  if(x){ x.value = ""; x.disabled = true; x.placeholder = "폴더 이름과 동일"; }
  if(c) c.checked = false;
  el("up-msg").textContent = "";
  el("pchip").hidden = true; el("pchip").innerHTML = "";
  for(const url of [...imgCache.keys()])
    if(/^\/api\/(thumb|reference)\//.test(url)) imgCache.delete(url);
  drawStaged();
  setStep("setup", "", "");
  ["pre","proc","fin"].forEach(v => setStep(v, "", "대기"));
  renderVisitBadges();
}

function setStep(v, state, st){
  const n = document.querySelector(`.nav[data-view="${v}"]`);
  if(!n) return;
  n.dataset.state = state;
  n.querySelector(".st").textContent = st;
}

/* 초진/재진 배지 — 기준 사진 풀이 비었으면 초진이다. */
function renderVisitBadges(){
  const nRef = PHOTOS.filter(p => p.pool === "ref").length;
  const has = PHOTOS.length > 0;
  const revisit = REVIEW ? REVIEW.mode === "revisit" : nRef > 0;
  for(const id of ["pre-visit","proc-visit","fin-visit"]){
    const n = el(id); if(!n) continue;
    if(!has){ n.hidden = true; n.textContent = ""; n.removeAttribute("data-tone"); continue; }
    n.hidden = false;
    n.dataset.tone = revisit ? "revisit" : "first";
    n.textContent = revisit ? `재진 · 기준 ${nRef}장` : "초진";
  }
  const chip = el("pchip");
  if(UP.folder){
    chip.innerHTML = `<span class="nm">${esc(UP.folder)}</span>` +
      `<span class="visit">${revisit ? "재진" : "초진"}</span>`;
    chip.hidden = false;
  }else chip.hidden = true;
}

/* ══ 저장 위치 (roots) ════════════════════════════════════════════════════ */
async function drawRootPicker(path, host){
  const d = host;
  d.innerHTML = `<p class="empty">폴더를 읽는 중…</p>`;
  let r;
  try{ r = await api("/api/fs" + (path ? "?path=" + encodeURIComponent(path) : "")); }
  catch(e){ d.innerHTML = `<p class="err">${esc(e.message)}</p>` +
    `<p style="margin-top:12px"><button class="btn" id="pk-cancel">돌아가기</button></p>`;
    el("pk-cancel").onclick = () => closePicker(host); return; }

  d.innerHTML =
    `<div class="who">저장 위치 고르기</div>` +
    `<div class="no">저장 폴더들이 들어 있는(또는 들어갈) 폴더를 고르세요</div>` +
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
      const res = await post("/api/fs/mkdir", {path: r.path, name});
      drawRootPicker(res.path, host);
    }catch(e){ el("pk-err").textContent = e.message; }
  };
  el("pk-up").onclick = () => r.parent && drawRootPicker(r.parent, host);
  el("pk-cancel").onclick = () => closePicker(host);
  el("pk-ok").onclick = async () => {
    try{
      const res = await post("/api/root", {path: r.path});
      closePicker(host);
      await showRoot(res.root, res.roots);
    }catch(e){ el("pk-err").textContent = e.message; }
  };
}

function closePicker(host){
  if(host){ host.hidden = true; host.innerHTML = ""; }
}
let ROOTS = [];

function drawRoots(list, current){
  if(list) ROOTS = list;
  const cur = current || (ROOTS.find(r => r.current) || {}).path || "";
  for(const r of ROOTS) r.current = (r.path === cur);
  for(const id of ["root-sel", "set-root-sel"]){
    const sel = el(id); if(!sel) continue;
    sel.innerHTML = "";
    for(const r of ROOTS){
      const op = document.createElement("option");
      op.value = r.path;
      op.textContent = r.path + (r.exists ? "" : "  · 연결 안 됨");
      op.disabled = !r.exists && !r.current;
      sel.appendChild(op);
    }
    sel.value = cur;
    sel.title = cur;
  }
}

async function loadRoots(current){
  const d = await api("/api/roots").catch(() => null);
  if(d) drawRoots(d.roots, current || d.current);
}

async function showRoot(root, roots){
  if(HEALTH) HEALTH.root = root;
  drawRoots(roots, root);
  el("dlg-set").close();
  resetSession();
  loadMaint();
}

async function selectRoot(path){
  const now = (ROOTS.find(r => r.current) || {}).path || "";
  if(!path || path === now) return;
  if(PHOTOS.length &&
     !confirm(`담아둔 사진 ${PHOTOS.length}장이 사라집니다.\n저장 위치를 바꿀까요?`)){
    drawRoots();
    return;
  }
  try{
    const r = await post("/api/root/select", {path});
    await showRoot(r.root, r.roots);
  }catch(e){
    alert("그 위치로 바꾸지 못했습니다: " + (e.message || e));
    drawRoots();
  }
}
for(const id of ["root-sel", "set-root-sel"])
  el(id).onchange = e => selectRoot(e.target.value);

el("set-forget").onclick = async () => {
  const path = el("set-root-sel").value;
  if(!path) return;
  if(!confirm("목록에서만 뺍니다 — 폴더와 그 안의 자료는 그대로 남습니다.\n\n" + path))
    return;
  try{
    const r = await post("/api/root/forget", {path});
    drawRoots(r.roots);
  }catch(e){ alert(e.message || e); }
};

async function addRoot(){
  const got = await pickFolder(HEALTH && HEALTH.root);
  if(got === null) return;
  if(got === undefined){
    openSettings();
    const host = el("set-picker"); host.hidden = false; drawRootPicker("", host);
    return;
  }
  try{
    const r = await post("/api/root", {path: got});
    await showRoot(r.root, r.roots);
  }catch(e){ alert("그 위치를 쓸 수 없습니다: " + (e.message || e)); }
}
el("btn-root").onclick = addRoot;
el("set-change").onclick = addRoot;

async function pickFolder(start){
  const r = await api("/api/pick-folder?start=" + encodeURIComponent(start || ""))
                  .catch(() => null);
  if(r && r.ok) return r.path;
  if(r && r.cancelled) return null;
  return undefined;
}

/* ══ 설정 ═════════════════════════════════════════════════════════════════ */
function openSettings(){
  syncThemeSeg();
  closePicker(el("set-picker"));
  el("dlg-set").showModal();
  syncPrefs();
  loadMaint();
}
el("btn-set").onclick = openSettings;
el("set-close").onclick = () => el("dlg-set").close();

const setPref = body => post("/api/prefs", body).catch(() => null);

async function syncPrefs(){
  const r = await api("/api/prefs").catch(() => null);
  if(!r) return;
  PREFS = r;
  if(r.letterbox_color) LETTERBOX = "#" + r.letterbox_color;

  // 반전 기본값 6×2 그리드
  const grid = el("flip-grid");
  grid.innerHTML = `<tr><th></th><th>기준 사진</th><th>현재 사진</th></tr>` +
    r.classes.map(c =>
      `<tr><td>${c}</td>` +
      ["ref", "cur"].map(pool =>
        `<td><input type="checkbox" data-pool="${pool}" data-cat="${c}"` +
        `${r.flip_defaults[pool] && r.flip_defaults[pool][c] ? " checked" : ""}></td>`).join("") +
      `</tr>`).join("");
  for(const cb of grid.querySelectorAll("input")){
    cb.onchange = async () => {
      const out = {ref: {}, cur: {}};
      for(const x of grid.querySelectorAll("input"))
        out[x.dataset.pool][x.dataset.cat] = x.checked;
      await setPref({flip_defaults: out});
    };
  }

  // 파일 이름
  const nm = r.naming;
  for(const b of el("set-nummode").children){
    b.setAttribute("aria-pressed", b.dataset.m === nm.number_mode);
    b.onclick = async () => { await setPref({naming: {number_mode: b.dataset.m}}); syncPrefs(); };
  }
  for(const b of el("set-numstart").children){
    b.setAttribute("aria-pressed", +b.dataset.s === nm.start);
    b.onclick = async () => { await setPref({naming: {start: +b.dataset.s}}); syncPrefs(); };
  }
  const sep = el("set-numsep");
  sep.value = nm.separator;
  sep.onchange = async () => {
    if(!sep.value) { sep.value = nm.separator; return; }
    await setPref({naming: {separator: sep.value}}); syncPrefs();
  };
  const ag = el("alias-grid");
  ag.innerHTML = r.classes.map(c =>
    `<label>${c}<input data-cat="${c}" value="${esc(nm.aliases[c] || c)}"></label>`).join("");
  for(const inp of ag.querySelectorAll("input")){
    inp.onchange = async () => {
      const v = inp.value.trim();
      if(!v){ inp.value = nm.aliases[inp.dataset.cat]; return; }
      const p = await setPref({naming: {aliases: {[inp.dataset.cat]: v}}});
      if(!p) inp.value = nm.aliases[inp.dataset.cat];
      syncPrefs();
    };
  }
  const px = UP.folder || "홍길동";
  const n1 = nm.number_mode === "always" ? nm.separator + nm.start : "";
  el("name-preview").textContent =
    `${px}_${nm.aliases.IO_FRONT}${n1}.${r.output.format} · ` +
    `${px}_${nm.aliases.FACE}${nm.separator}${nm.start}.${r.output.format}, ` +
    `${px}_${nm.aliases.FACE}${nm.separator}${nm.start + 1}.${r.output.format} …`;

  // 출력
  const ppcm = el("set-ppcm");
  ppcm.value = r.output.px_per_cm;
  ppcm.onchange = async () => {
    const v = parseFloat(ppcm.value);
    if(!(v >= 50 && v <= 400)){ ppcm.value = r.output.px_per_cm; return; }
    await setPref({output: {px_per_cm: v}});
  };
  for(const b of el("set-format").children){
    b.setAttribute("aria-pressed", b.dataset.f === r.output.format);
    b.onclick = async () => { await setPref({output: {format: b.dataset.f}}); syncPrefs(); };
  }
  const q = el("set-quality");
  q.value = r.output.jpeg_quality;
  q.onchange = async () => {
    const v = parseInt(q.value, 10);
    if(!(v >= 60 && v <= 100)){ q.value = r.output.jpeg_quality; return; }
    await setPref({output: {jpeg_quality: v}});
  };
  for(const b of el("set-ioratio").children){
    b.setAttribute("aria-pressed", b.dataset.r === r.output.io_ratio);
    b.onclick = async () => { await setPref({output: {io_ratio: b.dataset.r}}); syncPrefs(); };
  }
  for(const b of el("set-faceratio").children){
    b.setAttribute("aria-pressed", b.dataset.r === r.output.face_ratio);
    b.onclick = async () => { await setPref({output: {face_ratio: b.dataset.r}}); syncPrefs(); };
  }
  el("set-flipsave").checked = r.output.flip_save;
  el("set-flipsave").onchange = e => setPref({output: {flip_save: e.target.checked}});
  el("set-extras").checked = r.output.save_extras;
  el("set-extras").onchange = e => setPref({output: {save_extras: e.target.checked}});
  el("set-raw").checked = r.save_raw;
  el("set-raw").onchange = e => setPref({save_raw: e.target.checked});
  for(const b of el("set-letterbox").children){
    b.setAttribute("aria-pressed", b.dataset.c === r.letterbox_color);
    b.onclick = async () => {
      const p = await setPref({letterbox_color: b.dataset.c});
      if(p && p.letterbox_color){
        LETTERBOX = "#" + p.letterbox_color;
        syncPrefs(); renderEditor(); redrawBoardSlots();
      }
    };
  }

  // 저장 후 동작
  el("set-openfolder").checked = r.after_save.open_folder;
  el("set-openfolder").onchange = e => setPref({after_save: {open_folder: e.target.checked}});
  el("set-autonext").checked = r.after_save.auto_next;
  el("set-autonext").onchange = e => setPref({after_save: {auto_next: e.target.checked}});
}

function syncThemeSeg(){
  const cur = document.documentElement.dataset.theme;
  for(const b of el("set-theme").children) b.setAttribute("aria-pressed", b.dataset.t === cur);
}
function setTheme(t){
  document.documentElement.dataset.theme = t;
  try{ localStorage.setItem("crocs-theme", t); }catch(e){}
  syncThemeSeg();
}
for(const b of el("set-theme").children) b.onclick = () => setTheme(b.dataset.t);

/* ══ 유지관리 ═════════════════════════════════════════════════════════════ */
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
}

el("btn-shortcut").onclick = async () => {
  const r = await api("/api/shortcut", {method:"POST"})
    .catch(e => ({ok: false, detail: e.message}));
  alert(r.ok ? `바로가기를 만들었습니다: ${r.desktop}\\CRoCs Fastest Lap.lnk`
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
    `&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;바탕화면 바로가기<br>` +
    `남음 &nbsp;— 저장된 사진 <b>${v.data_dir}</b> (${MB(v.data_bytes)})`;
  const opts = v.tool_options || [];
  const box = el("uninst-tools-row");
  box.hidden = !opts.length;
  box.innerHTML = opts.map(t =>
    `<label class="row" style="margin-top:4px">` +
    `<input type="checkbox" class="uninst-tool" value="${esc(t.id)}"${t.ours ? " checked" : ""}>` +
    `<span>${esc(t.name)} 도 제거` +
    (t.ours ? ` <span class="aux">— 이 프로그램이 설치했습니다</span>`
            : ` <span class="aux">— 원래 있던 것일 수 있습니다</span>`) +
    `</span></label>`).join("");
  el("uninst").hidden = false;
};

el("btn-uninstall-cancel").onclick = () => { el("uninst").hidden = true; };

el("btn-uninstall-go").onclick = async () => {
  const tools = [...document.querySelectorAll(".uninst-tool:checked")]
                  .map(x => x.value);
  const names = [...document.querySelectorAll(".uninst-tool:checked")]
                  .map(x => x.parentElement.textContent.split(" 도 제거")[0].trim());
  const body = {drop_tools: tools};
  if(!confirm("프로그램을 지웁니다. 저장된 사진은 남습니다." +
              (tools.length ? `\n${names.join(" · ")} 도 제거합니다 — 다른 프로그램이 ` +
                              "쓰고 있다면 그쪽이 동작하지 않게 됩니다." : "") +
              "\n\n계속할까요?")) return;
  const r = await post("/api/uninstall/prepare", body).catch(() => null);
  if(!r || !r.ok){ alert((r && r.detail) || "실패했습니다"); return; }
  alert(r.detail + "\n\n앱이 곧 종료됩니다.");
};

/* ══ 문서 · 배너 · 업데이트 ═══════════════════════════════════════════════ */
const DOCS = {
  log: {title: "업데이트 로그", src: "/static/changelog.json"},
};

/* 본문의 **아주 작은** 마크다운만 그린다 — `` `코드` `` 와 `**굵게**`.

   본문은 JSON 에 든 글이라 innerHTML 로 쓰면 그 글이 그대로 화면 요소가 된다.
   그래서 문자열을 만들지 않고 **노드로 쌓는다**.

   왼쪽부터 한 글자씩 훑는다. 표기를 따로따로 훑으면 실제 데이터에서 깨진다:

   ① **굵게가 코드를 감싼다** — `**` + 백틱 + `**`. 코드를 먼저 떼어내면 양옆의
      별표가 고아가 되어 한참 뒤의 별표와 잘못 짝짓는다(문단이 통째로 굵어졌다).
      그래서 굵게 **안쪽을 다시 이 함수로** 그린다.
   ② **굵게 안에 별표가 있다** — `**PowerPoint 문서(*.pptx)**`. 닫는 표기를
      `indexOf` 로 찾으므로 홑별표는 그냥 지나간다.

   짝을 못 찾은 표기는 글자 그대로 둔다. 이 제품에는 `**(구분자까지 통째로)`
   라는 **블록 이름 자체가 별 두 개**라(F&Q 의 이름 양식 설명), 함부로 먹으면
   안내가 거짓이 된다. 줄을 넘어가서 짝짓지도 않는다. */
function inlineMd(text, parent){
  let i = 0, buf = "";
  const flush = () => {
    if(buf){ parent.appendChild(document.createTextNode(buf)); buf = ""; }
  };
  const closes = (open, from) => {           // 같은 줄 안에서만 닫는다
    const end = text.indexOf(open, from);
    const nl = text.indexOf("\n", from);
    return (end > from && (nl === -1 || end < nl)) ? end : -1;
  };
  while(i < text.length){
    if(text[i] === "`"){
      const end = closes("`", i + 1);
      if(end !== -1){
        flush();
        const c = document.createElement("code");
        c.textContent = text.slice(i + 1, end);
        parent.appendChild(c);
        i = end + 1; continue;
      }
    }
    if(text.startsWith("**", i)){
      const end = closes("**", i + 2);
      if(end !== -1){
        flush();
        const b = document.createElement("strong");
        inlineMd(text.slice(i + 2, end), b);   // 안쪽의 `코드` 도 그린다
        parent.appendChild(b);
        i = end + 2; continue;
      }
    }
    buf += text[i++];
  }
  flush();
}

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
    bd.className = "body";
    (it.body || "").split("```").forEach((seg, i) => {
      if(i % 2){
        const pre = document.createElement("pre");
        pre.className = "mono";
        pre.textContent = seg.replace(/^\n|\n$/g, "");
        bd.appendChild(pre);
      }else if(seg){
        inlineMd(seg, bd);
      }
    });
    dt.append(sm, bd);
    box.appendChild(dt);
  }
  if(!items.length) box.textContent = "아직 항목이 없습니다";
}

el("btn-log").onclick = () => openDoc("log");
el("doc-close").onclick = () => el("dlg-doc").close();
el("btn-fb").onclick = () =>
  window.open("https://forms.gle/k8MRUas5LwGAxFnB9", "_blank", "noopener");

function banner(kind, html){
  const b = el("banner");
  b.className = "banner " + kind;
  b.innerHTML = html;
  b.hidden = false;
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

async function checkUpdate(manual){
  const u = await api("/api/update/check").catch(() => null);
  if(!u || !u.ok){
    const why = (u && u.reason) || "서버가 응답하지 않습니다";
    if(manual || !/개발용 설치본/.test(why))
      banner("warn", `<b>업데이트를 확인하지 못했습니다</b>
                      <span class="grow">${why}</span>`);
    return;
  }
  if(!u.has_update){
    if(manual)
      banner("info", `<b>최신 버전입니다</b>` +
        `<span class="grow">${u.app_from ? "v" + u.app_from : ""}</span>`);
    return;
  }
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
  if(!u.blocked){
    const bn = el("banner");
    bn.style.cursor = "pointer";
    bn.title = "눌러서 업데이트";
    bn.onclick = doUpdate;
  }
}

async function doUpdate(force){
  if(doUpdate.busy) return;
  doUpdate.busy = true;
  const b = el("btn-upd") || el("btn-upd-force");
  if(b){ b.disabled = true; b.textContent = "받는 중..."; }
  const r = await post("/api/update/apply", {force: !!force}).catch(() => null);
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

el("btn-upd-check").onclick = async () => {
  const b = el("btn-upd-check"), t = b.textContent;
  b.disabled = true; b.textContent = "확인 중…";
  try{ await checkUpdate(true); }
  finally{ b.disabled = false; b.textContent = t; }
};

/* ══ 첫 실행 · 저장 위치 확인 ═════════════════════════════════════════════ */
function firstRun(h){
  el("first-path").value = h.root || "";
  el("dlg-first").showModal();
}

function rootMissing(h){
  el("first-title").textContent = "저장 위치를 찾을 수 없습니다";
  el("first-sub").innerHTML =
    `설정에 적힌 위치에 닿지 못했습니다 — <span class="ident">${esc(h.root_missing)}</span>`;
  el("first-path").value = h.root_missing;
  el("first-tips").innerHTML =
    `<li>외장 드라이브라면 <b>연결한 뒤 [다시 확인]</b> 을 눌러 주세요</li>` +
    `<li>드라이브 문자가 바뀌었으면 <b>변경</b> 으로 새 경로를 고르세요</li>` +
    `<li>지금은 임시로 <span class="ident">${esc(h.root)}</span> 를 보고 있습니다 — ` +
    `여기서 확정하지 않으면 원래 위치는 그대로 남습니다</li>`;
  el("first-recheck").hidden = false;
  el("first-ok").textContent = "이 위치로 바꾸기";
  el("dlg-first").showModal();
}

el("first-recheck").onclick = async () => {
  const b = el("first-recheck");
  b.disabled = true; b.textContent = "확인 중…";
  const r = await api("/api/root/recheck", {method: "POST"}).catch(() => null);
  b.disabled = false; b.textContent = "다시 확인";
  if(r && r.ok){
    if(HEALTH) { HEALTH.root = r.root; HEALTH.root_missing = ""; }
    el("dlg-first").close();
    loadRoots(r.root);
    return;
  }
  el("first-sub").innerHTML =
    `아직 닿지 못했습니다 — <span class="ident">${esc((r && r.path) || "")}</span><br>` +
    `연결을 확인한 뒤 다시 눌러 주세요.`;
};

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
    const r = await post("/api/root", {path: p});
    if(HEALTH) HEALTH.root = r.root;
    el("dlg-first").close();
    loadRoots(r.root);
  }catch(e){ alert("그 위치를 쓸 수 없습니다: " + (e.message || e)); }
};

/* ══ 화면 전환 · 초기화 ═══════════════════════════════════════════════════ */
function showView(v){
  VIEW = v;
  document.querySelectorAll(".view").forEach(x => x.classList.toggle("on", x.dataset.view === v));
  document.querySelectorAll(".nav").forEach(n =>
    n.dataset.view === v ? n.setAttribute("aria-current","page") : n.removeAttribute("aria-current"));
  renderVisitBadges();
}

syncThemeSeg();
bindEditor();
bindUpload();
drawStaged();
showView("setup");
loadRoots();
api("/api/health").then(h => {
  HEALTH = h;
  if(h.needs_setup) firstRun(h);
  else if(h.root_missing) rootMissing(h);
}).catch(() => {});
api("/api/prefs").then(p => {
  PREFS = p;
  if(p && p.letterbox_color) LETTERBOX = "#" + p.letterbox_color;
}).catch(() => {});
checkWeights();
setTimeout(checkUpdate, 3000);   // 네트워크를 쓰므로 첫 화면을 막지 않는다
