const state = {
  heroes: [],
  traits: new Map(),
  equipment: new Map(),  // equipment_key -> 装备
  board: [],             // hero id 列表
  items: new Map(),      // hero id -> equipment_key 列表，每人最多 3 件
  positions: new Map(),  // 站位格序号 -> hero id
  activeHero: null,      // 当前配装目标
  boardSize: 10,
  costFilter: new Set(),
  traitFilter: new Set(),
  typeFilter: new Set(),
  poolTab: "heroes",
  keyword: "",
  seasonId: null,
  compositions: [],      // 当前赛季的阵容存档
  currentComp: null,     // 正在编辑的存档 id
};

const MAX_ITEMS = 3;
const HEX_ROWS = 4;
const HEX_COLS = 7;

const $ = (id) => document.getElementById(id);

init();

async function init() {
  let data;
  try {
    const response = await fetch("/api/season");
    data = await response.json();
    if (data.error) throw new Error(data.error);
  } catch (error) {
    $("season-meta").textContent = `数据加载失败：${error.message}`;
    return;
  }

  state.heroes = data.heroes;
  data.traits.forEach((trait) => state.traits.set(trait.id, trait));
  (data.equipment || []).forEach((item) => state.equipment.set(item.equipment_key, item));
  const s = data.season;
  state.seasonId = s.id;
  $("season-meta").textContent =
    `${s.season} ${s.name} · 版本 ${s.version} · ${data.heroes.length} 英雄 / ` +
    `${data.traits.length} 羁绊 / ${state.equipment.size} 装备`;

  renderFilters();
  buildHexBoard();
  bindEvents();
  bindDragAndDrop();
  readHash();
  renderPool();
  renderAll();
  refreshCompositions();
}

/* ---------------- 交互 ---------------- */

function bindEvents() {
  $("search").addEventListener("input", (event) => {
    state.keyword = event.target.value.trim().toLowerCase();
    renderPool();
  });
  $("btn-clear").addEventListener("click", () => {
    state.board = [];
    state.items.clear();
    state.positions.clear();
    state.activeHero = null;
    renderAll();
  });
  $("pool-tabs").addEventListener("click", (event) => {
    const tab = event.target.closest(".tab");
    if (!tab) return;
    state.poolTab = tab.dataset.tab;
    document.querySelectorAll("#pool-tabs .tab").forEach((node) =>
      node.classList.toggle("on", node.dataset.tab === state.poolTab));
    document.querySelectorAll(".pool-view").forEach((node) => {
      node.hidden = node.dataset.view !== state.poolTab;
    });
    $("search").placeholder = state.poolTab === "items"
      ? "搜索装备 / 属性 / 效果" : "搜索英雄 / 羁绊 / 技能";
    renderPool();
  });
  document.querySelectorAll("[data-size]").forEach((button) => {
    button.addEventListener("click", () => {
      const next = state.boardSize + Number(button.dataset.size);
      state.boardSize = Math.min(12, Math.max(1, next));
      if (state.board.length > state.boardSize) state.board.length = state.boardSize;
      renderAll();
    });
  });
  window.addEventListener("hashchange", () => { readHash(); renderAll(); });
}

function flash(element, text) {
  const original = element.textContent;
  element.textContent = text;
  setTimeout(() => { element.textContent = original; }, 1200);
}

function toggleHero(id) {
  const index = state.board.indexOf(id);
  if (index >= 0) removeHero(id);
  else if (state.board.length < state.boardSize) {
    state.board.push(id);
    state.activeHero = id;
  } else flash($("board-count"), "人口已满");
  renderAll();
}

function removeHero(id) {
  const index = state.board.indexOf(id);
  if (index < 0) return;
  state.board.splice(index, 1);
  state.items.delete(id);
  cellOf(id).forEach((cell) => state.positions.delete(cell));
  if (state.activeHero === id) state.activeHero = null;
}

function cellOf(heroId) {
  return [...state.positions.entries()]
    .filter(([, id]) => id === heroId)
    .map(([cell]) => cell);
}

// 一个英雄只能占一个格子：落座时先把他从原格子拿走，目标格有人就换位
function placeHero(heroId, cell) {
  if (!state.board.includes(heroId)) return;
  const from = cellOf(heroId)[0];
  const occupant = state.positions.get(cell);
  if (occupant === heroId) return;
  state.positions.set(cell, heroId);
  if (from !== undefined && from !== cell) {
    occupant ? state.positions.set(from, occupant) : state.positions.delete(from);
  }
  renderAll();
}

function heroItems(id) {
  return state.items.get(id) || [];
}

// 同一件装备可以重复携带，所以点击是「加一件」，取下走装备条上的 ✕
function equipItem(key) {
  const heroId = state.activeHero;
  if (!heroId || !state.board.includes(heroId)) {
    flash($("equip-hint"), "先在右侧阵容里点选一个英雄");
    return;
  }
  const carried = heroItems(heroId);
  if (carried.length >= MAX_ITEMS) {
    flash($("equip-hint"), `每位英雄最多 ${MAX_ITEMS} 件装备`);
    return;
  }
  state.items.set(heroId, [...carried, key]);
  renderAll();
}

