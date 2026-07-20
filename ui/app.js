/* ===========================================================================
   Plashker — фронтенд-логика. Вызывает Python-движок через
   window.pywebview.api.*; превью приходит как base64-PNG.
   =========================================================================== */

const api = () => window.pywebview && window.pywebview.api;

/* ---- определение ОС (п.5) ----------------------------------------------
   Нужно, чтобы: (а) в подсказках показывать ⌘ на macOS и Ctrl на Windows;
   (б) шорткаты ловились по ФИЗИЧЕСКОЙ клавише (e.code === "KeyS"), а не по
   символу (e.key), иначе на русской раскладке Windows Ctrl+S не срабатывает —
   физическая S там даёт «ы». */
const IS_MAC = (() => {
  const p = (navigator.userAgentData && navigator.userAgentData.platform) ||
            navigator.platform || navigator.userAgent || "";
  return /mac|iphone|ipad|ipod/i.test(p);
})();
const MOD_LABEL = IS_MAC ? "⌘" : "Ctrl";
const SHIFT_LABEL = IS_MAC ? "⇧" : "Shift";

/* Заменить в подсказках символы модификаторов на актуальные для ОС. */
function applyOSHotkeyLabels() {
  document.querySelectorAll("kbd.kbd-mod").forEach(k => { k.textContent = MOD_LABEL; });
  document.querySelectorAll("kbd.kbd-shift").forEach(k => { k.textContent = SHIFT_LABEL; });
  const saveBtn = document.getElementById("btn-save");
  if (saveBtn) saveBtn.title = `Сохранить проект (${MOD_LABEL}+S)`;
}

let STATE = null;
let CURRENT = null;
let BOUNDS = {};
let PXCTX = {};
let LAST_SETTINGS = null;
let POSITION_TARGET = "title";
const EDIT_BASELINES = {};
const FIELD_VALUES = {};
let sessionCalibrated = false;  // авто-пропагация: только первое касание
const previewCache = {};
const VIEW = {};                       // вид на формат: {z, panX, panY} (сессия)

/* Минималистичные SVG-иконки семейств форматов (п.11): уникальные и
   осмысленные — соцсети (узлы-репост), BYYD (рекламный баннер),
   Digital Alliance (дисплей-вещание). */
const FAMILY_ICON = {
  social: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
      stroke-linecap="round" stroke-linejoin="round">
      <circle cx="6" cy="12" r="2.4"/><circle cx="17.5" cy="6" r="2.4"/>
      <circle cx="17.5" cy="18" r="2.4"/>
      <path d="M8.1 10.9 15.4 7.1M8.1 13.1l7.3 3.8"/></svg>`,
  byyd: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
      stroke-linecap="round" stroke-linejoin="round">
      <rect x="3.5" y="5" width="17" height="11" rx="2"/>
      <path d="M7 9h7M7 12h4"/><path d="M12 16v3M9 19h6"/></svg>`,
  da: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
      stroke-linecap="round" stroke-linejoin="round">
      <rect x="4" y="6" width="16" height="10" rx="1.6"/>
      <path d="M9 20h6M12 16v4"/>
      <path d="M14.6 9.2a3 3 0 0 1 0 3.6M16.7 7.6a5.6 5.6 0 0 1 0 6.8"/></svg>`,
};

/* Иконки видимости карточки (п.10): минималистичный глаз / глаз перечёркнут. */
const EYE_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/>
    <circle cx="12" cy="12" r="2.6"/></svg>`;
const EYE_OFF_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <path d="M9.6 5.8A8.9 8.9 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a16 16 0 0 1-2.4 3.1"/>
    <path d="M6.2 7.3A15.8 15.8 0 0 0 2.5 12S6 18.5 12 18.5a8.7 8.7 0 0 0 3.6-.75"/>
    <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/><path d="M3.5 3.5l17 17"/></svg>`;
/* «Соло» — изолировать одну карточку (п.4): мишень-фокус. */
const SOLO_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="12" cy="12" r="7.5"/><circle cx="12" cy="12" r="2.4" fill="currentColor" stroke="none"/></svg>`;
const KINDS = ["title", "date", "rating"];

/* п.4 v0.8: определить «светлость» изображения и затемнить альфа-шахматку */
function adaptAlphaBg(imgEl, container) {
  if (!imgEl || !container) return;
  const onLoad = () => {
    try {
      const c = document.createElement("canvas");
      const sz = 96; c.width = sz; c.height = sz;
      const cx = c.getContext("2d", { willReadFrequently: true });
      cx.clearRect(0, 0, sz, sz);
      cx.drawImage(imgEl, 0, 0, sz, sz);
      const d = cx.getImageData(0, 0, sz, sz).data;
      let sum = 0, n = 0, minL = 255, maxL = 0;
      for (let i = 0; i < d.length; i += 4) {
        const a = d[i + 3];
        if (a < 18) continue;
        const lum = d[i] * 0.299 + d[i+1] * 0.587 + d[i+2] * 0.114;
        sum += lum; n++; minL = Math.min(minL, lum); maxL = Math.max(maxL, lum);
      }
      container.classList.remove("light-asset", "midlight-asset");
      container.style.removeProperty("--canvas-a");
      container.style.removeProperty("--canvas-b");
      if (n === 0) return;
      const lum = sum / n;
      const mostlyLight = lum > 170 || (lum > 145 && minL > 115);
      if (mostlyLight) {
        container.classList.add("light-asset");
        container.style.setProperty("--canvas-a", "#727272");
        container.style.setProperty("--canvas-b", "#5d5d5d");
      } else if (lum > 125) {
        container.classList.add("midlight-asset");
        container.style.setProperty("--canvas-a", "#a0a0a0");
        container.style.setProperty("--canvas-b", "#8a8a8a");
      }
    } catch(e) { /* ignore */ }
  };
  if (imgEl.complete) onLoad(); else imgEl.onload = onLoad;
}

/* ---- утилиты ------------------------------------------------------------- */
function $(sel, root = document) { return root.querySelector(sel); }
function $all(sel, root = document) { return [...root.querySelectorAll(sel)]; }

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 3500);
}
function flags() { return { withBg: $("#chk-bg").checked, withSafe: $("#chk-safe").checked, withAim: $("#chk-aim")?.checked || false }; }

/* ---- тема оформления (п.5/6) -------------------------------------------- */
function applyTheme(theme) {
  const normalized = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", normalized);
  $all(".theme-choice").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.themeChoice === normalized);
  });
}
async function loadTheme() {
  try { applyTheme(await api()?.get_theme?.() || "light"); }
  catch (e) { applyTheme("light"); }
}
async function setTheme(next) {
  next = next === "dark" ? "dark" : "light";
  applyTheme(next);
  try { await api()?.set_theme?.(next); } catch (e) { /* ignore */ }
}
async function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  await setTheme(cur === "dark" ? "light" : "dark");
}
function anyModalOpen() { return $all(".modal-backdrop.open").length > 0; }
function readFileAsDataURL(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result);
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}

async function makeVisibleThumbDataURL(dataUrl, maxSide = 260) {
  return new Promise(resolve => {
    const img = new Image();
    img.onload = () => {
      try {
        const c = document.createElement("canvas");
        c.width = img.naturalWidth; c.height = img.naturalHeight;
        const ctx = c.getContext("2d", { willReadFrequently: true });
        ctx.drawImage(img, 0, 0);
        const d = ctx.getImageData(0, 0, c.width, c.height).data;
        let minX = c.width, minY = c.height, maxX = -1, maxY = -1;
        for (let y = 0; y < c.height; y++) {
          for (let x = 0; x < c.width; x++) {
            if (d[(y * c.width + x) * 4 + 3] > 12) {
              if (x < minX) minX = x; if (x > maxX) maxX = x;
              if (y < minY) minY = y; if (y > maxY) maxY = y;
            }
          }
        }
        if (maxX < minX || maxY < minY) { resolve(dataUrl); return; }
        const w = maxX - minX + 1, h = maxY - minY + 1;
        const sideSrc = Math.max(w, h);
        const pad = Math.max(8, Math.round(sideSrc * 0.12));
        const side = sideSrc + pad * 2;
        const scale = Math.min(1, maxSide / side);
        const out = document.createElement("canvas");
        out.width = Math.max(1, Math.round(side * scale));
        out.height = Math.max(1, Math.round(side * scale));
        const octx = out.getContext("2d");
        octx.clearRect(0, 0, out.width, out.height);
        octx.drawImage(
          c, minX, minY, w, h,
          Math.round(((side - w) / 2) * scale),
          Math.round(((side - h) / 2) * scale),
          Math.round(w * scale),
          Math.round(h * scale)
        );
        resolve(out.toDataURL("image/png"));
      } catch (e) { resolve(dataUrl); }
    };
    img.onerror = () => resolve(dataUrl);
    img.src = dataUrl;
  });
}

/* ---- запуск ------------------------------------------------------------- */
window.addEventListener("pywebviewready", initBridge);
document.addEventListener("DOMContentLoaded", () => { wireStaticUI(); });
setTimeout(() => { if (!api()) standaloneNotice(); }, 900);

async function initBridge() {
  await loadTheme();
  $("#btn-demo").onclick = async () => {
    await withLoader("Открываю демо-проект…", async () => { await boot(await api().open_demo()); });
  };
  $("#btn-open").onclick = async () => {
    // диалог выбора файла открывается ДО лоадера (нативное окно поверх), а
    // распаковку и рендер уже накрываем оверлеем.
    const st = await api().open_project_dialog?.();
    if (st && st.loaded) await boot(st);
    else if (st === null || st === undefined) { /* отмена диалога — без шума */ }
  };
  $("#btn-new").onclick = openCreateScreen;
  await loadRecents();
}

function standaloneNotice() {
  if (api()) return;
  const msg = () => toast("Запустите приложение через python run.py — нужен мост pywebview");
  $("#btn-demo").onclick = msg; $("#btn-open").onclick = msg; $("#btn-new").onclick = msg;
  const welcomeSettings = $("#btn-app-settings-welcome"); if (welcomeSettings) welcomeSettings.onclick = msg;
}

/* ---- Мои проекты (недавние) --------------------------------------------- */
async function loadRecents() {
  if (!api()?.get_recent_projects) return;
  let items = [];
  try { items = await api().get_recent_projects(); } catch (e) { items = []; }
  const wrap = $("#recents"), list = $("#recents-list");
  list.innerHTML = "";
  if (!items || !items.length) { wrap.style.display = "none"; return; }
  wrap.style.display = "block";
  items.forEach(r => {
    const it = document.createElement("div"); it.className = "recent-item";
    const left = document.createElement("div");
    left.innerHTML = `<div class="r-title">${escapeHtml(r.title || "проект")}</div>` +
                     `<div class="r-path">${escapeHtml(r.path || "")}</div>`;
    const g = document.createElement("div"); g.className = "r-glyph";
    g.innerHTML = `<svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M3 4h14M3 4v12a2 2 0 002 2h10a2 2 0 002-2V4M7 4V2h6v2"/></svg>`;
    it.append(left, g);
    it.onclick = async () => {
      await withLoader("Открываю проект…", async () => {
        const st = await api().open_project(r.path);
        if (st && st.loaded) await boot(st); else toast("Файл не найден");
      });
      await loadRecents();
    };
    list.appendChild(it);
  });
}
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c =>
  ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c])); }

/* ===========================================================================
   ЭКРАН СОЗДАНИЯ ПРОЕКТА (импорт исходных элементов, drag&drop) — п.2
   =========================================================================== */
let createInited = false;
const imported = { title: false, date: false, rating: false, date_kz: false, date_now: false, rating_kz: false };

function openCreateScreen() {
  $("#welcome").style.display = "none";
  $("#create").style.display = "grid";
  $("#new-title").value = "";
  $("#create-note").textContent = "";
  imported.title = imported.date = imported.rating = imported.date_kz = imported.date_now = imported.rating_kz = false;
  projectCreated = false;
  Object.keys(importCache).forEach(k => delete importCache[k]);
  $all(".drop-card").forEach(resetDropCard);
  $("#create-enter").disabled = true;
  if (!createInited) { wireCreateScreen(); createInited = true; }
  setTimeout(() => $("#new-title").focus(), 50);
}

function resetDropCard(card) {
  card.classList.remove("filled", "drag");
  const st = $("[data-state]", card);
  st.textContent = "Перетащите PNG";
  const old = $(".drop-thumb", card); if (old) old.remove();
  const del = $(".drop-remove", card); if (del) del.remove();
}