function unequipItem(heroId, slotIndex) {
  const carried = [...heroItems(heroId)];
  carried.splice(slotIndex, 1);
  carried.length ? state.items.set(heroId, carried) : state.items.delete(heroId);
  renderAll();
}

function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  const size = Number(params.get("size"));
  if (size >= 1 && size <= 12) state.boardSize = size;
  const ids = (params.get("h") || "").split(",").map(Number).filter((id) => id > 0);
  const valid = new Set(state.heroes.map((hero) => hero.id));
  state.board = ids.filter((id) => valid.has(id)).slice(0, state.boardSize);

  // i=英雄id:装备key.装备key,英雄id:装备key
  state.items.clear();
  (params.get("i") || "").split(",").filter(Boolean).forEach((chunk) => {
    const [rawHero, rawItems = ""] = chunk.split(":");
    const heroId = Number(rawHero);
    if (!state.board.includes(heroId)) return;
    const keys = rawItems.split(".").filter((key) => state.equipment.has(key)).slice(0, MAX_ITEMS);
    if (keys.length) state.items.set(heroId, keys);
  });
  // p=格子序号.英雄id_格子序号.英雄id
  state.positions.clear();
  (params.get("p") || "").split("_").filter(Boolean).forEach((chunk) => {
    const [cell, heroId] = chunk.split(".").map(Number);
    if (cell >= 0 && cell < HEX_ROWS * HEX_COLS && state.board.includes(heroId)) {
      state.positions.set(cell, heroId);
    }
  });
  if (!state.board.includes(state.activeHero)) state.activeHero = null;
}

function writeHash() {
  const items = [...state.items.entries()]
    .filter(([heroId, keys]) => state.board.includes(heroId) && keys.length)
    .map(([heroId, keys]) => `${heroId}:${keys.join(".")}`)
    .join(",");
  const positions = [...state.positions.entries()]
    .filter(([, heroId]) => state.board.includes(heroId))
    .map(([cell, heroId]) => `${cell}.${heroId}`)
    .join("_");
  const params = new URLSearchParams({ size: String(state.boardSize), h: state.board.join(",") });
  if (items) params.set("i", items);
  if (positions) params.set("p", positions);
  history.replaceState(null, "", `#${params}`);
}

/* ---------------- 阵容存档 ---------------- */

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (char) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