function wireCreateScreen() {
  $("#create-back").onclick = () => {
    $("#create").style.display = "none";
    $("#welcome").style.display = "grid";
  };

  $all(".drop-card").forEach(card => {
    const kind = card.dataset.kind;
    const input = document.createElement("input");
    input.type = "file"; input.accept = "image/png"; input.multiple = true; input.style.display = "none";
    card.appendChild(input);
    card.onclick = () => input.click();
    input.onchange = () => { if (input.files?.length) enqueueImport(() => handleCreateFiles(kind, input.files, card)); };

    card.addEventListener("dragover", e => { e.preventDefault(); card.classList.add("drag"); });
    card.addEventListener("dragleave", () => card.classList.remove("drag"));
    card.addEventListener("drop", async e => {
      e.preventDefault(); e.stopPropagation(); card.classList.remove("drag");
      const files = e.dataTransfer.files;
      if (files && files.length) enqueueImport(() => handleCreateFiles(kind, files, card));
    });
  });

  wireFullscreenDrop();

  $("#create-enter").onclick = async () => {
    const title = $("#new-title").value.trim();
    if (!title) { toast("Введите название проекта"); return; }
    await withLoader("Собираю проект…", async () => {
      // ждём, пока докачаются все файлы из пачки (иначе часть могла ещё
      // кешироваться и не попала бы в проект, п.2)
      await _importQueue;
      // создаём проект и импортируем кэшированные файлы
      if (!projectCreated) { await api().new_project(title); projectCreated = true; }
      await flushAllCached();
      const st = await api().enter_editor();
      if (st && st.loaded) await boot(st);
      else toast(st?.error || "Не удалось открыть редактор");
    });
  };

  // пересчитываем готовность при изменении имени
  $("#new-title").addEventListener("input", validateCreateReady);
}

let projectCreated = false;
const importCache = {}; // kind → {file, dataUrl, card}

/* Очередь импорта (п.2): все дропы прогоняем строго последовательно, а кнопка
   «Создать проект» ждёт её завершения — так ни один файл из пачки не теряется
   в гонке с созданием проекта. */
let _importQueue = Promise.resolve();
function enqueueImport(fn) {
  _importQueue = _importQueue.then(fn).catch(err => console.error("Plashker import:", err));
  return _importQueue;
}

/* п.2: полноэкранная зона дропа на экране создания. При перетаскивании файлов
   любую точку экрана можно использовать как приёмник — удобно, когда кидаешь
   сразу пачку. Оверлей чисто визуальный (pointer-events:none): реальную логику
   дропа держит обработчик на window, а точечный дроп на конкретную карточку
   по-прежнему работает (карточка гасит всплытие). */
let _fsdWired = false;
let _fsDragDepth = 0;
function _showFullDrop(on) {
  const el = document.getElementById("create-fulldrop");
  if (el) el.classList.toggle("show", !!on);
}
/* Вешаем прямо на экран #create (а не на window): в WKWebView события drag на
   window ненадёжны, из-за чего оверлей на всю зону не показывался (п.2). Оверлей
   чисто визуальный (pointer-events:none), логику дропа держит #create; точечный
   дроп на карточку обрабатывается ею самой (stopPropagation). */
function wireFullscreenDrop() {
  if (_fsdWired) return;
  const zone = document.getElementById("create");
  if (!zone) return;
  _fsdWired = true;

  zone.addEventListener("dragenter", e => {
    e.preventDefault();
    _fsDragDepth++;
    _showFullDrop(true);
  });
  zone.addEventListener("dragover", e => {
    e.preventDefault();                       // обязательно, иначе drop не сработает
    if (e.dataTransfer) { try { e.dataTransfer.dropEffect = "copy"; } catch (_) {} }
    _showFullDrop(true);
  });
  zone.addEventListener("dragleave", e => {
    _fsDragDepth = Math.max(0, _fsDragDepth - 1);
    if (_fsDragDepth === 0) _showFullDrop(false);
  });
  zone.addEventListener("drop", e => {
    e.preventDefault();
    _fsDragDepth = 0; _showFullDrop(false);
    // точечный дроп на карточку она обрабатывает сама (stopPropagation)
    if (e.target && e.target.closest && e.target.closest(".drop-card")) return;
    const files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length) enqueueImport(() => handleAutoImportFiles(files));
  });
}

const CREATE_KIND_TITLES = {
  title: "Тайтл",
  date: "Дата",
  rating: "ВО",
  date_now: "Уже в кино",
  date_kz: "Дата для KZ",
  rating_kz: "ВО для KZ",
};