// 存档只保留 hash 里那几样：英雄、装备、站位、人口
function compositionBody() {
  const items = {};
  state.items.forEach((keys, heroId) => {
    if (state.board.includes(heroId) && keys.length) items[heroId] = keys;
  });
  const positions = {};
  state.positions.forEach((heroId, cell) => {
    if (state.board.includes(heroId)) positions[cell] = heroId;
  });
  return { board: [...state.board], board_size: state.boardSize, items, positions };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`);
  return data;
}

function compHint(text) {
  $("comp-hint").textContent = text;
}

async function refreshCompositions() {
  try {
    const data = await api("/api/compositions");
    state.compositions = data.compositions;
  } catch (error) {
    state.compositions = [];
    compHint(`读取存档失败：${error.message}`);
  }
  renderCompositions();
}

async function saveComposition() {
  const input = $("comp-name");
  const name = input.value.trim();
  if (!name) {
    compHint("请先填写阵容名称");
    input.focus();
    return;
  }
  if (!state.board.length) {
    compHint("阵容是空的，先选几个英雄");
    return;
  }
  try {
    const data = await api("/api/compositions", {
      method: "POST",
      body: JSON.stringify({ name, ...compositionBody() }),
    });
    state.currentComp = data.composition.id;
    input.value = "";
    compHint(`已保存【${data.composition.name}】`);
  } catch (error) {
    compHint(`保存失败：${error.message}`);
  }
  refreshCompositions();
}

// 覆盖：把当前棋盘写回已选存档，名字不变
async function overwriteComposition(id) {
  try {
    const data = await api(`/api/compositions/${id}`, {
      method: "PUT",
      body: JSON.stringify(compositionBody()),
    });
    state.currentComp = id;
    compHint(`已更新【${data.composition.name}】`);
  } catch (error) {
    compHint(`更新失败：${error.message}`);
  }
  refreshCompositions();
}

async function renameComposition(id) {
  const current = state.compositions.find((item) => item.id === id);
  const name = prompt("新的阵容名称", current?.name || "");
  if (name === null || name.trim() === (current?.name || "")) return;
  try {
    await api(`/api/compositions/${id}`, {
      method: "PUT",
      body: JSON.stringify({ name: name.trim() }),
    });
    compHint(`已重命名为【${name.trim()}】`);
  } catch (error) {
    compHint(`重命名失败：${error.message}`);
  }
  refreshCompositions();
}

async function removeComposition(id) {
  const current = state.compositions.find((item) => item.id === id);
  if (!confirm(`确定删除阵容【${current?.name || id}】？此操作不可撤销。`)) return;
  try {
    await api(`/api/compositions/${id}`, { method: "DELETE" });
    if (state.currentComp === id) state.currentComp = null;
    compHint(`已删除【${current?.name || id}】`);
  } catch (error) {
    compHint(`删除失败：${error.message}`);
  }
  refreshCompositions();
}

function applyComposition(id) {
  const comp = state.compositions.find((item) => item.id === id);
  if (!comp) return;
  const valid = new Set(state.heroes.map((hero) => hero.id));
  state.boardSize = comp.board_size;
  state.board = comp.board.filter((heroId) => valid.has(heroId)).slice(0, comp.board_size);
  state.items.clear();
  Object.entries(comp.items || {}).forEach(([heroId, keys]) => {
    const id_ = Number(heroId);
    if (state.board.includes(id_) && keys.length) state.items.set(id_, [...keys]);
  });
  state.positions.clear();
  Object.entries(comp.positions || {}).forEach(([cell, heroId]) => {
    if (state.board.includes(heroId)) state.positions.set(Number(cell), heroId);
  });
  state.activeHero = null;
  state.currentComp = id;
  compHint(`已载入【${comp.name}】，可继续调整后覆盖保存`);
  renderAll();
  renderCompositions();
  closeDrawer();  // 载入后直接看棋盘
}

function renderCompositions() {
  const list = $("comp-list");
  const update = $("btn-comp-update");
  const current = state.compositions.find((item) => item.id === state.currentComp);
  update.hidden = !current;
  if (current) update.textContent = `覆盖保存【${current.name}】`;
  $("btn-comps").textContent = state.compositions.length
    ? `我的阵容 ${state.compositions.length}` : "我的阵容";

  if (!state.compositions.length) {
    list.innerHTML = `<p class="empty">还没有保存过阵容</p>`;
    return;
  }
  list.innerHTML = state.compositions.map((comp) => {
    const on = comp.id === state.currentComp ? " on" : "";
    const time = (comp.updated_at || "").replace("T", " ").slice(0, 16);
    return `<div class="comp-row${on}" data-id="${comp.id}">
      <b class="comp-name">${escapeHtml(comp.name)}</b>
      <i class="comp-meta">${comp.board.length}/${comp.board_size} 人 · ${escapeHtml(time)}</i>
      <span class="comp-actions">
        <button class="mini-btn" data-act="load">载入</button>
        <button class="mini-btn" data-act="overwrite">覆盖</button>
        <button class="mini-btn" data-act="rename">改名</button>
        <button class="mini-btn danger" data-act="delete">删除</button>
      </span>
    </div>`;
  }).join("");
}

/* 抽屉：顶栏按钮打开，遮罩 / ✕ / ESC 关闭，关闭后焦点回到按钮 */

function openDrawer() {
  $("comp-mask").hidden = false;
  $("comp-drawer").hidden = false;
  $("btn-comps").setAttribute("aria-expanded", "true");
  document.body.classList.add("drawer-open");
  refreshCompositions();
  $("comp-name").focus();
}

function closeDrawer() {
  if ($("comp-drawer").hidden) return;
  $("comp-mask").hidden = true;
  $("comp-drawer").hidden = true;
  $("btn-comps").setAttribute("aria-expanded", "false");
  document.body.classList.remove("drawer-open");
  $("btn-comps").focus();
}

$("btn-comps").addEventListener("click", () => {
  $("comp-drawer").hidden ? openDrawer() : closeDrawer();
});
$("btn-comp-close").addEventListener("click", closeDrawer);
$("comp-mask").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDrawer();
});

$("comp-list").addEventListener("click", (event) => {
  const button = event.target.closest(".mini-btn");
  const row = event.target.closest(".comp-row");
  if (!button || !row) return;
  const id = Number(row.dataset.id);
  if (button.dataset.act === "load") applyComposition(id);
  else if (button.dataset.act === "overwrite") overwriteComposition(id);
  else if (button.dataset.act === "rename") renameComposition(id);
  else if (button.dataset.act === "delete") removeComposition(id);
});

$("btn-comp-save").addEventListener("click", saveComposition);
$("btn-comp-update").addEventListener("click", () => {
  if (state.currentComp) overwriteComposition(state.currentComp);
});
$("comp-name").addEventListener("keydown", (event) => {
  if (event.key === "Enter") saveComposition();
});

/* ---------------- 羁绊计算 ---------------- */

function boardHeroes() {
  return state.board.map((id) => state.heroes.find((hero) => hero.id === id)).filter(Boolean);
}

// 英雄自带羁绊 + 携带的转职纹章追加的羁绊（同一个羁绊不重复计）
function heroTraitIds(hero) {
  const ids = new Set(hero.trait_ids);
  heroItems(hero.id).forEach((key) => {
    const trait = state.equipment.get(key)?.trait_id;
    if (trait) ids.add(trait);
  });
  return [...ids];
}

// 同名英雄只算一次羁绊，与游戏内一致
function traitCounts(heroes) {
  const counts = new Map();
  const seen = new Set();
  heroes.forEach((hero) => {
    if (seen.has(hero.hero_key)) return;
    seen.add(hero.hero_key);
    heroTraitIds(hero).forEach((tid) => counts.set(tid, (counts.get(tid) || 0) + 1));
  });
  return counts;
}

/* ---------------- 装备基础属性 ---------------- */

// basicDesc 形如 "+150生命上限 +20%攻击速度 +2法力回复"，按属性名累加
function parseStats(text) {
  const stats = [];
  const pattern = /\+(\d+(?:\.\d+)?)(%?)([^\s+]+)/g;
  let match;
  while ((match = pattern.exec(text || "")) !== null) {
    stats.push({ value: Number(match[1]), percent: match[2] === "%", name: match[3] });
  }
  return stats;
}

function equipmentStats() {
  const totals = new Map();
  state.board.forEach((heroId) => {
    heroItems(heroId).forEach((key) => {
      parseStats(state.equipment.get(key)?.basic_description).forEach((stat) => {
        const label = `${stat.name}${stat.percent ? "%" : ""}`;
        totals.set(label, (totals.get(label) || 0) + stat.value);
      });
    });
  });
  return [...totals.entries()].sort((a, b) => b[1] - a[1]);
}

function tierOf(trait, count) {
  let active = null;
  let next = null;
  trait.levels.forEach((level) => {
    if (count >= level.count) active = level;
    else if (!next) next = level;
  });
  return { active, next };
}

/* ---------------- 渲染 ---------------- */

// 节点没变就别碰 DOM：replaceChildren 会把复用的节点 detach 再 attach，图片跟着重绘
function syncChildren(parent, nodes) {
  const current = parent.childNodes;
  const same = current.length === nodes.length && nodes.every((node, i) => current[i] === node);
  if (!same) parent.replaceChildren(...nodes);
}

// 池子里的卡片不随每次点击重建，否则图片会重新解码，视觉上一闪
function renderAll() {
  writeHash();
  syncPoolSelection();
  renderBoard();
  renderHexBoard();
  renderEquipBar();
  renderEquipStats();
  renderTraits();
  renderSuggestions();
}

function renderFilters() {
  const costs = [...new Set(state.heroes.map((hero) => hero.cost))].sort();
  $("cost-filters").innerHTML = costs
    .map((cost) => `<button class="chip" data-cost="${cost}"><i class="dot cost-${cost}"></i>${cost} 费</button>`)
    .join("");
  $("cost-filters").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;
    const cost = Number(chip.dataset.cost);
    state.costFilter.has(cost) ? state.costFilter.delete(cost) : state.costFilter.add(cost);
    chip.classList.toggle("on");
    renderPool();
  });

  const traits = [...state.traits.values()];
  $("trait-filters").innerHTML = traits
    .map((trait) => `<button class="chip" data-trait="${trait.id}">${escapeHtml(trait.name)}</button>`)
    .join("");
  $("trait-filters").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;
    const id = Number(chip.dataset.trait);
    state.traitFilter.has(id) ? state.traitFilter.delete(id) : state.traitFilter.add(id);
    chip.classList.toggle("on");
    renderPool();
  });

  const types = [...new Set([...state.equipment.values()].map((item) => item.type))];
  $("equip-filters").innerHTML = types
    .map((type) => `<button class="chip" data-type="${escapeHtml(type)}">${escapeHtml(type)}</button>`)
    .join("");
  $("equip-filters").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;
    const type = chip.dataset.type;
    state.typeFilter.has(type) ? state.typeFilter.delete(type) : state.typeFilter.add(type);
    chip.classList.toggle("on");
    renderPool();
  });
}

function matchKeyword(hero) {
  if (!state.keyword) return true;
  const haystack = [
    hero.name,
    hero.skill_name || "",
    hero.skill_description || "",
    ...hero.trait_ids.map((id) => state.traits.get(id)?.name || ""),
  ].join(" ").toLowerCase();
  return haystack.includes(state.keyword);
}

function renderPool() {
  state.poolTab === "items" ? renderEquipPool() : renderHeroPool();
}

// 只改选中态与提示文案，不碰卡片本身
function syncPoolSelection() {
  const picked = new Set(state.board);
  document.querySelectorAll("#hero-grid .hero-card").forEach((card) => {
    card.classList.toggle("picked", picked.has(Number(card.dataset.id)));
  });
  updateEquipHint();
}

function updateEquipHint() {
  const target = state.heroes.find((hero) => hero.id === state.activeHero);
  $("equip-hint").textContent = target
    ? `正在为【${target.name}】配装（${heroItems(target.id).length}/${MAX_ITEMS}），点击装备装上`
    : `先在右侧阵容里点选一个英雄，再点击装备为他装上（每人 ${MAX_ITEMS} 件）`;
}

function renderHeroPool() {
  const list = state.heroes.filter((hero) =>
    (!state.costFilter.size || state.costFilter.has(hero.cost)) &&
    (!state.traitFilter.size || [...state.traitFilter].every((id) => hero.trait_ids.includes(id))) &&
    matchKeyword(hero));

  $("hero-grid").innerHTML = list.length
    ? list.map(heroCard).join("")
    : `<p class="empty">没有符合条件的英雄</p>`;
}

function renderEquipPool() {
  const keyword = state.keyword;
  const list = [...state.equipment.values()].filter((item) =>
    (!state.typeFilter.size || state.typeFilter.has(item.type)) &&
    (!keyword || [item.name, item.type, item.basic_description || "", item.description || ""]
      .join(" ").toLowerCase().includes(keyword)));

  updateEquipHint();
  $("equip-grid").innerHTML = list.length
    ? list.map(equipCard).join("")
    : `<p class="empty">没有符合条件的装备</p>`;
}

function equipCard(item) {
  const trait = item.trait_id ? state.traits.get(item.trait_id) : null;
  return `<article class="equip-card" data-key="${escapeHtml(item.equipment_key)}" title="${escapeHtml(item.description || "")}">
    <img loading="lazy" src="${escapeHtml(item.picture)}" alt="${escapeHtml(item.name)}" />
    <div class="equip-text">
      <b>${escapeHtml(item.name)}</b>
      <i>${escapeHtml(item.basic_description || item.type)}</i>
    </div>
    ${trait ? `<span class="emblem">${escapeHtml(trait.name)}</span>` : ""}
  </article>`;
}

$("equip-grid").addEventListener("click", (event) => {
  const card = event.target.closest(".equip-card");
  if (card) equipItem(card.dataset.key);
});
$("equip-grid").addEventListener("contextmenu", (event) => {
  const card = event.target.closest(".equip-card");
  if (!card) return;
  event.preventDefault();
  showItemDetail(card.dataset.key);
});

// 统一使用 1624×750 横版原画，具体裁切交给 CSS 的 object-fit / object-position
function heroArt(hero) {
  return hero.picture_big_local || hero.picture_big ||
    hero.picture_local || hero.picture_small || hero.picture;
}

function heroCard(hero) {
  const names = hero.trait_ids.map((id) => state.traits.get(id)?.name).filter(Boolean).join(" · ");
  const picked = state.board.includes(hero.id) ? " picked" : "";
  return `<article class="hero-card${picked}" data-cost="${hero.cost}" data-id="${hero.id}">
    <img loading="lazy" src="${escapeHtml(heroArt(hero))}" alt="${escapeHtml(hero.name)}" />
    <i class="cost cost-${hero.cost}">${hero.cost}</i>
    <b class="name">${escapeHtml(hero.name)}</b>
    <i class="traits">${escapeHtml(names)}</i>
  </article>`;
}

$("hero-grid").addEventListener("click", (event) => {
  const card = event.target.closest(".hero-card");
  if (card) toggleHero(Number(card.dataset.id));
});
$("hero-grid").addEventListener("contextmenu", (event) => {
  const card = event.target.closest(".hero-card");
  if (!card) return;
  event.preventDefault();
  showHeroDetail(Number(card.dataset.id));
});

// 按 hero id 复用已有格子，避免每次操作都重新加载原画
function renderBoard() {
  const grid = $("board-grid");
  const heroes = boardHeroes();
  const existing = new Map(
    [...grid.querySelectorAll(".slot.filled")].map((node) => [Number(node.dataset.id), node]));

  const nodes = heroes.map((hero) => {
    let slot = existing.get(hero.id);
    if (!slot) {
      slot = document.createElement("div");
      slot.className = "slot filled";
      slot.dataset.id = String(hero.id);
      slot.draggable = true;
      slot.innerHTML = `<div class="slot-art">
          <img src="${escapeHtml(heroArt(hero))}" alt="${escapeHtml(hero.name)}" />
          <div class="slot-items"></div>
          <button class="slot-off" title="移出阵容" aria-label="移出阵容">✕</button>
        </div>
        <span class="slot-name">${escapeHtml(hero.name)}</span>`;
    }
    slot.classList.toggle("on", state.activeHero === hero.id);
    const icons = heroItems(hero.id)
      .map((key) => `<img class="mini" src="${escapeHtml(state.equipment.get(key)?.picture || "")}" alt="" />`).join("");
    const box = slot.querySelector(".slot-items");
    if (box.innerHTML !== icons) box.innerHTML = icons;
    return slot;
  });

  // 空位也复用，否则每次渲染都产生新节点，整块棋盘会被判定为「变了」
  const spares = [...grid.querySelectorAll(".slot:not(.filled)")];
  for (let index = heroes.length; index < state.boardSize; index += 1) {
    let empty = spares.shift();
    if (!empty) {
      empty = document.createElement("div");
      empty.className = "slot";
      empty.innerHTML = `<div class="slot-art">空位</div><span class="slot-name"></span>`;
    }
    nodes.push(empty);
  }
  syncChildren(grid, nodes);

  $("board-count").textContent = `${heroes.length} / ${state.boardSize}`;
  $("board-cost").textContent = `${heroes.reduce((sum, hero) => sum + hero.cost, 0)} 金`;
  $("board-size").textContent = String(state.boardSize);
}

// 点角标 ✕（或右键）移出阵容，点格子其余位置是选中配装
$("board-grid").addEventListener("click", (event) => {
  const slot = event.target.closest(".slot.filled");
  if (!slot) return;
  const id = Number(slot.dataset.id);
  if (event.target.closest(".slot-off")) removeHero(id);
  else state.activeHero = state.activeHero === id ? null : id;
  renderAll();
});
$("board-grid").addEventListener("contextmenu", (event) => {
  const slot = event.target.closest(".slot.filled");
  if (!slot) return;
  event.preventDefault();
  removeHero(Number(slot.dataset.id));
  renderAll();
});

/* ---------------- 站位棋盘 ---------------- */

// 28 个格子只建一次，之后只换里面的英雄
function buildHexBoard() {
  const board = $("hex-board");
  for (let row = 0; row < HEX_ROWS; row += 1) {
    const line = document.createElement("div");
    line.className = `hex-row${row % 2 ? " offset" : ""}`;
    for (let col = 0; col < HEX_COLS; col += 1) {
      const cell = document.createElement("div");
      cell.className = "hex";
      cell.dataset.cell = String(row * HEX_COLS + col);
      line.appendChild(cell);
    }
    board.appendChild(line);
  }
}

function renderHexBoard() {
  document.querySelectorAll("#hex-board .hex").forEach((cell) => {
    const heroId = state.positions.get(Number(cell.dataset.cell));
    const hero = heroId ? state.heroes.find((item) => item.id === heroId) : null;
    cell.classList.toggle("filled", Boolean(hero));
    cell.classList.toggle("on", Boolean(hero) && state.activeHero === hero.id);
    cell.draggable = Boolean(hero);
    if (!hero) {
      if (cell.firstChild) cell.replaceChildren();
      cell.dataset.hero = "";
      return;
    }
    if (cell.dataset.hero !== String(hero.id)) {
      cell.dataset.hero = String(hero.id);
      cell.innerHTML = `<img src="${escapeHtml(heroArt(hero))}" alt="${escapeHtml(hero.name)}" />
        <span class="hex-name">${escapeHtml(hero.name)}</span>
        <div class="hex-items"></div>`;
    }
    const icons = heroItems(hero.id)
      .map((key) => `<img class="mini" src="${escapeHtml(state.equipment.get(key)?.picture || "")}" alt="" />`).join("");
    const box = cell.querySelector(".hex-items");
    if (box.innerHTML !== icons) box.innerHTML = icons;
  });

  const placed = [...state.positions.values()].filter((id) => state.board.includes(id)).length;
  $("hex-hint").textContent = state.board.length
    ? `${placed} / ${state.board.length} 已站位`
    : "拖动英雄到格子，格子之间可互换";
}

let dragHeroId = null;
let dragGhost = null;

// 拖影用圆形头像，浏览器默认会把整个方格连同名字一起拖着走
function setDragGhost(event, heroId) {
  const hero = state.heroes.find((item) => item.id === heroId);
  if (!hero || !event.dataTransfer.setDragImage) return;
  dragGhost?.remove();
  dragGhost = document.createElement("div");
  dragGhost.className = "drag-ghost";
  dragGhost.innerHTML = `<img src="${escapeHtml(heroArt(hero))}" alt="" />`;
  document.body.appendChild(dragGhost);
  const size = dragGhost.offsetWidth / 2;
  event.dataTransfer.setDragImage(dragGhost, size, size);
}

function bindDragAndDrop() {
  // 阵容格与站位格都能拖起，落点统一是站位格
  const start = (event) => {
    const source = event.target.closest(".slot.filled, .hex.filled");
    if (!source) return;
    dragHeroId = Number(source.dataset.id || source.dataset.hero);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", String(dragHeroId));
    setDragGhost(event, dragHeroId);
  };
  const end = () => {
    dragGhost?.remove();
    dragGhost = null;
    dragHeroId = null;
  };
  [$("board-grid"), $("hex-board")].forEach((node) => {
    node.addEventListener("dragstart", start);
    node.addEventListener("dragend", end);
  });

  $("hex-board").addEventListener("dragover", (event) => {
    const cell = event.target.closest(".hex");
    if (!cell) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    cell.classList.add("over");
  });
  $("hex-board").addEventListener("dragleave", (event) => {
    event.target.closest(".hex")?.classList.remove("over");
  });
  $("hex-board").addEventListener("drop", (event) => {
    const cell = event.target.closest(".hex");
    if (!cell) return;
    event.preventDefault();
    cell.classList.remove("over");
    const heroId = Number(event.dataTransfer.getData("text/plain") || dragHeroId);
    if (heroId) placeHero(heroId, Number(cell.dataset.cell));
    dragHeroId = null;
  });

  // 双击把英雄请下场（只清站位，不动阵容）
  $("hex-board").addEventListener("dblclick", (event) => {
    const cell = event.target.closest(".hex.filled");
    if (!cell) return;
    state.positions.delete(Number(cell.dataset.cell));
    renderAll();
  });
  $("hex-board").addEventListener("click", (event) => {
    const cell = event.target.closest(".hex.filled");
    if (!cell) return;
    const heroId = Number(cell.dataset.hero);
    state.activeHero = state.activeHero === heroId ? null : heroId;
    renderAll();
  });
}

function renderEquipBar() {
  const bar = $("equip-bar");
  const hero = state.heroes.find((item) => item.id === state.activeHero);
  if (!hero) {
    bar.hidden = true;
    return;
  }
  const carried = heroItems(hero.id);
  const slots = Array.from({ length: MAX_ITEMS }, (_, index) => {
    const item = state.equipment.get(carried[index]);
    return item
      ? `<span class="equip-slot filled" data-slot="${index}" title="${escapeHtml(item.name)}">
           <img src="${escapeHtml(item.picture)}" alt="${escapeHtml(item.name)}" /><i class="off">✕</i>
         </span>`
      : `<span class="equip-slot" data-slot="${index}">＋</span>`;
  }).join("");
  const emblems = carried
    .map((key) => state.equipment.get(key)?.trait_id)
    .filter(Boolean)
    .map((id) => state.traits.get(id)?.name)
    .filter(Boolean);

  bar.hidden = false;
  bar.innerHTML = `<b>${escapeHtml(hero.name)}</b>${slots}
    <span class="hint">${escapeHtml(emblems.length ? `纹章追加：${emblems.join("、")}` : "到装备库点击装备")}</span>`;
}

$("equip-bar").addEventListener("click", (event) => {
  const slot = event.target.closest(".equip-slot");
  if (!slot || !state.activeHero) return;
  if (slot.classList.contains("filled")) unequipItem(state.activeHero, Number(slot.dataset.slot));
  else document.querySelector('#pool-tabs .tab[data-tab="items"]').click();
});

function renderEquipStats() {
  const stats = equipmentStats();
  $("equip-stats").innerHTML = stats.length
    ? stats.map(([label, value]) => {
        const percent = label.endsWith("%");
        return `<span class="stat"><b>${escapeHtml(value)}${percent ? "%" : ""}</b>${escapeHtml(percent ? label.slice(0, -1) : label)}</span>`;
      }).join("")
    : `<p class="empty">阵容里还没有装备</p>`;
}

function renderTraits() {
  const counts = traitCounts(boardHeroes());
  const rows = [...counts.entries()]
    .map(([id, count]) => {
      const trait = state.traits.get(id);
      const { active, next } = tierOf(trait, count);
      return { trait, count, active, next };
    })
    .sort((a, b) =>
      (b.active ? b.active.color : -1) - (a.active ? a.active.color : -1) ||
      b.count - a.count || a.trait.name.localeCompare(b.trait.name));

  const activeCount = rows.filter((row) => row.active).length;
  $("trait-summary").textContent = rows.length ? `${activeCount} 个生效 / ${rows.length} 个在场` : "";

  const list = $("active-traits");
  if (!rows.length) {
    list.innerHTML = `<p class="empty">选择英雄后这里会显示羁绊档位</p>`;
    return;
  }
  // 羁绊图标是官网 CDN 地址，重建节点会重新解码，复用同一行只改文字
  syncChildren(list, rows.map(traitRow));
}

const traitNodes = new Map();

function traitRowNode(trait) {
  let node = traitNodes.get(trait.id);
  if (!node) {
    node = document.createElement("div");
    node.className = "trait-row";
    node.dataset.trait = String(trait.id);
    node.innerHTML = `<div class="trait-line">
      <img src="${escapeHtml(trait.picture)}" alt="" />
      <span class="badge"></span>
      <span class="tname">${escapeHtml(trait.name)}</span>
      <span class="steps"></span>
    </div>
    <p class="effect"></p>
    <p class="effect dim"></p>`;
    traitNodes.set(trait.id, node);
  }
  return node;
}

// 效果文本自带 "(4)" 这样的档位前缀，档位在徽章里已经显示，去掉更好读
function effectText(level) {
  return (level?.effect || "").replace(/^\(\d+\)\s*/, "");
}

function traitRow({ trait, count, active, next }) {
  const node = traitRowNode(trait);
  node.classList.toggle("inactive", !active);

  const badge = node.querySelector(".badge");
  badge.className = `badge${active ? ` tier-${active.color}` : ""}`;
  badge.textContent = count;

  const steps = trait.levels.map((level) =>
    level === active ? `<b>${level.count}</b>` : level.count).join(" / ");
  const tail = next ? `还差 ${next.count - count}` : "已满级";
  node.querySelector(".steps").innerHTML = `${steps} · ${tail}`;

  const [effect, upgrade] = node.querySelectorAll(".effect");
  effect.className = active ? "effect" : "effect dim";
  effect.textContent = active
    ? effectText(active)
    : `${next.count} 人激活：${effectText(next)}`;
  upgrade.hidden = !(active && next);
  upgrade.textContent = active && next ? `↑ ${next.count} 人：${effectText(next)}` : "";
  return node;
}

$("active-traits").addEventListener("click", (event) => {
  const row = event.target.closest(".trait-row");
  if (row) showTraitDetail(Number(row.dataset.trait));
});

// 推荐：加入后能立刻推进一个羁绊档位的英雄，按推进档位数与费用排序
function renderSuggestions() {
  const heroes = boardHeroes();
  if (!heroes.length || heroes.length >= state.boardSize) {
    $("suggestions").innerHTML = `<p class="empty">${heroes.length ? "人口已满，可提高人口或替换英雄" : "先选择几个英雄"}</p>`;
    return;
  }
  const counts = traitCounts(heroes);
  const onBoard = new Set(state.board);

  const scored = state.heroes
    .filter((hero) => !onBoard.has(hero.id))
    .map((hero) => {
      const gains = [];
      hero.trait_ids.forEach((id) => {
        const trait = state.traits.get(id);
        if (!trait) return;
        const before = counts.get(id) || 0;
        const beforeTier = tierOf(trait, before).active;
        const afterTier = tierOf(trait, before + 1).active;
        if (afterTier && afterTier !== beforeTier) gains.push(`${trait.name} ${afterTier.count}`);
      });
      return { hero, gains };
    })
    .filter((item) => item.gains.length)
    .sort((a, b) => b.gains.length - a.gains.length || a.hero.cost - b.hero.cost)
    .slice(0, 8);

  const list = $("suggestions");
  if (!scored.length) {
    list.innerHTML = `<p class="empty">没有能直接推进档位的英雄</p>`;
    return;
  }
  syncChildren(list, scored.map(suggestRow));
}

const suggestNodes = new Map();

// 同样复用节点，推荐列表里的原画不必每次重新解码
function suggestRow({ hero, gains }) {
  let node = suggestNodes.get(hero.id);
  if (!node) {
    node = document.createElement("div");
    node.className = "suggest-row";
    node.dataset.id = String(hero.id);
    node.innerHTML = `<img src="${escapeHtml(heroArt(hero))}" alt="" />
      <span>${escapeHtml(hero.name)}</span><i class="cost cost-${hero.cost}">&nbsp;${hero.cost}费&nbsp;</i>
      <span class="why"></span>`;
    suggestNodes.set(hero.id, node);
  }
  node.querySelector(".why").textContent = gains.join("、");
  return node;
}

$("suggestions").addEventListener("click", (event) => {
  const row = event.target.closest(".suggest-row");
  if (row) toggleHero(Number(row.dataset.id));
});

/* ---------------- 详情浮层 ---------------- */

function showDetail(html) {
  const box = $("detail");
  box.hidden = false;
  box.innerHTML = `<button class="close" aria-label="关闭">✕</button>${html}`;
  box.querySelector(".close").onclick = () => { box.hidden = true; };
}

function showHeroDetail(id) {
  const hero = state.heroes.find((item) => item.id === id);
  if (!hero) return;
  const names = hero.trait_ids.map((tid) => state.traits.get(tid)?.name).filter(Boolean).join(" · ");
  showDetail(`<img class="art" src="${escapeHtml(heroArt(hero))}" alt="${escapeHtml(hero.name)}" />
    <h3>${escapeHtml(hero.name)}</h3>
    <p class="sub">${hero.cost} 费 · ${escapeHtml(names)}</p>
    <p><b>${escapeHtml(hero.skill_name || "技能")}</b><br />${escapeHtml(hero.skill_description || "—")}</p>
    <ul>${hero.skill_values.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>`);
}

function showItemDetail(key) {
  const item = state.equipment.get(key);
  if (!item) return;
  const parts = [item.component_1_key, item.component_2_key]
    .map((componentKey) => state.equipment.get(componentKey)?.name)
    .filter(Boolean);
  const trait = item.trait_id ? state.traits.get(item.trait_id) : null;
  showDetail(`<h3>${escapeHtml(item.name)}</h3>
    <p class="sub">${escapeHtml(item.type)}${parts.length ? ` · ${escapeHtml(parts.join(" + "))}` : ""}</p>
    <p><b>${escapeHtml(item.basic_description || "无基础属性")}</b></p>
    <p>${escapeHtml(item.description || "")}</p>
    ${trait ? `<p class="sub">携带者获得羁绊：${escapeHtml(trait.name)}</p>` : ""}`);
}

function showTraitDetail(id) {
  const trait = state.traits.get(id);
  if (!trait) return;
  const counts = traitCounts(boardHeroes());
  const count = counts.get(id) || 0;
  const owners = state.heroes
    .filter((hero) => hero.trait_ids.includes(id))
    .map((hero) => `${hero.name}(${hero.cost})`).join("、");
  showDetail(`<h3>${escapeHtml(trait.name)}</h3>
    <p class="sub">当前 ${count} 人 · ${trait.type === "race" ? "特质" : "职业"}</p>
    <p>${escapeHtml(trait.prefix || "")}</p>
    ${trait.levels.map((level) =>
      `<div class="lv"><b class="badge tier-${level.color}">${level.count}</b><span>${escapeHtml(level.effect)}</span></div>`).join("")}
    <p class="sub" style="margin-top:10px">拥有该羁绊：${escapeHtml(owners)}</p>`);
}