function normalizeAssetName(name) {
  return String(name || "")
    .replace(/\.[^.]+$/, "")
    .toLowerCase()
    .replace(/ё/g, "е")
    .replace(/[()\[\]{}]+/g, " ")
    .replace(/[_\-.+]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

const AUTO_TAGS = [
  { kind: "rating_kz", weight: 100, tags: [
    "возрастное ограничение kz", "возрастное ограничение кз", "возрастной рейтинг kz", "возрастной рейтинг кз",
    "рейтинг kz", "рейтинг кз", "rating kz", "kz rating", "vo kz", "kz vo", "во kz", "во кз", "кз во", "rating kazakhstan", "kazakhstan rating"
  ]},
  { kind: "date_kz", weight: 95, tags: [
    "дата для kz", "дата для кз", "дата kz", "дата кз", "kz дата", "кз дата", "date kz", "kz date",
    "date kazakhstan", "kazakhstan date", "дата казахстан", "казахстан дата", "релиз kz", "релиз кз"
  ]},
  { kind: "date_now", weight: 90, tags: [
    "уже в кино", "уже", "now", "in cinemas", "in cinema", "already in cinemas", "now in cinema", "now in cinemas",
    "сейчас в кино", "в кино", "уже кино", "cinema now", "theaters now", "now showing"
  ]},
  { kind: "title", weight: 70, tags: [
    "тайтл", "title", "tt", "тт", "titel", "titl", "movie title", "film title", "логотип фильма", "название фильма", "название", "name"
  ]},
  { kind: "rating", weight: 65, tags: [
    "возрастное ограничение", "возрастной рейтинг", "возраст", "рейтинг", "rating", "vo", "во", "age rating", "age limit", "age", "ценз", "censor"
  ]},
  { kind: "date", weight: 60, tags: [
    "дата релиза", "дата выхода", "дата", "date", "release date", "release", "релиз", "coming", "start", "premiere", "премьера"
  ]},
];

function tagMatches(norm, tag) {
  const t = normalizeAssetName(tag);
  if (!t) return false;
  const hay = ` ${norm} `;
  const needle = ` ${t} `;
  if (hay.includes(needle)) return true;
  // Для коротких аббревиатур нужна только точная токенизация, без fuzzy.
  if (t.length <= 3) return false;
  const compactHay = norm.replace(/\s+/g, "");
  const compactNeedle = t.replace(/\s+/g, "");
  return compactHay.includes(compactNeedle);
}

/* Все подходящие типы для файла: {kind: score}. Возвращаем НЕ только лучший,
   чтобы при коллизии (два файла метят в один слот) второй мог занять свой
   следующий по силе свободный слот, а не пропасть (п.2). */
function scoreAssetFile(file) {
  const norm = normalizeAssetName(file?.name || "");
  const scores = {};
  for (const group of AUTO_TAGS) {
    for (const tag of group.tags) {
      if (tagMatches(norm, tag)) {
        const score = group.weight + normalizeAssetName(tag).length / 100;
        if (!(group.kind in scores) || score > scores[group.kind]) scores[group.kind] = score;
      }
    }
  }
  return scores;
}

/* Обратная совместимость: лучший единственный тип для файла. */
function classifyAssetFile(file) {
  const scores = scoreAssetFile(file);
  let best = null;
  for (const [kind, score] of Object.entries(scores)) {
    if (!best || score > best.score) best = { kind, score };
  }
  return best;
}

/* Жадное глобальное назначение файлов по слотам: каждый файл → максимум один
   слот, каждый слот → максимум один файл, суммарно по убыванию уверенности.
   Гарантирует, что ни один распознаваемый файл не теряется из-за того, что его
   «лучший» слот занял другой файл — он опускается на следующий свободный (п.2). */
function assignAssetFiles(arr) {
  const cands = [];
  arr.forEach((file, fi) => {
    const scores = scoreAssetFile(file);
    for (const [kind, score] of Object.entries(scores)) cands.push({ fi, kind, score });
  });
  cands.sort((a, b) => b.score - a.score);
  const usedFile = new Set(), usedKind = new Set();
  const chosen = {};                 // kind -> file
  for (const c of cands) {
    if (usedFile.has(c.fi) || usedKind.has(c.kind)) continue;
    chosen[c.kind] = arr[c.fi];
    usedFile.add(c.fi); usedKind.add(c.kind);
  }
  const unassigned = arr.filter((_f, i) => !usedFile.has(i));
  return { chosen, unassigned };
}

async function handleAutoImportFiles(files) {
  const arr = [...(files || [])].filter(f => /^image\/png$/i.test(f.type || "") || /\.png$/i.test(f.name || ""));
  const rejected = [...(files || [])].length - arr.length;
  if (!arr.length) { toast("Нужны PNG-файлы"); return; }

  const { chosen, unassigned } = assignAssetFiles(arr);

  // Добор (п.2): файлы, которые не распознались по имени (например, тайтл
  // назван по имени фильма), НЕ теряем — раскладываем по пустым ОБЯЗАТЕЛЬНЫМ
  // зонам в порядке title → date → rating. Именно из-за этого «1 элемент
  // пропадал при импорте пачки».
  const leftover = [...unassigned];
  const filled = [];
  for (const kind of ["title", "date", "rating"]) {
    if (chosen[kind] || imported[kind]) continue;
    if (!leftover.length) break;
    chosen[kind] = leftover.shift();
    filled.push(kind);
  }

  const entries = Object.entries(chosen);
  if (!entries.length) { toast("Не удалось распознать материалы"); return; }
  // импортируем последовательно, чтобы не гонять создание проекта в параллель
  for (const [kind, file] of entries) {
    const card = document.querySelector(`.drop-card[data-kind="${kind}"]`);
    if (card) await handleImport(kind, file, card);
  }

  const names = entries.map(([kind]) => CREATE_KIND_TITLES[kind] || kind).join(", ");
  let msg = `Разложено: ${names}`;
  if (filled.length) msg += " · файлы без узнаваемого имени — по свободным зонам, проверьте";
  if (leftover.length) msg += ` · ${leftover.length} не размещены (перетащите вручную)`;
  if (rejected > 0) msg += ` · пропущено не-PNG: ${rejected}`;
  if (leftover.length) console.warn("Plashker auto-import: не размещены", leftover.map(f => f.name));
  toast(msg);
}

async function handleCreateFiles(kind, files, card) {
  const list = [...(files || [])];
  if (list.length > 1) return handleAutoImportFiles(list);
  if (list[0]) return handleImport(kind, list[0], card);
}

async function ensureProject() {
  const title = $("#new-title").value.trim();
  if (!title) return false;
  if (!projectCreated) { await api().new_project(title); projectCreated = true; }
  return true;
}

async function handleImport(kind, file, card) {
  card.classList.add("drag");
  const dataUrl = await readFileAsDataURL(file);

  // кэшируем оригинал СРАЗУ — до генерации превью и до возможного клика
  // «Создать проект». Иначе при быстром клике по пачке файл, чьё превью ещё
  // считается, не попадал в кэш и терялся (п.2).
  importCache[kind] = { file, dataUrl };
  imported[kind] = true;

  const thumbUrl = await makeVisibleThumbDataURL(dataUrl, 260);
  card.classList.remove("drag");
  card.classList.add("filled");
  $("[data-state]", card).textContent = "";
  const old = $(".drop-thumb", card); if (old) old.remove();
  const thumb = document.createElement("img"); thumb.className = "drop-thumb";
  thumb.src = thumbUrl; card.appendChild(thumb);
  adaptAlphaBg(thumb, thumb);
  addCreateRemoveButton(kind, card);

  // если проект уже создан — сразу импортируем
  if (projectCreated) {
    await flushImport(kind);
  }

  validateCreateReady();
}

function addCreateRemoveButton(kind, card) {
  const old = $(".drop-remove", card); if (old) old.remove();
  const btn = document.createElement("button");
  btn.type = "button"; btn.className = "drop-remove"; btn.title = "Удалить материал";
  btn.textContent = "×";
  btn.onclick = async (e) => {
    e.stopPropagation();
    imported[kind] = false;
    delete importCache[kind];
    resetDropCard(card);
    if (projectCreated && api()?.remove_element) {
      const map = { date_kz: ["date", "KZ", ""], date_now: ["date", "RU", "now"],
                    rating_kz: ["rating", "KZ", ""] };
      const args = map[kind] || [kind, "RU", ""];
      await api().remove_element(...args);
    }
    validateCreateReady();
  };
  card.appendChild(btn);
}

async function flushImport(kind) {
  const cached = importCache[kind];
  if (!cached) return;
  let res;
  if (kind === "date_kz") {
    res = await api().import_element("date", "KZ", cached.file.name, cached.dataUrl);
  } else if (kind === "date_now") {
    res = await api().import_date_now?.("RU", cached.file.name, cached.dataUrl);
    if (!res) res = { ok: true };
  } else if (kind === "rating_kz") {
    res = await api().import_element("rating", "KZ", cached.file.name, cached.dataUrl);
  } else {
    res = await api().import_element(kind, "RU", cached.file.name, cached.dataUrl);
  }
  if (!res || !res.ok) {
    toast(res?.error || `Не удалось импортировать ${kind}`);
    imported[kind] = false;
  }
  delete importCache[kind];
}

async function flushAllCached() {
  for (const kind of Object.keys(importCache)) {
    await flushImport(kind);
  }
}

function validateCreateReady() {
  const note = $("#create-note");
  const title = $("#new-title").value.trim();
  const allMandatory = imported.title && imported.date && imported.rating && title;
  if (allMandatory) {
    note.className = "create-note ok";
    note.textContent = "Всё готово — можно открыть редактор";
  } else {
    const missing = [];
    if (!title) missing.push("название проекта");
    if (!imported.title) missing.push("тайтл");
    if (!imported.date) missing.push("дата");
    if (!imported.rating) missing.push("ВО");
    note.className = "create-note warn";
    note.textContent = `Не хватает: ${missing.join(", ")}`;
  }
  $("#create-enter").disabled = !allMandatory;
}

/* ===========================================================================
   ЗАГРУЗКА РЕДАКТОРА
   =========================================================================== */
/* п.1: оверлей загрузки. Показываем МГНОВЕННО при клике «Открыть», чтобы не
   было ощущения зависания на время распаковки .plshk и первого рендера. */
function showLoader(text) {
  const el = document.getElementById("app-loader");
  if (!el) return;
  const t = document.getElementById("app-loader-title");
  if (t && text) t.textContent = text;
  el.classList.add("show");
}
function hideLoader() {
  const el = document.getElementById("app-loader");
  if (el) el.classList.remove("show");
}
/* Обёртка: гарантированно показать лоадер на время открытия и скрыть в конце,
   даже если что-то упало. Уступаем один кадр, чтобы оверлей успел отрисоваться
   до тяжёлой синхронной работы. */
async function withLoader(text, fn) {
  showLoader(text);
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  try { return await fn(); }
  finally { hideLoader(); }
}

async function boot(state) {
  STATE = state;
  if (!state || !state.loaded) { hideLoader(); toast("Не удалось открыть проект"); return; }
  showLoader("Открываю проект…");
  try {
    $("#welcome").style.display = "none";
    $("#create").style.display = "none";
    $("#app").style.display = "grid";
    $("#movie-chip").textContent = state.movie_title;
    projectCreated = false;
    sessionCalibrated = false;  // сброс авто-пропагации
    SOLO = null; SAVED_VIS = null;

    buildRegions();
    buildCards();
    await selectFormat(state.current_format);
  } finally {
    hideLoader();
  }
  prefetchOthers();
}

function exitToWelcome() {
  $("#app").style.display = "none";
  $("#create").style.display = "none";
  $("#welcome").style.display = "grid";
  invalidateCache();
  const box = $("#welcome-box");
  box.classList.remove("intro"); void box.offsetWidth; box.classList.add("intro");
  loadRecents();
}

/* п.7: SVG-флаги регионов (эмодзи-флаги не рендерятся на Windows, поэтому
   рисуем инлайновым SVG). preserveAspectRatio=none — заливаем всю кнопку,
   скругление даёт .region-flag { overflow:hidden }. */
const FLAG_SVG = {
  RU: `<svg viewBox="0 0 30 20" preserveAspectRatio="none" aria-hidden="true"><rect width="30" height="20" fill="#fff"/><rect y="6.67" width="30" height="13.33" fill="#0039A6"/><rect y="13.33" width="30" height="6.67" fill="#D52B1E"/></svg>`,
  KZ: `<svg viewBox="0 0 30 20" preserveAspectRatio="none" aria-hidden="true"><rect width="30" height="20" fill="#00AFCA"/><circle cx="15" cy="8.5" r="3.1" fill="#FEC50C"/><path d="M10.5 15c1-1.6 2.7-2.4 4.5-2.4s3.5.8 4.5 2.4" fill="none" stroke="#FEC50C" stroke-width="1.1" stroke-linecap="round"/></svg>`,
  BY: `<svg viewBox="0 0 30 20" preserveAspectRatio="none" aria-hidden="true"><rect width="30" height="20" fill="#D22730"/><rect y="13.5" width="30" height="6.5" fill="#009543"/><rect width="6" height="20" fill="#fff"/><g fill="#D22730"><rect x="1.6" y="2.2" width="1.1" height="1.1"/><rect x="3.3" y="2.2" width="1.1" height="1.1"/><rect x="2.45" y="4" width="1.1" height="1.1"/><rect x="1.6" y="5.8" width="1.1" height="1.1"/><rect x="3.3" y="5.8" width="1.1" height="1.1"/><rect x="2.45" y="7.6" width="1.1" height="1.1"/></g></svg>`,
};
const REGION_NAME = { RU: "Россия", KZ: "Казахстан", BY: "Беларусь" };

function buildRegions() {
  const wrap = $("#regions"); wrap.innerHTML = "";

  // п.7: ТВ-кнопка над регионами, по центру. Функция в разработке —
  // при наведении показываем мини-подсказку «ТВ-ПЛАШКИ В РАЗРАБОТКЕ».
  const tv = document.createElement("button");
  tv.type = "button";
  tv.className = "tv-btn";
  tv.setAttribute("data-tip", "ТВ-ПЛАШКИ В РАЗРАБОТКЕ");
  tv.setAttribute("aria-label", "ТВ-плашки (в разработке)");
  tv.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="7" width="19" height="12" rx="2"/><path d="M8 3l4 4 4-4"/><path d="M9 22h6"/></svg>`;
  tv.onclick = (e) => { e.preventDefault(); toast("ТВ-плашки в разработке"); };
  wrap.appendChild(tv);

  const row = document.createElement("div");
  row.className = "region-flags";
  STATE.regions.forEach(r => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "region-flag" + (r === STATE.current_region ? " active" : "");
    b.title = REGION_NAME[r] || r;
    b.setAttribute("aria-label", REGION_NAME[r] || r);
    b.innerHTML = FLAG_SVG[r] || `<span class="region-code">${r}</span>`;
    b.onclick = async () => {
      STATE = await api().switch_region(r);
      buildRegions(); buildCards(); invalidateCache();
      await selectFormat(CURRENT, true); prefetchOthers();
      // п.4 v0.5.2: предупреждение о недостающей дате при переключении региона
      if (r === "KZ") {
        const hasKzDate = STATE.formats?.length && await api().has_region_date?.("KZ");
        if (!hasKzDate) toast("Пожалуйста, загрузите дату для Казахстана");
      }
    };
    row.appendChild(b);
  });
  wrap.appendChild(row);
}

function buildCards() {
  const host = $("#cards"); host.innerHTML = "";
  const groups = {};
  STATE.formats.forEach(f => { (groups[f.family] ??= []).push(f); });
  // п.3 v0.8: в KZ нет BYYD и DA — только соцсети
  const allFams = ["social", "byyd", "da"];
  const order = STATE.current_region === "KZ" ? ["social"] : allFams;
  let first = true;
  order.forEach(fam => {
    if (!groups[fam]) return;
    if (!first) { const s = document.createElement("div"); s.className = "family-sep"; host.appendChild(s); }
    first = false;
    const g = document.createElement("div"); g.className = "family-group";
    const lbl = document.createElement("div"); lbl.className = "family-label";
    lbl.textContent = groups[fam][0].family_title; g.appendChild(lbl);
    const row = document.createElement("div"); row.className = "family-cards";
    groups[fam].forEach(f => row.appendChild(makeCard(f)));
    g.appendChild(row); host.appendChild(g);
  });
}

function makeCard(f) {
  const card = document.createElement("div");
  card.className = "card" + (f.key === CURRENT ? " active" : "") + (f.visible ? "" : " hidden-export");
  card.dataset.key = f.key;
  const ind = document.createElement("div"); ind.className = "ratio";
  const base = 40;
  if (f.aspect >= 1) { ind.style.width = base + "px"; ind.style.height = Math.round(base / f.aspect) + "px"; }
  else { ind.style.height = base + "px"; ind.style.width = Math.round(base * f.aspect) + "px"; }
  const glyph = document.createElement("div"); glyph.className = "glyph";
  glyph.innerHTML = FAMILY_ICON[f.family] || FAMILY_ICON.social;
  const label = document.createElement("div"); label.className = "card-label"; label.textContent = f.key;
  const eye = document.createElement("button"); eye.className = "eye";
  eye.type = "button";
  eye.innerHTML = f.visible ? EYE_ICON : EYE_OFF_ICON;
  eye.classList.toggle("is-off", !f.visible);
  eye.title = f.visible ? "Исключить из экспорта" : "Вернуть в экспорт";
  eye.onclick = async (e) => {
    e.stopPropagation();
    const r = await api().toggle_visible(f.key);
    f.visible = r.visible;
    card.classList.toggle("hidden-export", !r.visible);
    eye.innerHTML = r.visible ? EYE_ICON : EYE_OFF_ICON;
    eye.classList.toggle("is-off", !r.visible);
    eye.title = r.visible ? "Исключить из экспорта" : "Вернуть в экспорт";
  };
  // кнопка «соло» — внизу-слева (п.1 v0.5)
  const solo = document.createElement("button"); solo.className = "solo";
  solo.type = "button"; solo.innerHTML = SOLO_ICON;
  solo.title = "Соло: работать только с этой карточкой";
  solo.classList.toggle("active", SOLO === f.key);
  solo.onclick = (e) => { e.stopPropagation(); toggleSolo(f.key); };
  card.append(ind, glyph, label, eye, solo);
  card.classList.toggle("solo-on", SOLO === f.key);
  card.classList.toggle("solo-dim", SOLO !== null && SOLO !== f.key);
  card.onclick = () => selectFormat(f.key);
  return card;
}

/* ---- соло-режим карточки (п.4): изолировать один формат ------------------ */
let SOLO = null;
let SAVED_VIS = null;
async function toggleSolo(key) {
  if (SOLO === key) { await clearSolo(); return; }
  if (SOLO === null) {
    SAVED_VIS = {};
    STATE.formats.forEach(f => { SAVED_VIS[f.key] = f.visible; });
  }
  SOLO = key;
  for (const f of STATE.formats) {
    const want = (f.key === key);
    if (f.visible !== want) { await api().set_visible(f.key, want); f.visible = want; }
  }
  refreshCardsVisual();
  selectFormat(key);
  toast("Соло: " + key + " — остальные скрыты из экспорта");
}
async function clearSolo() {
  if (SOLO === null) return;
  SOLO = null;
  if (SAVED_VIS) {
    for (const f of STATE.formats) {
      const want = SAVED_VIS[f.key] ?? true;
      if (f.visible !== want) { await api().set_visible(f.key, want); f.visible = want; }
    }
  }
  SAVED_VIS = null;
  refreshCardsVisual();
  toast("Соло выключен");
}
function refreshCardsVisual() {
  $all(".card").forEach(c => {
    const key = c.dataset.key;
    const f = STATE.formats.find(x => x.key === key);
    if (!f) return;
    c.classList.toggle("hidden-export", !f.visible);
    c.classList.toggle("solo-on", SOLO === key);
    c.classList.toggle("solo-dim", SOLO !== null && SOLO !== key);
    const eye = c.querySelector(".eye");
    if (eye) {
      eye.innerHTML = f.visible ? EYE_ICON : EYE_OFF_ICON;
      eye.classList.toggle("is-off", !f.visible);
    }
    const solo = c.querySelector(".solo");
    if (solo) solo.classList.toggle("active", SOLO === key);
  });
}

/* ---- выбор формата (мгновенно из кэша) ---------------------------------- */
async function selectFormat(key, force = false) {
  CURRENT = key;
  $all(".card").forEach(c => c.classList.toggle("active", c.dataset.key === key));
  syncSyncButtonState();
  const r = await api().switch_format(key);
  applySettings(r.settings);
  applyView();
  const f = flags();
  if (!force && previewCache[key] && !f.withBg && !f.withSafe && !f.withAim) {
    $("#preview").src = previewCache[key];
  } else {
    await refreshPreview();
  }
}

function applySettings(s) {
  LAST_SETTINGS = s || {};
  BOUNDS = s.bounds || {};
  PXCTX = s.px_ctx || {};
  const dateCtlLabel = document.querySelector('.control[data-elem="date"] label');
  const gapCtlLabel = document.querySelector('.control[data-elem="gap"] label');
  const isNowMode = s.available?.date_active === "now";
  if (dateCtlLabel) dateCtlLabel.textContent = isNowMode ? "Уже в кино" : "Дата";
  if (gapCtlLabel) gapCtlLabel.textContent = isNowMode ? "Отступ название → уже в кино" : "Отступ название → дата";

  bindControl("title", s.title_scale_pct);
  bindControl("gap", s.gap_title_date_pct);
  bindControl("date", s.date_scale_pct);
  bindControl("rating", s.rating_scale_pct);
  rememberFieldValue("title_scale_pct", s.title_scale_pct);
  rememberFieldValue("gap_title_date_pct", s.gap_title_date_pct);
  rememberFieldValue("date_scale_pct", s.date_scale_pct);
  rememberFieldValue("rating_scale_pct", s.rating_scale_pct);

  // «Уже в кино» — переключатель вариантов даты (v0.13).
  // п.1 v0.14: показываем разбивку «Дата / Уже в кино» ТОЛЬКО когда вариант
  // «Уже в кино» реально загружен. Иначе — прячем (лишняя информация) и на
  // всякий случай возвращаем активный вариант на «Дата».
  const dvHost = $("#date-variant-toggle");
  if (dvHost) {
    if (s.available?.date_now) {
      const active = s.available?.date_active || "date";
      // Сначала задаём состояние, потом показываем: так CSS-ползунок не успевает
      // отрисоваться в дефолтной позиции и не делает лишний прыжок.
      dvHost.dataset.active = active;
      dvHost.querySelectorAll("button").forEach(b => {
        b.classList.toggle("active", b.dataset.variant === active);
      });
      dvHost.style.display = "flex";
    } else {
      dvHost.style.display = "none";
      if (s.available?.date_active === "now") {
        try { api().switch_date_variant?.("date"); } catch (e) { /* ignore */ }
      }
    }
  }

  syncSyncButtonState();
  buildInlineShadowControls(s);
  buildLegal(s);
  // п.3 v0.9: в KZ нет юридического блока — скрываем секцию
  const isKZ = STATE.current_region === "KZ";
  ["#legal-details", "#legal-divider"].forEach(sel => {
    const el = $(sel); if (el) el.style.display = isKZ ? "none" : "";
  });
  buildAdvanced(s);
}

/* перевод occupancy% → человекочитаемые px (п.1).
   Scale → «Ш × В px» (реальный размер контента), отступ → «N px». */
function pxLabel(elem, pct) {
  if (elem === "gap") {
    const H = PXCTX.H || 0;
    return Math.round(pct / 100 * H) + " px";
  }
  const c = PXCTX[elem];
  if (!c) return "—";
  const W = PXCTX.W || 0, H = PXCTX.H || 0;
  let w, h;
  if (c.axis === "height") { h = pct / 100 * H; w = h * (c.cw / c.ch); }
  else { w = pct / 100 * W; h = w * (c.ch / c.cw); }
  return `${Math.round(w)} × ${Math.round(h)} px`;
}

/* ---- индикатор площади ВО (п.2): по закону ВО иногда должна занимать
   не менее 5% площади кадра. Считаем реальную площадь контента / площадь
   холста и подсвечиваем порог. ---------------------------------------------- */
const VO_LEGAL_AREA_PCT = 5;
function ratingAreaPct(scalePct) {
  const c = PXCTX.rating; if (!c) return null;
  const W = PXCTX.W || 0, H = PXCTX.H || 0; if (!W || !H) return null;
  let w, h;
  if (c.axis === "height") { h = scalePct / 100 * H; w = h * (c.cw / c.ch); }
  else { w = scalePct / 100 * W; h = w * (c.ch / c.cw); }
  return (w * h) / (W * H) * 100;
}
function ratingScaleForArea(areaPct) {
  const c = PXCTX.rating; if (!c) return null;
  const W = PXCTX.W || 0, H = PXCTX.H || 0; if (!W || !H) return null;
  const k = (c.axis === "height") ? (H / W) * (c.cw / c.ch)
                                  : (W / H) * (c.ch / c.cw);
  if (k <= 0) return null;
  return 100 * Math.sqrt((areaPct / 100) / k);
}
function updateVOIndicator(ctl, b, value) {
  /* п.2 v0.5: индикатор площади ВО убран — он некорректно отображал реальное
     соотношение и вводил в заблуждение. Очищаем любые остатки. */
  const ind = ctl.querySelector(".vo-ind"); if (ind) ind.remove();
  const tick = ctl.querySelector(".vo-tick"); if (tick) tick.remove();
}

function bindControl(elem, value) {
  const ctl = $(`.control[data-elem="${elem}"]`);
  if (!ctl) return;
  ctl.style.display = (BOUNDS[elem] === null) ? "none" : "block";
  const b = BOUNDS[elem] || { min: 0, max: 100 };
  const range = ctl.querySelector("input[type=range]");
  const px = ctl.querySelector("[data-px]");
  // обернём дорожку для метки-порога (только у ВО), один раз
  if (elem === "rating" && !ctl.querySelector(".srange")) {
    const wrap = document.createElement("div"); wrap.className = "srange";
    range.parentNode.insertBefore(wrap, range); wrap.appendChild(range);
  }
  range.min = b.min; range.max = b.max; range.step = 0.1; range.value = value;
  if (px) px.textContent = pxLabel(elem, value);
  if (elem === "rating") updateVOIndicator(ctl, b, value);

  let dragStartValue = value;
  range.onpointerdown = () => { dragStartValue = parseFloat(range.value) || value; };
  range.onfocus = () => { dragStartValue = parseFloat(range.value) || value; };
  const onInput = (v) => {
    v = Math.min(b.max, Math.max(b.min, parseFloat(v) || 0));
    range.value = v;
    if (px) px.textContent = pxLabel(elem, v);  // px обновляется мгновенно
    if (elem === "rating") updateVOIndicator(ctl, b, v);
    liveUpdate(elem, v);
  };
  range.oninput = () => onInput(range.value);
  range.onchange = async () => {
    const field = fieldMap[elem];
    const v = Math.min(b.max, Math.max(b.min, parseFloat(range.value) || 0));
    if (field) {
      await waitLiveIdle();
      const f = flags();
      await api().set_setting(CURRENT, field, v, f.withBg, f.withSafe, f.withAim, true, dragStartValue);
    }
    invalidateOthers(); await refreshPreview(); prefetchOthers();
    dragStartValue = v;
  };
}

/* ---- live-обновление превью: плавно, без очереди (п.4) ------------------- */
const fieldMap = { title: "title_scale_pct", date: "date_scale_pct",
                   rating: "rating_scale_pct", gap: "gap_title_date_pct" };
function rememberFieldValue(field, value) { FIELD_VALUES[`${CURRENT}:${field}`] = Number(value); }
function beginEdit(field, value) {
  const k = `${CURRENT}:${field}`;
  if (!(k in EDIT_BASELINES)) EDIT_BASELINES[k] = (FIELD_VALUES[k] ?? Number(value));
}
async function commitEdit(field, value) {
  const k = `${CURRENT}:${field}`;
  const old = EDIT_BASELINES[k];
  delete EDIT_BASELINES[k];
  const v = Number(value);
  if (old === undefined || Math.abs(v - Number(old)) < 0.0001) { rememberFieldValue(field, v); return; }
  const f = flags();
  await api().set_setting(CURRENT, field, v, f.withBg, f.withSafe, f.withAim, true, Number(old));
  rememberFieldValue(field, v);
}
let inFlight = false;
let pending = null;          // {field, value} — последнее непосланное значение

function liveUpdate(elem, value) {
  const field = fieldMap[elem];
  if (!field) return;
  pending = { field, value };
  pumpLive();
}
/* п.3 v0.14: живое обновление произвольного числового поля настроек
   (используется ползунками положения X/Y) — через тот же плавный pump. */
function liveSetField(field, value) {
  pending = { field, value };
  pumpLive();
}
function waitLiveIdle() {
  pending = null;
  if (!inFlight) return Promise.resolve();
  return new Promise(resolve => {
    const tick = () => inFlight ? setTimeout(tick, 16) : resolve();
    tick();
  });
}
async function pumpLive() {
  if (inFlight || !pending) return;
  const { field, value } = pending; pending = null;
  inFlight = true;
  const f = flags();
  try {
    const b64 = await api().set_setting(CURRENT, field, value, f.withBg, f.withSafe, f.withAim, false);
    $("#preview").src = b64;
    if (!f.withBg && !f.withSafe && !f.withAim) previewCache[CURRENT] = b64;
  } catch (e) { /* ignore */ }
  inFlight = false;
  if (pending) requestAnimationFrame(pumpLive);   // есть новее — сразу рендерим его
}

async function refreshPreview() {
  const f = flags();
  const b64 = await api().render_preview(CURRENT, f.withBg, f.withSafe, f.withAim);
  $("#preview").src = b64;
  if (!f.withBg && !f.withSafe && !f.withAim) previewCache[CURRENT] = b64;
}

/* ---- фоновый предрасчёт остальных форматов ------------------------------ */
function invalidateCache() { for (const k in previewCache) delete previewCache[k]; }
function invalidateOthers() { for (const k in previewCache) if (k !== CURRENT) delete previewCache[k]; }
let prefetchQueue = [];
function prefetchOthers() {
  if (!STATE) return;
  prefetchQueue = STATE.formats.map(f => f.key).filter(k => k !== CURRENT && !previewCache[k]);
  pumpPrefetch();
}
async function pumpPrefetch() {
  if (inFlight) { setTimeout(pumpPrefetch, 60); return; }  // не мешаем live-рендеру
  if (!prefetchQueue.length) return;
  const key = prefetchQueue.shift();
  try { previewCache[key] = await api().render_preview(key, false, false); } catch (e) {}
  setTimeout(pumpPrefetch, 0);
}

/* ---- зум/панорамирование превью как в Photoshop (п.3) ------------------- */
function getView() {
  return VIEW[CURRENT] || (VIEW[CURRENT] = { z: 1, panX: 0, panY: 0 });
}
function applyView() {
  const v = getView();
  v.z = Math.max(0.1, Math.min(8, v.z));
  $("#canvas-zoom").style.transform =
    `translate(${v.panX}px, ${v.panY}px) scale(${v.z})`;
  $("#zoom-input").value = Math.round(v.z * 100);
  api()?.set_zoom?.(CURRENT, Math.round(v.z * 100));
}
function resetView() {
  VIEW[CURRENT] = { z: 1, panX: 0, panY: 0 };
  applyView();
}
function setZoomPct(pct) {            // из числового поля — зум к центру
  zoomAt(pct / 100, null);
}
/* зум к точке (cx,cy) в координатах вьюпорта относительно его центра;
   если point=null — к центру. transform-origin: center. */
function zoomAt(newZ, point) {
  const v = getView();
  newZ = Math.max(0.1, Math.min(8, newZ));
  const wrap = $("#canvas-wrap").getBoundingClientRect();
  const cx = point ? point.x - (wrap.left + wrap.width / 2) : 0;
  const cy = point ? point.y - (wrap.top + wrap.height / 2) : 0;
  // точка под курсором в «контентных» координатах до зума
  const ux = (cx - v.panX) / v.z;
  const uy = (cy - v.panY) / v.z;
  // после зума она должна остаться под курсором
  v.panX = cx - ux * newZ;
  v.panY = cy - uy * newZ;
  v.z = newZ;
  applyView();
}
function wireCanvasInteractions() {
  const wrap = $("#canvas-wrap");
  // колесо/трекпад: ctrl(или cmd) → зум к курсору; иначе → панорамирование
  wrap.addEventListener("wheel", (e) => {
    e.preventDefault();
    const v = getView();
    if (e.ctrlKey || e.metaKey) {
      // зум к курсору — чувствительность увеличена ~вдвое (п.4)
      const factor = Math.exp(-e.deltaY * 0.0036);
      zoomAt(v.z * factor, { x: e.clientX, y: e.clientY });
    } else {
      v.panX -= e.deltaX;
      v.panY -= e.deltaY;
      applyView();
    }
  }, { passive: false });

  // перетаскивание мышью = панорамирование (как «рука» в фотошопе)
  let dragging = false, lx = 0, ly = 0;
  wrap.addEventListener("mousedown", (e) => {
    dragging = true; lx = e.clientX; ly = e.clientY;
    wrap.classList.add("grabbing");
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const v = getView();
    v.panX += e.clientX - lx; v.panY += e.clientY - ly;
    lx = e.clientX; ly = e.clientY;
    applyView();
  });
  window.addEventListener("mouseup", () => { dragging = false; wrap.classList.remove("grabbing"); });
  // двойной клик — сброс к «вписать»
  wrap.addEventListener("dblclick", resetView);
}

/* ---- секция юридического блока ------------------------------------------ */
function syncSyncButtonState() {
  const host = $("#sync-mode-actions");
  if (!host) return;
  const hasNow = !!LAST_SETTINGS?.available?.date_now;
  const activeVariant = LAST_SETTINGS?.available?.date_active || "date";
  const isSource = CURRENT === "16x9";
  host.style.display = isSource ? "grid" : "none";
  const dateBtn = $("#btn-sync-date");
  const nowBtn = $("#btn-sync-now");
  if (nowBtn) nowBtn.style.display = hasNow ? "" : "none";

  const setup = (btn, variant, available) => {
    if (!btn) return;
    const enabled = isSource && available && activeVariant === variant;
    btn.disabled = !enabled;
    btn.classList.toggle("is-active-sync", enabled);
    btn.title = enabled
      ? (variant === "now" ? "Синхронизировать режим «Уже в кино» с 16×9" : "Синхронизировать режим даты с 16×9")
      : (!isSource
          ? "Синхронизация доступна только на соц. формате 16×9"
          : (!available
              ? "Версия «Уже в кино» не загружена"
              : (variant === "now" ? "Доступно только в режиме «Уже в кино»" : "Доступно только в режиме «Дата»")));
  };
  setup(dateBtn, "date", true);
  setup(nowBtn, "now", hasNow);
}

function buildLegal(s) {
  const host = $("#legal-section"); host.innerHTML = "";
  const L = s.legal;
  const addChk = (field, text, checked, disabled) => {
    const lab = document.createElement("label");
    lab.className = "chk" + (disabled ? " disabled" : "");
    const cb = document.createElement("input"); cb.type = "checkbox";
    cb.checked = !!checked; cb.disabled = !!disabled;
    cb.onchange = async () => {
      await api().set_legal(CURRENT, field, cb.checked);
      invalidateCache(); await refreshPreview(); prefetchOthers();
    };
    const vis = document.createElement("span"); vis.className = "chk-vis"; vis.setAttribute("aria-hidden", "true");
    lab.append(cb, vis, document.createTextNode(" " + text));
    host.appendChild(lab);
    return cb;
  };

  if (s.legal_is_vertical) {
    addChk("show_ad_and_legal_combined", "РЕКЛАМА + юр.информация", L.show_ad_and_legal_combined);

    // п. v0.12: ползунок масштаба рекламной маркировки
    const scaleWrap = document.createElement("div"); scaleWrap.className = "legal-y-slider";
    const scaleLabel = document.createElement("div"); scaleLabel.className = "legal-y-label";
    const curScale = L.combined_scale_pct || 28.8;
    scaleLabel.innerHTML = `<span>Масштаб маркировки</span><span class="pxout">${curScale.toFixed(1)}%</span>`;
    const scaleRange = document.createElement("input");
    scaleRange.type = "range"; scaleRange.min = 5; scaleRange.max = 60;
    scaleRange.step = 0.5; scaleRange.value = curScale;
    scaleRange.oninput = () => {
      scaleLabel.querySelector(".pxout").textContent = `${parseFloat(scaleRange.value).toFixed(1)}%`;
    };
    scaleRange.onchange = async () => {
      await api().set_legal(CURRENT, "combined_scale_pct", parseFloat(scaleRange.value));
      invalidateCache(); await refreshPreview(); prefetchOthers();
    };
    scaleWrap.append(scaleLabel, scaleRange);
    host.appendChild(scaleWrap);

    // п. v0.11: ползунок вертикальной позиции рекламной маркировки
    const H = s.canvas_h || 1920;
    const yWrap = document.createElement("div"); yWrap.className = "legal-y-slider";
    const yLabel = document.createElement("div");
    yLabel.className = "legal-y-label";
    const offsetPct = L.combined_offset_y_pct || 0;
    // перевод: offset% → пиксельная Y-координата (1920 = верх, 0 = низ)
    const anchorBottom = H;  // без offset элемент внизу
    const pixelY = Math.round(anchorBottom + (offsetPct / 100) * H);
    const displayY = H - pixelY; // инвертируем: 1920=верх, 0=низ
    yLabel.innerHTML = `<span>Позиция Y маркировки</span><span class="pxout">${Math.max(0, displayY)} px</span>`;
    const yRange = document.createElement("input");
    yRange.type = "range"; yRange.min = 0; yRange.max = H;
    yRange.step = 1; yRange.value = Math.max(0, displayY);
    yRange.oninput = () => {
      const val = parseInt(yRange.value);
      yLabel.querySelector(".pxout").textContent = `${val} px`;
    };
    yRange.onchange = async () => {
      const val = parseInt(yRange.value);
      // конвертация: displayY (0=низ, 1920=верх) → offset_pct
      const pixY = H - val;
      const newOffset = ((pixY - anchorBottom) / H) * 100;
      await api().set_legal(CURRENT, "combined_offset_y_pct", Math.round(newOffset * 100) / 100);
      invalidateCache(); await refreshPreview(); prefetchOthers();
    };
    yWrap.append(yLabel, yRange);
    host.appendChild(yWrap);
  } else {
    addChk("show_ad_label", "РЕКЛАМА", L.show_ad_label);
    addChk("show_our_legal", "Наша юр.информация", L.show_our_legal);
  }

  // --- юр.информация площадки: СНАЧАЛА зона загрузки, ПОД ней чекбокс (п.2) ---
  const wrap = document.createElement("div"); wrap.className = "platform-legal";
  const lblTitle = document.createElement("div");
  lblTitle.className = "pl-title"; lblTitle.textContent = "Юр.информация площадки";
  const dz = document.createElement("div"); dz.className = "dropzone";
  dz.innerHTML = L.platform_legal_file
    ? `Файл загружен ✓<br><small>${escapeHtml(L.platform_legal_file.split("/").pop())}</small>`
    : "Перетащите PNG юр.информации площадки<br><small>или нажмите для выбора</small>";
  const fileInput = document.createElement("input");
  fileInput.type = "file"; fileInput.accept = "image/png"; fileInput.style.display = "none";
  const onFile = async (file) => {
    if (!file) return;
    const dataUrl = await readFileAsDataURL(file);
    const res = await api().set_platform_legal(CURRENT, file.name, dataUrl);
    if (!res || !res.ok) { toast(res?.error || "Не удалось загрузить"); return; }
    invalidateCache();
    const st = await api().switch_format(CURRENT);
    applySettings(st.settings);
    await refreshPreview(); prefetchOthers();
    toast("Юр.информация площадки загружена");
  };
  dz.onclick = () => fileInput.click();
  fileInput.onchange = () => onFile(fileInput.files[0]);
  dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("drag"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
  dz.addEventListener("drop", e => { e.preventDefault(); dz.classList.remove("drag");
    onFile(e.dataTransfer.files && e.dataTransfer.files[0]); });

  wrap.append(lblTitle, dz, fileInput);
  host.appendChild(wrap);

  // чекбокс показа — ПОД зоной, залочен пока файл не загружен
  const locked = !L.platform_legal_file;
  addChk("show_platform_legal",
         locked ? "Показывать (загрузите файл выше)" : "Показывать юр.информацию площадки",
         L.show_platform_legal, locked);
}


function buildInlineShadowControls(s) {
  let slot = document.getElementById("shadow-inline-slot");
  const ratingCtl = document.querySelector('.control[data-elem="rating"]');
  const divider = document.getElementById("legal-divider");
  if (!slot) {
    slot = document.createElement("div");
    slot.id = "shadow-inline-slot";
    if (ratingCtl && ratingCtl.parentNode) ratingCtl.insertAdjacentElement("afterend", slot);
    else if (divider && divider.parentNode) divider.parentNode.insertBefore(slot, divider);
  }
  slot.innerHTML = "";
  buildShadowControls(s, slot);
}

/* ---- расширенные настройки плашки (п.4 v0.7) ---------------------------- */
function buildAdvanced(s) {
  const host = $("#advanced-section"); if (!host) return;
  host.innerHTML = "";
  const d = s.display || {};
  const avail = s.available || {};

  const addToggle = (field, text, checked, disabled) => {
    const lab = document.createElement("label");
    lab.className = "chk" + (disabled ? " disabled" : "");
    const cb = document.createElement("input"); cb.type = "checkbox";
    cb.checked = !!checked; cb.disabled = !!disabled;
    cb.onchange = async () => {
      const res = await api().set_display(CURRENT, field, cb.checked);
      if (res && res.bounds) { applySettings(res); }
      invalidateCache(); await refreshPreview(); prefetchOthers();
    };
    const vis = document.createElement("span"); vis.className = "chk-vis"; vis.setAttribute("aria-hidden", "true");
    lab.append(cb, vis, document.createTextNode(" " + text));
    host.appendChild(lab);
  };

  const hint = document.createElement("div");
  hint.className = "advanced-hint";
  hint.textContent = "Элементы плашки";
  host.appendChild(hint);

  addToggle("show_title", "Показывать название", d.show_title, false);
  addToggle("show_date", "Показывать дату",
            d.show_date, !avail.date);
  addToggle("show_rating", "Показывать ВО",
            d.show_rating, !avail.rating);

  // перестановка блоков — выше положения элементов, как основное действие.
  if (STATE.current_region !== "KZ") {
    const swapWrap = document.createElement("div"); swapWrap.className = "advanced-swap";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "swap-btn" + (d.swap_title_rating ? " on" : "");
    btn.innerHTML =
      `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 7h11l-3-3M17 17H6l3 3"/></svg>` +
      `<span>Отзеркалить расположение элементов</span>`;
    btn.onclick = async () => {
      const res = await api().set_display(CURRENT, "swap_title_rating", !d.swap_title_rating);
      if (res) applySettings(res);
      invalidateCache(); await refreshPreview(); prefetchOthers();
    };
    swapWrap.appendChild(btn);
    host.appendChild(swapWrap);
  }

  // --- положение элементов по X и Y ---------------------------------------
  buildPositionControls(s, host);
}

function buildShadowControls(s, host) {
  const d = s.display || {};
  const wrap = document.createElement("div");
  wrap.className = "shadow-block";

  const lab = document.createElement("label");
  lab.className = "chk shadow-toggle";
  const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = !!d.shadow_enabled;
  cb.onchange = async () => {
    const res = await api().set_display(CURRENT, "shadow_enabled", cb.checked);
    if (res) applySettings(res);
    invalidateCache(); await refreshPreview(); prefetchOthers();
  };
  const shadowVis = document.createElement("span"); shadowVis.className = "chk-vis"; shadowVis.setAttribute("aria-hidden", "true");
  const title = document.createElement("span");
  title.className = "shadow-title";
  title.innerHTML = `<span class="shadow-name">Тень элементов</span><span class="shadow-sub">мягкая глубина для всех слоёв</span>`;
  lab.append(cb, shadowVis, title);
  wrap.appendChild(lab);

  const panel = document.createElement("div");
  panel.className = "shadow-panel";
  panel.style.display = cb.checked ? "grid" : "none";

  const make = (field, title, value, min, max, step, suffix) => {
    const row = document.createElement("div"); row.className = "shadow-row";
    const head = document.createElement("div"); head.className = "shadow-head";
    const out = document.createElement("span"); out.className = "pxout";
    const val = Number(value ?? 0);
    out.textContent = `${val.toFixed(2)}${suffix}`;
    head.innerHTML = `<span>${title}</span>`; head.appendChild(out);
    const range = document.createElement("input");
    range.type = "range"; range.min = min; range.max = max; range.step = step; range.value = val;
    range.oninput = () => {
      const v = Number(range.value);
      out.textContent = `${v.toFixed(2)}${suffix}`;
      liveSetField(field, v);
    };
    range.onchange = async () => {
      const f = flags();
      await waitLiveIdle();
      await api().set_setting(CURRENT, field, Number(range.value), f.withBg, f.withSafe, f.withAim, true, val);
      invalidateOthers(); await refreshPreview(); prefetchOthers();
    };
    row.append(head, range);
    return row;
  };

  panel.append(
    make("shadow_blur_pct", "Размер / интенсивность", d.shadow_blur_pct ?? 0.45, 0, 3.0, 0.05, "%"),
    make("shadow_distance_pct", "Расстояние от элементов", d.shadow_distance_pct ?? 0.55, 0, 4.0, 0.05, "%"),
    make("shadow_opacity_pct", "Прозрачность тени", d.shadow_opacity_pct ?? 55, 0, 100, 1, "%")
  );
  wrap.appendChild(panel);
  host.appendChild(wrap);
}


/* ---- п.3 v0.14: ползунки положения плашки по X и Y ---------------------- */
function buildPositionControls(s, host) {
  const W = (s.px_ctx && s.px_ctx.W) || s.canvas_w || 1000;
  const H = (s.px_ctx && s.px_ctx.H) || s.canvas_h || 1000;
  const bx = (s.bounds && s.bounds.offset_x) || { min: -25, max: 25 };
  const by = (s.bounds && s.bounds.offset_y) || { min: -25, max: 25 };
  const activeDate = s.available?.date_active === "now";
  const dateKey = activeDate ? "date_now" : "date";
  const offsets = s.element_offsets || {};
  const labels = [
    ["title", "Тайтл"],
    [dateKey, activeDate ? "Уже в кино" : "Дата"],
    ["rating", "ВО"],
  ];
  if (!labels.find(([k]) => k === POSITION_TARGET)) POSITION_TARGET = labels[0][0];

  const wrap = document.createElement("div");
  wrap.className = "position-block";

  const head = document.createElement("div");
  head.className = "position-head";
  const title = document.createElement("span");
  title.className = "advanced-hint"; title.style.margin = "0";
  title.textContent = "Положение элемента";
  const reset = document.createElement("button");
  reset.type = "button"; reset.className = "position-reset";
  reset.textContent = "сбросить";
  head.append(title, reset);
  wrap.appendChild(head);

  const seg = document.createElement("div");
  seg.className = "position-segments";
  labels.forEach(([key, label]) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "position-seg" + (POSITION_TARGET === key ? " active" : "");
    b.textContent = label;
    b.onclick = async () => {
      POSITION_TARGET = key;
      await api().set_position_target?.(key);
      const st = await api().switch_format(CURRENT);
      applySettings(st.settings);
    };
    seg.appendChild(b);
  });
  wrap.appendChild(seg);

  const current = offsets[POSITION_TARGET] || { x_pct: 0, y_pct: 0 };
  const makeAxis = (axis, label, curPct, b, span, icon) => {
    const row = document.createElement("div"); row.className = "position-row";
    const lab = document.createElement("div"); lab.className = "position-label";
    const px = Math.round((curPct / 100) * span);
    lab.innerHTML = `<span class="position-name">${icon} ${label}</span>` +
                    `<span class="pxout position-val">${px > 0 ? "+" : ""}${px} px</span>`;
    const range = document.createElement("input");
    range.type = "range"; range.min = b.min; range.max = b.max;
    range.step = 0.1; range.value = curPct;
    let dragStartOffset = { ...(offsets[POSITION_TARGET] || { x_pct: 0, y_pct: 0 }) };
    range.onpointerdown = () => { dragStartOffset = { ...(offsets[POSITION_TARGET] || { x_pct: 0, y_pct: 0 }) }; };
    range.onfocus = () => { dragStartOffset = { ...(offsets[POSITION_TARGET] || { x_pct: 0, y_pct: 0 }) }; };
    const out = lab.querySelector(".position-val");
    const field = `offset_${POSITION_TARGET}_${axis}_pct`;
    range.oninput = () => {
      const v = parseFloat(range.value) || 0;
      const p = Math.round((v / 100) * span);
      out.textContent = `${p > 0 ? "+" : ""}${p} px`;
      liveSetField(field, v);
    };
    range.onchange = async () => {
      const v = parseFloat(range.value) || 0;
      await waitLiveIdle();
      const f = flags();
      await api().set_setting(CURRENT, field, v, f.withBg, f.withSafe, f.withAim, true, dragStartOffset);
      invalidateOthers(); await refreshPreview(); prefetchOthers();
      dragStartOffset = { ...(offsets[POSITION_TARGET] || { x_pct: 0, y_pct: 0 }), [axis + '_pct']: v };
    };
    range.ondblclick = () => {
      range.value = 0; out.textContent = "0 px";
      liveSetField(field, 0);
      invalidateOthers(); refreshPreview(); prefetchOthers();
    };
    row.append(lab, range);
    return { row, range, out };
  };

  const xIco = `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M5 12l3-3M5 12l3 3M19 12l-3-3M19 12l-3 3"/></svg>`;
  const yIco = `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M12 5l-3 3M12 5l3 3M12 19l-3-3M12 19l3-3"/></svg>`;

  const ax = makeAxis("x", "По горизонтали", current.x_pct || 0, bx, W, xIco);
  const ay = makeAxis("y", "По вертикали", current.y_pct || 0, by, H, yIco);
  wrap.append(ax.row, ay.row);

  reset.onclick = async () => {
    ax.range.value = 0; ay.range.value = 0;
    ax.out.textContent = "0 px"; ay.out.textContent = "0 px";
    await api().set_setting(CURRENT, `offset_${POSITION_TARGET}_x_pct`, 0, false, false, false);
    await api().set_setting(CURRENT, `offset_${POSITION_TARGET}_y_pct`, 0, false, false, false);
    invalidateOthers(); await refreshPreview(); prefetchOthers();
  };

  host.appendChild(wrap);
}

/* ===========================================================================
   СТАТИЧНЫЕ ОБРАБОТЧИКИ (модалки, зум, клавиши)
   =========================================================================== */

function wireDateVariantToggle() {
  $all("#date-variant-toggle .dv-btn").forEach(btn => {
    btn.onclick = async () => {
      const res = await api().switch_date_variant?.(btn.dataset.variant);
      if (res?.ok) {
        const host = $("#date-variant-toggle");
        if (host) host.dataset.active = btn.dataset.variant || "date";
        $all("#date-variant-toggle .dv-btn").forEach(b => b.classList.toggle("active", b === btn));
        const st = await api().switch_format(CURRENT);
        if (st?.settings) applySettings(st.settings);
        invalidateCache(); await refreshPreview(); prefetchOthers();
      } else {
        toast("Версия «Уже в кино» не загружена");
      }
    };
  });
}

function wireStaticUI() {
  $("#chk-bg").onchange = refreshPreview;
  $("#chk-safe").onchange = refreshPreview;
  const aim = $("#chk-aim"); if (aim) aim.onchange = refreshPreview;
  $("#zoom-input").onchange = (e) => setZoomPct(parseInt(e.target.value) || 100);
  $("#zoom-fit").onclick = resetView;
  wireCanvasInteractions();
  wireDateVariantToggle();

  $all("[data-close]").forEach(b => b.onclick = () => $("#" + b.dataset.close).classList.remove("open"));
  $("#btn-export").onclick = openExportModal;
  $("#exp-run").onclick = runExport;
  $("#exp-toggle-all").onclick = toggleAllFormats;
  // п.10 v0.5: пересчитывать доступность кнопки при переключении PNG/JPEG/PSD
  ["exp-png", "exp-jpeg", "exp-psd", "exp-now", "exp-platform"].forEach(id => {
    const el = $("#" + id);
    if (el) el.addEventListener("change", async () => {
      if (id === "exp-now" && !el.disabled) {
        await api().set_export_now_enabled?.(el.checked);
        if (LAST_SETTINGS?.available) LAST_SETTINGS.available.export_now_enabled = el.checked;
      }
      syncExportRunBtn();
    });
  });
  $all(".sync-mode-btn").forEach(syncBtn => {
    syncBtn.onclick = async () => {
      syncSyncButtonState();
      const variant = syncBtn.dataset.syncVariant || "date";
      if (CURRENT !== "16x9") {
        toast("Синхронизация только на 16×9");
        return;
      }
      await waitLiveIdle();
      syncBtn.disabled = true;
      try {
        const res = await api().sync_from?.(CURRENT, variant);
        if (!res?.ok) { toast(res?.error || "Не удалось синхронизировать"); return; }
        if (variant === "now") {
          await api().switch_date_variant?.("now");
        } else {
          await api().switch_date_variant?.("date");
        }
        const st = await api().switch_format(CURRENT);
        if (st?.settings) applySettings(st.settings);
        invalidateCache();
        await refreshPreview();
        prefetchOthers();
        toast(variant === "now" ? `Синхронизировано «Уже в кино»: ${res.count || 0}` : `Синхронизирована дата: ${res.count || 0}`);
        syncSuccessBurst(syncBtn);
      } finally {
        syncSyncButtonState();
      }
    };
  });
  // кнопка «Заменить» — открывает модалку со всеми элементами (п.2 v0.5.2)
  $("#btn-replace-all").onclick = openReplaceAll;

  $("#btn-exit").onclick = () => $("#modal-exit").classList.add("open");
  $("#exit-cancel").onclick = () => $("#modal-exit").classList.remove("open");
  $("#exit-nosave").onclick = () => { $("#modal-exit").classList.remove("open"); exitToWelcome(); };
  $("#exit-save").onclick = async () => {
    const path = await api().save_current();
    $("#modal-exit").classList.remove("open");
    if (path) { toast("Сохранено"); exitToWelcome(); }
    else toast("Сохранение отменено");
  };
  $("#btn-app-settings").onclick = openAppSettings;
  const welcomeSettings = $("#btn-app-settings-welcome");
  if (welcomeSettings) welcomeSettings.onclick = openAppSettings;
  $("#btn-hotkeys").onclick = () => $("#modal-hotkeys").classList.add("open");
  // п.6: кнопка сохранения в топбаре — тот же путь, что и ⌘/Ctrl+S
  const saveBtn = $("#btn-save");
  if (saveBtn) saveBtn.onclick = () => saveCurrentProject();
  applyOSHotkeyLabels();
  $all(".theme-choice").forEach(btn => {
    btn.onclick = (e) => { e.stopPropagation(); setTheme(btn.dataset.themeChoice); };
  });
  const tt = $("#theme-toggle");
  if (tt) {
    tt.onclick = toggleTheme;
    tt.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleTheme(); }
    };
  }
  applyTheme(document.documentElement.getAttribute("data-theme") || "light");

  // закрытие модалок по клику на фон
  $all(".modal-backdrop").forEach(bd => bd.addEventListener("click", e => {
    if (e.target === bd) bd.classList.remove("open");
  }));

  document.addEventListener("keydown", onKeydown);
}

/* Единая точка сохранения: и кнопка в топбаре (п.6), и ⌘/Ctrl+S (п.5).
   Работает в любой момент, пока открыт редактор (в т.ч. при открытых
   модалках). Если проект новый — save_current сам откроет диалог «Сохранить как». */
async function saveCurrentProject() {
  const inEditor = $("#app").style.display !== "none";
  if (!inEditor || !api()?.save_current) return null;
  const btn = $("#btn-save");
  if (btn) btn.classList.add("busy");
  try {
    const path = await api().save_current();
    toast(path ? "Проект сохранён" : "Сохранение отменено");
    return path;
  } catch (err) {
    toast("Не удалось сохранить проект");
    return null;
  } finally {
    if (btn) btn.classList.remove("busy");
  }
}

/* ---- горячие клавиши (п.10 CMD+S, п.11 стрелки) ------------------------- */
async function onKeydown(e) {
  const inEditor = $("#app").style.display !== "none";
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");

  // Enter — принять базовые действия: создать проект / финальный экспорт.
  if (e.key === "Enter" && !e.metaKey && !e.ctrlKey && !e.altKey) {
    const exportOpen = $("#modal-export")?.classList.contains("open");
    const createOpen = $("#create")?.style.display !== "none";
    if (exportOpen) {
      const btn = $("#exp-run");
      if (btn && !btn.disabled) { e.preventDefault(); btn.click(); }
      return;
    }
    if (createOpen) {
      const btn = $("#create-enter");
      if (btn && !btn.disabled) { e.preventDefault(); btn.click(); }
      return;
    }
  }

  // CMD/CTRL+S — сохранить (п.10); +Shift = «Сохранить как» (п.7 v0.8)
  // Ловим по e.code (физическая клавиша), чтобы работало на любой раскладке —
  // на русской раскладке Windows e.key для этой клавиши = «ы», не «s» (п.5).
  const isKeyS = e.code === "KeyS" || e.key === "s" || e.key === "S" ||
                 e.key === "ы" || e.key === "Ы";
  if ((e.metaKey || e.ctrlKey) && isKeyS) {
    e.preventDefault();
    if (e.shiftKey) {
      if (!inEditor || !api()?.save_as) return;
      const path = await api().save_as();
      toast(path ? `Дубликат сохранён: ${path}` : "Сохранение отменено");
    } else {
      await saveCurrentProject();
    }
    return;
  }
  // CMD/CTRL+Z и CMD/CTRL+SHIFT+Z — undo/redo только для редактирования.
  // Тоже по e.code — на русской раскладке физическая Z = «я» (п.5).
  const isKeyZ = e.code === "KeyZ" || e.key === "z" || e.key === "Z" ||
                 e.key === "я" || e.key === "Я";
  if ((e.metaKey || e.ctrlKey) && !typing && !anyModalOpen() && isKeyZ) {
    e.preventDefault();
    if (!inEditor) return;
    await applyHistory(e.shiftKey ? "redo" : "undo");
    return;
  }

  // стрелки — переключение форматов (п.11)
  if (inEditor && !typing && !anyModalOpen() &&
      (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
    e.preventDefault();
    stepFormat(e.key === "ArrowRight" ? 1 : -1);
  }
}
async function applyHistory(action) {
  try {
    const res = action === "redo" ? await api().redo?.() : await api().undo?.();
    if (!res || !res.settings) { toast(action === "redo" ? "Повторять нечего" : "Отменять нечего"); return; }
    if (res.format && res.format !== CURRENT) {
      CURRENT = res.format;
      $all(".card").forEach(c => c.classList.toggle("active", c.dataset.key === CURRENT));
    }
    applySettings(res.settings);
    invalidateCache();
    await refreshPreview(); prefetchOthers();
    toast(action === "redo" ? "Повторено" : "Отменено");
  } catch (e) {
    toast("Не удалось выполнить команду");
  }
}

function stepFormat(dir) {
  if (!STATE) return;
  const keys = STATE.formats.map(f => f.key);
  let i = keys.indexOf(CURRENT);
  if (i < 0) return;
  i = (i + dir + keys.length) % keys.length;
  selectFormat(keys[i]);
  const card = $(`.card[data-key="${keys[i]}"]`);
  card?.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
}

/* ---- модалка настроек приложения (п.5/8) -------------------------------- */
const GLOBAL_LABELS = {
  ad_label_h: "РЕКЛАМА (гориз.)",
  our_legal_h: "Наша юр.инфо (гориз.)",
  ad_and_legal_combined_v: "РЕКЛАМА + юр (верт.)",
  preview_background: "Фон для согласования",
};

function assetCard(label, thumb, onFile) {
  const card = document.createElement("div"); card.className = "asset-card";
  const cap = document.createElement("div"); cap.className = "asset-cap"; cap.textContent = label;
  const prev = document.createElement("div"); prev.className = "asset-prev";
  if (thumb) { const im = document.createElement("img"); im.src = thumb; prev.appendChild(im); adaptAlphaBg(im, prev); }
  else prev.textContent = "нет файла";
  const hint = document.createElement("div"); hint.className = "asset-hint"; hint.textContent = "заменить";
  const input = document.createElement("input");
  input.type = "file"; input.accept = "image/png"; input.style.display = "none";
  card.append(cap, prev, hint, input);
  card.onclick = () => input.click();
  const handle = async (file) => {
    if (!file) return;
    card.classList.add("busy");
    const dataUrl = await readFileAsDataURL(file);
    const res = await onFile(file.name, dataUrl);
    card.classList.remove("busy");
    if (!res || !res.ok) { toast(res?.error || "Не удалось заменить"); return; }
    prev.innerHTML = ""; const im = document.createElement("img"); im.src = res.thumb; prev.appendChild(im); adaptAlphaBg(im, prev);
    invalidateCache(); refreshPreview(); prefetchOthers();
    toast("Ассет заменён");
  };
  input.onchange = () => handle(input.files[0]);
  card.addEventListener("dragover", e => { e.preventDefault(); card.classList.add("drag"); });
  card.addEventListener("dragleave", () => card.classList.remove("drag"));
  card.addEventListener("drop", e => { e.preventDefault(); card.classList.remove("drag");
    handle(e.dataTransfer.files && e.dataTransfer.files[0]); });
  return card;
}

async function openAppSettings() {
  try {
    const a = await api().get_app_assets();
    const ghost = $("#as-globals"); ghost.innerHTML = "";
    Object.keys(GLOBAL_LABELS).forEach(slot => {
      const v = a[slot] || {};
      ghost.appendChild(assetCard(GLOBAL_LABELS[slot], v.thumb,
        (fn, durl) => api().replace_global_asset(slot, fn, durl)));
    });
    const shost = $("#as-safe"); shost.innerHTML = "";
    Object.entries(a.safe_zones || {}).forEach(([k, v]) => {
      shost.appendChild(assetCard(k, v.thumb,
        (fn, durl) => api().replace_safe_zone(k, fn, durl)));
    });
  } catch (e) { toast("Не удалось загрузить настройки"); }
  $("#modal-app").classList.add("open");
}

/* ---- экспорт ------------------------------------------------------------ */
async function openExportModal() {
  const host = $("#exp-formats"); host.innerHTML = "";
  const groups = [
    ["Соц.сети RU", STATE.formats.filter(f => f.family === "social" && f.supports_regions).map(f => ({ value: f.key, label: f.key, checked: f.visible }))],
    ["BYYD", STATE.formats.filter(f => f.family === "byyd").map(f => ({ value: f.key, label: f.key.replace(/^byyd_/, ""), checked: f.visible }))],
    ["Digital Alliance", STATE.formats.filter(f => f.family === "da").map(f => ({ value: f.key, label: f.key.replace(/^da_/, ""), checked: f.visible }))],
    ["KZ", STATE.formats.filter(f => f.family === "social" && f.supports_regions).map(f => ({ value: `${f.key}-KZ`, label: f.key, checked: false }))],
  ];
  const addChip = (wrap, value, label, checked) => {
    const lab = document.createElement("label");
    lab.className = "fmt-chip" + (checked ? " on" : "");
    const cb = document.createElement("input"); cb.type = "checkbox";
    cb.checked = checked; cb.value = value;
    cb.onchange = () => { lab.classList.toggle("on", cb.checked); syncToggleAllLabel(); };
    const vis = document.createElement("span"); vis.className = "chk-vis"; vis.setAttribute("aria-hidden", "true");
    const tx = document.createElement("span"); tx.textContent = label;
    lab.append(cb, vis, tx); wrap.appendChild(lab);
  };
  groups.forEach(([title, items]) => {
    if (!items.length) return;
    const sec = document.createElement("div"); sec.className = "export-section";
    const master = document.createElement("label"); master.className = "export-master";
    const mcb = document.createElement("input"); mcb.type = "checkbox"; mcb.className = "family-check"; mcb.checked = items.every(x => x.checked);
    const mvis = document.createElement("span"); mvis.className = "chk-vis"; mvis.setAttribute("aria-hidden", "true");
    master.append(mcb, mvis, document.createTextNode(title));
    const grid = document.createElement("div"); grid.className = "export-section-grid";
    items.forEach(x => addChip(grid, x.value, x.label, x.checked));
    const syncMaster = () => {
      const kids = [...grid.querySelectorAll("input")];
      const any = kids.some(cb => cb.checked);
      mcb.checked = kids.length && any;
      mcb.indeterminate = false;
    };
    grid.addEventListener("change", syncMaster);
    mcb.onchange = () => {
      const target = !!mcb.checked;
      mcb.indeterminate = false;
      grid.querySelectorAll("input").forEach(cb => {
        cb.checked = target;
        cb.closest(".fmt-chip")?.classList.toggle("on", target);
      });
      mcb.checked = target;
      syncToggleAllLabel();
    };
    syncMaster();
    sec.append(master, grid); host.appendChild(sec);
  });
  const hasNow = !!(await api().has_export_now?.());
  const nowCb = $("#exp-now");
  const nowRow = $("#exp-now-row");
  if (nowCb) { nowCb.disabled = !hasNow; nowCb.checked = hasNow; }
  if (nowRow) { nowRow.classList.toggle("disabled", !hasNow); nowRow.title = hasNow ? "" : "Сначала загрузите картинку «Уже в кино»"; }

  // Доступность площадки берём из ДВУХ источников: глобальная проверка по всем
  // форматам + платформенный файл текущего формата из уже загруженных настроек.
  // Так тумблер не блокируется по ошибке, если один из сигналов подвис.
  let hasPlatform = !!(await api().has_platform_legal?.());
  if (!hasPlatform && LAST_SETTINGS && LAST_SETTINGS.legal && LAST_SETTINGS.legal.platform_legal_file) {
    hasPlatform = true;
  }
  const platformCb = $("#exp-platform");
  const platformRow = $("#exp-platform-row");
  if (platformCb) {
    platformCb.disabled = !hasPlatform;
    if (!hasPlatform) platformCb.checked = false;   // не форсим включение — пусть решает пользователь
  }
  if (platformRow) { platformRow.classList.toggle("disabled", !hasPlatform); platformRow.title = hasPlatform ? "" : "Сначала загрузите юр.информацию площадки"; }

  syncToggleAllLabel();
  syncExportRunBtn();
  $("#modal-export").classList.add("open");
}
function syncToggleAllLabel() {
  const boxes = $all("#exp-formats .fmt-chip input");
  $all("#exp-formats .export-section").forEach(sec => {
    const master = sec.querySelector(".family-check");
    const kids = [...sec.querySelectorAll(".fmt-chip input")];
    if (master) {
      master.checked = kids.length && kids.some(cb => cb.checked);
      master.indeterminate = false;
    }
  });
  const allOn = boxes.length && boxes.every(b => b.checked);
  const btn = $("#exp-toggle-all");
  if (btn) btn.textContent = allOn ? "Снять все" : "Выбрать все";
}
/* п.10 v0.5: заблокировать кнопку экспорта, если все выходные форматы отключены */
function syncExportRunBtn() {
  const anyOutput = $("#exp-png").checked || $("#exp-jpeg").checked || $("#exp-psd").checked;
  const btn = $("#exp-run");
  btn.disabled = !anyOutput;
  btn.title = anyOutput ? "" : "Включите хотя бы один формат файла (PNG, JPEG или PSD)";
}
function toggleAllFormats() {
  const boxes = $all("#exp-formats .fmt-chip input");
  const allOn = boxes.length && boxes.every(b => b.checked);
  boxes.forEach(b => { b.checked = !allOn; b.dispatchEvent(new Event("change")); });
  $all("#exp-formats .family-check").forEach(b => { b.checked = !allOn; b.indeterminate = false; });
  syncToggleAllLabel();
}
async function runExport() {
  const formats = $all("#exp-formats .fmt-chip input:checked").map(c => c.value);
  if (!formats.length) { toast("Выберите хотя бы один формат"); return; }
  const dir = await api().pick_export_dir?.();
  if (dir === null || dir === undefined || dir === "") {
    toast("Экспорт отменён");
    return;
  }
  const r = await api().export(dir, formats,
    $("#exp-png").checked, $("#exp-jpeg").checked,
    $("#exp-psd").checked, false, $("#exp-now") ? $("#exp-now").checked : true,
    $("#exp-platform") ? $("#exp-platform").checked : false);
  $("#modal-export").classList.remove("open");
  showExportCelebration(r.count, r.out_dir);
}

/* п.4: финальная анимация после экспорта — крупная и экспрессивная,
   с тёмным фоном как в окне экспорта */
function showExportCelebration(count, dir) {
  const el = document.createElement("div");
  el.className = "export-celebration";
  // sparkles — много, крупные, разлетаются широко и вращаются
  const sparkles = document.createElement("div"); sparkles.className = "ec-sparkles";
  // насыщенные тёплые + один акцентный бирюзовый — хорошо видны на светлой карточке
  const colors = ["#F2A53C", "#D98E2B", "#C8453C", "#E86A3A", "#2FAE8F"];
  const N = 48;
  for (let i = 0; i < N; i++) {
    const s = document.createElement("div"); s.className = "ec-sparkle";
    const angle = (i / N) * Math.PI * 2 + Math.random() * 0.3;
    const dist = 220 + Math.random() * 360;
    s.style.setProperty("--dx", Math.cos(angle) * dist + "px");
    s.style.setProperty("--dy", Math.sin(angle) * dist + "px");
    s.style.setProperty("--rot", (Math.random() * 720 - 360) + "deg");
    s.style.left = "50%"; s.style.top = "50%";
    s.style.background = colors[i % colors.length];
    s.style.animationDelay = (0.05 + Math.random() * 0.45) + "s";
    const sz = (7 + Math.random() * 12);
    s.style.width = sz + "px";
    s.style.height = sz + "px";
    s.style.borderRadius = Math.random() < 0.5 ? "2px" : "50%";
    sparkles.appendChild(s);
  }
  el.appendChild(sparkles);
  const content = document.createElement("div"); content.className = "ec-content";
  const ring = document.createElement("div"); ring.className = "ec-ring";
  ring.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>`;
  const label = document.createElement("div"); label.className = "ec-label";
  label.textContent = "Экспорт завершён";
  const sub = document.createElement("div"); sub.className = "ec-sub";
  sub.textContent = `${count} файл${count === 1 ? "" : count < 5 ? "а" : "ов"} сохранено`;
  content.append(ring, label, sub);
  el.appendChild(content);
  document.body.appendChild(el);
  // держим ~2.5 сек, затем плавно убираем
  setTimeout(() => el.classList.add("fade-out"), 2100);
  setTimeout(() => el.remove(), 2500);
}

/* мини-«праздник» при успешном Sync — нарочно не модалка с подложкой, как у
   экспорта, а компактный всплеск прямо на кнопке: поп + кольцо + искры. */
function syncSuccessBurst(btn) {
  if (!btn) return;
  btn.classList.remove("sync-burst");
  void btn.offsetWidth; // форс-рефлоу, чтобы анимация перезапустилась при повторном клике
  btn.classList.add("sync-burst");
  const N = 6;
  for (let i = 0; i < N; i++) {
    const sp = document.createElement("span");
    sp.className = "sync-spark";
    const angle = (i / N) * Math.PI * 2 + Math.random() * 0.4;
    const dist = 20 + Math.random() * 16;
    sp.style.setProperty("--dx", Math.cos(angle) * dist + "px");
    sp.style.setProperty("--dy", Math.sin(angle) * dist + "px");
    sp.style.animationDelay = (Math.random() * 0.06) + "s";
    btn.appendChild(sp);
    setTimeout(() => sp.remove(), 700);
  }
  setTimeout(() => btn.classList.remove("sync-burst"), 600);
}

/* ---- замена элементов (п.2 v0.5.2 + п.6: доп. строки KZ/фон) ----------- */
const REPLACE_LABELS = { title: "Тайтл", date: "Дата", rating: "ВО" };
let replacePending = {};  // { rowId: {file, dataUrl, row} } — ожидают сохранения

/* строки модалки: 3 основных (текущий регион) + чёрточка + 3 дополнительных */
function buildReplaceRowDefs() {
  const reg = STATE.current_region;
  const main = [
    { id: "title",  kind: "title",  label: "Тайтл", region: reg },
    { id: "date",   kind: "date",   label: "Дата",     region: reg },
    { id: "rating", kind: "rating", label: "ВО",       region: reg },
  ];
  const extra = [
    { id: "date_now",  kind: "date",   label: "Уже в кино", region: "RU", special: "date_now" },
    { id: "date_kz",   kind: "date",       label: "Дата KZ", region: "KZ" },
    { id: "rating_kz", kind: "rating",     label: "ВО KZ",   region: "KZ" },
  ];
  return { main, extra };
}

function makeReplaceRow(row) {
  const el = document.createElement("div"); el.className = "rs-row"; el.dataset.rowId = row.id;
  const lbl = document.createElement("div"); lbl.className = "rs-row-label";
  lbl.textContent = row.label;
  const thumb = document.createElement("div"); thumb.className = "rs-row-thumb";
  thumb.textContent = "…";
  (async () => {
    try {
      let src = null;
      src = await api().get_element_thumb?.(row.kind, row.region, row.special === "date_now" ? "now" : "");
      if (src) {
        thumb.innerHTML = ""; const im = document.createElement("img"); im.src = src;
        thumb.appendChild(im);
        adaptAlphaBg(im, thumb);
      }
      else thumb.textContent = "—";
    } catch (e) { thumb.textContent = "—"; }
  })();
  const arrow = document.createElement("div"); arrow.className = "rs-row-arrow"; arrow.textContent = "→";
  const dz = document.createElement("div"); dz.className = "rs-row-drop";
  dz.innerHTML = `<span>Перетащите PNG</span>`;
  const del = document.createElement("button");
  del.type = "button"; del.className = "rs-remove"; del.textContent = "×";
  del.title = "Удалить материал";
  del.onclick = async (e) => {
    e.stopPropagation();
    delete replacePending[row.id];
    const variant = row.special === "date_now" ? "now" : "";
    const kind = row.kind;
    const res = await api().remove_element?.(kind, row.region || "RU", variant);
    if (!res || !res.ok) { toast(res?.error || "Не удалось удалить"); return; }
    invalidateCache();
    STATE = await api().switch_region(STATE.current_region);
    await selectFormat(CURRENT, true);
    openReplaceAll();
    toast("Материал удалён");
  };
  const input = document.createElement("input");
  input.type = "file"; input.accept = "image/png"; input.style.display = "none";
  el.appendChild(input);
  const openPicker = () => input.click();
  el.addEventListener("click", (e) => {
    if (e.target.closest("button")) return;
    openPicker();
  });
  input.onchange = () => { if (input.files[0]) handleRowFile(row, input.files[0], dz); };
  const setDrag = (on) => { el.classList.toggle("drag", on); dz.classList.toggle("drag", on); };
  el.addEventListener("dragover", e => { e.preventDefault(); setDrag(true); });
  el.addEventListener("dragleave", e => { if (!el.contains(e.relatedTarget)) setDrag(false); });
  el.addEventListener("drop", e => {
    e.preventDefault(); setDrag(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleRowFile(row, file, dz);
  });
  el.append(lbl, thumb, arrow, dz, del);
  return el;
}

function openReplaceAll() {
  replacePending = {};
  const host = $("#replace-rows"); host.innerHTML = "";
  const { main, extra } = buildReplaceRowDefs();
  main.forEach(row => host.appendChild(makeReplaceRow(row)));
  // чёрточка-разделитель перед дополнительными элементами (п.6)
  const sep = document.createElement("div"); sep.className = "rs-divider";
  host.appendChild(sep);
  extra.forEach(row => host.appendChild(makeReplaceRow(row)));
  $("#replace-save").disabled = true;
  $("#modal-replace").classList.add("open");
}

async function handleRowFile(row, file, dz) {
  const dataUrl = await readFileAsDataURL(file);
  const thumbUrl = await makeVisibleThumbDataURL(dataUrl, 260);
  replacePending[row.id] = { file, dataUrl, row };
  dz.classList.add("filled"); dz.innerHTML = "";
  const im = document.createElement("img"); im.src = thumbUrl; dz.appendChild(im);
  adaptAlphaBg(im, dz);
  const rm = document.createElement("button");
  rm.type = "button"; rm.className = "rs-drop-remove"; rm.textContent = "×";
  rm.onclick = (e) => { e.stopPropagation(); delete replacePending[row.id]; dz.classList.remove("filled"); dz.innerHTML = `<span>Перетащите PNG</span>`; $("#replace-save").disabled = !Object.keys(replacePending).length; };
  dz.appendChild(rm);
  const hint = document.createElement("div");
  hint.style.cssText = "font-size:10px;color:var(--ink-60);margin-top:4px";
  hint.textContent = file.name; dz.appendChild(hint);
  $("#replace-save").disabled = false;
}

function wireReplaceDrop() {
  $("#replace-back").onclick = () => { $("#modal-replace").classList.remove("open"); };
  $("#replace-save").onclick = async () => {
    const ids = Object.keys(replacePending);
    if (!ids.length) return;
    let done = 0;
    for (const id of ids) {
      const { file, dataUrl, row } = replacePending[id];
      let res;
      if (row.special === "date_now") {
        // загрузка/замена варианта «Уже в кино» для текущего региона (п.9)
        res = await api().import_date_now?.(row.region || "RU", file.name, dataUrl);
        if (!res) res = { ok: true };
      } else {
        res = await api().import_element(row.kind, row.region, file.name, dataUrl);
      }
      if (!res || !res.ok) { toast(res?.error || `Не удалось заменить «${row.label}»`); }
      else done++;
    }
    $("#modal-replace").classList.remove("open");
    invalidateCache();
    STATE = await api().switch_region(STATE.current_region);
    await selectFormat(CURRENT, true);
    prefetchOthers();
    toast(`Заменено элементов: ${done}`);
  };
}

document.addEventListener("DOMContentLoaded", wireReplaceDrop);

/* ===========================================================================
   v24: «Танец» логотипа-персонажа.
   При движении курсора персонаж пританцовывает: ножки топают (противофаза),
   руки тянутся вверх-вниз (scaleY от локтя), тело слегка покачивается.
   «Задор» (energy) копится от скорости мыши и плавно затухает ~1 c после
   остановки, ритм мягкий (~1.4 Гц) — чтобы не суетился. Уважает
   prefers-reduced-motion. Работает сразу для всех .pk-logo на странице.
   =========================================================================== */
function initLogoDance() {
  const svgs = Array.from(document.querySelectorAll(".pk-logo"));
  if (!svgs.length) return;

  const nodes = svgs.map(svg => ({
    foreL: svg.querySelector(".pk-fore-l"),
    foreR: svg.querySelector(".pk-fore-r"),
    legL:  svg.querySelector(".pk-leg-l"),
    legR:  svg.querySelector(".pk-leg-r"),
  }));

  let energy = 0, phase = 0, lx = null, ly = null, lt = 0;
  window.addEventListener("pointermove", e => {
    const t = performance.now();
    if (lx !== null) {
      const dt = Math.max(t - lt, 1);
      const v = Math.hypot(e.clientX - lx, e.clientY - ly) / dt;   // px/ms
      energy = Math.min(1, energy + Math.min(v, 2.2) * 0.11);
    }
    lx = e.clientX; ly = e.clientY; lt = t;
  }, { passive: true });

  const reduce = window.matchMedia &&
                 window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce) return;

  function frame() {
    energy *= 0.945;                       // затухание
    const a = energy;
    phase += 0.115 + a * 0.05;             // мягкий базовый ритм + чуть бодрее при движении
    const s = Math.sin(phase);
    const o = Math.sin(phase + Math.PI);   // противофаза → ножки топают по очереди
    // Ножки топают через сжатие от ВЕРХНЕЙ точки (transform-origin: top): стопа
    // поднимается к телу, но низ ноги остаётся у корпуса — нога не залезает внутрь.
    const legL = 1 - a * 0.4 * (0.5 + 0.5 * s);
    const legR = 1 - a * 0.4 * (0.5 + 0.5 * o);
    // Руки пульсируют «короче ↔ полная длина» (scaleY ≤ 1 от локтя): кисть
    // тянется вверх и обратно, но предплечье НЕ вылезает ниже локтя.
    const armL = 1 - a * 0.34 * (0.5 + 0.5 * s);
    const armR = 1 - a * 0.34 * (0.5 + 0.5 * o);
    // анимируем ТОЛЬКО конечности: ножки топают, руки тянутся вверх-вниз.
    for (const n of nodes) {
      if (n.legL)  n.legL.style.transform  = `scaleY(${legL.toFixed(3)})`;
      if (n.legR)  n.legR.style.transform  = `scaleY(${legR.toFixed(3)})`;
      if (n.foreL) n.foreL.style.transform = `scaleY(${armL.toFixed(3)})`;
      if (n.foreR) n.foreR.style.transform = `scaleY(${armR.toFixed(3)})`;
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}
document.addEventListener("DOMContentLoaded", initLogoDance);
