"use strict";
/* 도소매(sme_retail) 업종 화면: 대시보드 카드, 변경이력 라벨, 추천 질문, 입력폼.
   core/web/index.html 셸의 공용 함수($, api, makeBox, lineChart, field, submitForm 등)를 그대로 쓴다. */
const LAYOUT_KEY = "ssot.layout.v3";

const TABLE_KO = { sales: "매출", inventory: "재고", clients: "거래처", products: "상품" };
const FIELD_KO = { qty: "수량", sell_price: "판매가", list_price: "판매가(정가)", cost_price: "사입가", status: "상태", churn_date: "이탈일", reorder_level: "재주문 기준", date: "판매일", name: "거래처명", "신규 등록": "신규 등록" };
const VAL_KO = { active: "거래중", churned: "이탈" };

async function loadDashboard() {
  const [dash, look] = await Promise.all([api(`/api/dashboard?role=${encodeURIComponent(state.role)}`), api(`/api/lookups?role=${encodeURIComponent(state.role)}`)]);
  state.dash = dash; state.lookups = look;
  renderDashboard();
}

const MIN_SIZE = { kpi: [3, 5], chat: [3, 6], chart: [3, 5], inventory: [3, 5], log: [3, 5] };          // [최소 폭(칸), 최소 높이(칸)]
const DEFAULT_SIZE = { kpi: [6, 6], chat: [5, 13], chart: [12, 9], inventory: [6, 13], log: [6, 13] };
// 기본 배치 [x, y, 폭, 높이] (칸 단위, 12칸 그리드). 사용자가 직접 정한 배치를 기본값으로 고정한 것이다.
// 권한이 없어 없는 카드(예: 영업부의 순현금흐름 차트)가 있으면 나머지가 빈틈 없이 위로 붙는다. KPI 카드 높이는 내용에 맞춰 자동으로 정해진다.
const DEFAULT_LAYOUT = {
  "kpi-prev": [0, 0, 3, 6], "kpi-current": [3, 0, 3, 6], "chart-net_cash_flow": [6, 0, 6, 6],
  "chart-revenue": [0, 6, 6, 7], "chart-purchases": [0, 13, 6, 7], "chat": [6, 6, 6, 14],
  "inventory": [0, 20, 6, 13], "log": [6, 20, 6, 13],
};

function inventoryHTML(inv) {
  const low = inv.filter(i => i.status === "부족").length;
  return (low ? `<div class="notice" style="margin-bottom:10px">재고 부족 ${low}개 상품이 있어요</div>` : "") +
    `<table><thead><tr><th>상품</th><th class="num">재고</th><th>재주문 기준 대비</th><th>상태</th></tr></thead><tbody>` +
    inv.map(i => { const max = Math.max(i.qty, i.reorder_level * 3, 1); return `<tr><td>${esc(i.product)}</td><td class="num">${i.qty.toLocaleString()}${esc(i.unit)}</td>
      <td><div class="stock" title="재주문 기준 ${i.reorder_level}${esc(i.unit)}"><i class="${i.status === "부족" ? "low" : ""}" style="width:${(i.qty / max * 100).toFixed(1)}%"></i><b style="left:${(i.reorder_level / max * 100).toFixed(1)}%"></b></div></td>
      <td><span class="badge ${i.status === "부족" ? "low" : "ok"}">${i.status}</span></td></tr>`; }).join("") + `</tbody></table>`;
}

function renderDashboard() {
  const d = state.dash, byMonth = Object.fromEntries(d.months.map(m => [m.month, m]));
  const years = [...new Set(d.months.map(m => +m.month.slice(0, 4)))].sort().reverse().filter(y => byMonth[`${y - 1}-01`]);
  const ysel = $("#year"); ysel.innerHTML = years.map(y => `<option value="${y}">${y}년 vs ${y - 1}년</option>`).join("");
  if (!years.includes(state.year)) state.year = years[0]; ysel.value = state.year;
  const pm = d.months.find(m => m.partial);
  $("#partial-note").textContent = pm ? `${pm.month}은 ${d.today.slice(8)}일까지의 부분 월 (속이 빈 점)` : "";
  $("#partial-note").hidden = !pm;

  state.observers.forEach(o => o.disconnect()); state.observers = [];
  const boxes = [], draws = [];   // boxes: [id, node]

  // 전월(마감): 전년 동월과 비교 / 당월(마감 전): 1일~오늘을 전년 동기간과 비교. 서버가 매번 DB에서 계산하므로 저장하면 즉시 반영된다
  const METRICS = [["revenue", "매출", "won"], ["purchases", "매입", "won"], ["gross_margin_pct", "매출총이익률", "pct"], ["net_cash_flow", "순현금흐름", "won"]];
  const cell = ([key, label, kind], v, base, cmpLabel) => {
    if (v[key] == null) return "";
    const val = kind === "pct" ? `${v[key].toFixed(1)}%` : `${short(v[key])}원`;
    let cmp = "";
    if (base[key] != null) {
      if (kind === "pct") { const dp = v[key] - base[key]; cmp = `${cmpLabel} ${base[key].toFixed(1)}% <span class="${dp >= 0 ? "up" : "down"}">(${dp >= 0 ? "+" : ""}${dp.toFixed(1)}%p)</span>`; }
      else if (key === "net_cash_flow") cmp = `${cmpLabel} ${short(base[key])}원`;
      else if (base[key]) { const pc = (v[key] / base[key] - 1) * 100, cls = key === "purchases" ? "neutral" : pc >= 0 ? "up" : "down"; cmp = `${cmpLabel} <span class="${cls}">${pc >= 0 ? "▲" : "▼"} ${Math.abs(pc).toFixed(1)}%</span>`; }
    }
    return `<div class="metric"><div class="label">${label}</div><div class="value ${key === "net_cash_flow" && v[key] < 0 ? "down" : ""}">${val}</div><div class="sub">${cmp}</div></div>`;
  };
  const monthCard = (id, name, chip, sn, cmpLabel) => { const cells = METRICS.map(mt => cell(mt, sn.values, sn.last_year, cmpLabel)).filter(Boolean); boxes.push([id, makeBox(id, "kpi", name + " 요약",
    `${GRIP}<div class="kbody"><div class="mhead"><div class="mtitle"><b>${name}</b><span class="mmonth">${sn.month}</span></div>${chip}</div><div class="mgrid" data-n="${cells.length}" style="--n:${cells.length}">${cells.join("")}</div></div>`)]); };
  const sn = d.snapshots;
  monthCard("kpi-prev", "전월", '<span class="chip closed">마감 완료</span>', sn.prev_month, "전년 동월");
  monthCard("kpi-current", "당월", `<span class="chip live"><i class="pulse"></i>진행 중 · ${sn.current_month.through_day}일까지 · 저장 즉시 반영</span>`, sn.current_month, "전년 동기간");
  boxes.push(["chat", ensureChatBox()]);

  // 차트: 한 줄에 하나씩 (기본), 크기·위치는 사용자가 바꿀 수 있다
  const Y = state.year;
  const defs = [{ key: "revenue", title: "월별 매출", show: true }, { key: "purchases", title: "월별 매입", show: d.months.some(m => "purchases" in m) },
    { key: "net_cash_flow", title: "월별 순현금흐름", show: d.show_cash_flow, zero: true }].filter(c => c.show);
  for (const c of defs) {
    const box = makeBox("chart-" + c.key, "chart", c.title, `<div class="chart-head"><div class="ttl">${GRIP}<h2>${c.title}</h2></div><div class="legend"><span><i></i>${Y}년</span><span><i class="dash"></i>${Y - 1}년</span></div></div><div class="chart"></div>`);
    const host = $(".chart", box);
    const series = yr => Array.from({ length: 12 }, (_, i) => { const m = byMonth[`${yr}-${String(i + 1).padStart(2, "0")}`]; return { v: m && m[c.key] != null ? m[c.key] : null, partial: !!(m && m.partial), label: `${yr}년` }; });
    const draw = () => lineChart(host, { thisSeries: series(Y), lastSeries: series(Y - 1), fmt: short, tipFmt: man });
    let timer = 0;   // 크기 변화가 연속으로 올 때(끌기 중) 한 번씩만 다시 그린다. rAF 는 가려진 창에서 멈추므로 쓰지 않는다
    const redraw = () => { clearTimeout(timer); timer = setTimeout(draw, 30); };
    const ro = new ResizeObserver(redraw); ro.observe(host); state.observers.push(ro);   // 창 크기 변경 등
    box.addEventListener("boxresize", redraw);   // 카드 크기 조절은 관찰자에 기대지 않고 직접 알린다 (창이 백그라운드여도 동작)
    draws.push(draw);
    boxes.push(["chart-" + c.key, box]);
  }

  if (d.inventory) boxes.push(["inventory", makeBox("inventory", "inventory", "재고 현황", `<div class="chart-head"><div class="ttl">${GRIP}<h2>재고 현황</h2></div></div><div class="body">${inventoryHTML(d.inventory)}</div>`)]);
  const logBox = makeBox("log", "log", "최근 변경이력", `<div class="chart-head"><div class="ttl">${GRIP}<h2>최근 변경이력</h2></div><button class="btn" id="log-all" type="button" style="min-height:32px;padding:4px 12px">전체 보기</button></div><div class="body"><ul class="log"></ul></div>`);
  renderLog($(".log", logBox), d.change_log); $("#log-all", logBox).onclick = openLog;
  boxes.push(["log", logBox]);

  mountBoxes(boxes);
  draws.forEach(f => f());   // 화면에 붙은 직후 실제 크기로 즉시 그린다
}

function nameOf(table, id) {
  const L = state.lookups; if (!L) return id;
  if (table === "clients") return L.clients.find(c => c.client_id === id)?.name || id;
  if (table === "products" || table === "inventory") return L.products.find(p => p.product_id === id)?.name || id;
  return "#" + id;
}

const SUGGEST_BASE = ["올해 3월 매출이 올랐는데 왜지?", "왜 7월부터 계속 떨어지지?", "재고 부족한 상품 있어?", "최근 변경이력 보여줘"];
// 금전(순현금흐름) 권한이 없는 역할에게는 어차피 거절될 질문을 추천하지 않는다
const suggestions = () => state.dash?.show_cash_flow ? [...SUGGEST_BASE.slice(0, 3), "올해 월별 순현금흐름 알려줘", SUGGEST_BASE[3]] : SUGGEST_BASE;

const TOOL_KO = { get_monthly_summary: "월별 매출·자금 흐름", get_monthly_price_trend: "판매가·사입가 추이", get_client_sales_trend: "거래처별 매출", get_inventory: "재고 현황", get_product_price: "상품 가격", get_change_log: "변경이력" };

const FORMS = [["sale-new", "매출 등록", "sale"], ["sale-edit", "매출 수정", "sale"], ["inv", "재고", "inventory"], ["client", "거래처", "client"], ["price", "단가", null]];

function renderForm(id) {
  formTab = id;
  $("#ftabs").querySelectorAll(".ftab").forEach(b => b.setAttribute("aria-selected", b.dataset.t === id));
  const body = $("#fbody"), L = state.lookups, see = state.role !== "" && L.products.some(p => "cost_price" in p);
  const goods = L.products.filter(p => p.type === "goods");
  const shell = inner => `<form novalidate><div class="formerr" hidden role="alert"></div>${inner}</form>`;

  if (id === "sale-new") {
    body.innerHTML = shell(`
      ${field("date", "판매일", dateInp("date", L.today))}
      ${field("client_id", "거래처", `<select id="f-client_id">${L.clients.map(c => `<option value="${c.client_id}" ${c.status === "churned" ? "disabled" : ""}>${esc(c.name)}${c.status === "churned" ? " (이탈)" : ""}</option>`).join("")}</select>`)}
      ${field("product_id", "상품", `<select id="f-product_id">${L.products.map(p => `<option value="${p.product_id}">${esc(p.name)}${p.type === "goods" && p.stock != null ? ` · 재고 ${p.stock.toLocaleString()}${esc(p.unit)}` : ""}</option>`).join("")}</select>`)}
      <div class="row2">${field("qty", "수량", inp("qty", "1"))}${field("sell_price", "판매가 (개당, 원)", inp("sell_price", ""))}</div>
      <div class="summary" id="sum"></div>
      <div class="actions"><button class="btn primary" type="submit">매출 등록</button></div>`);
    const f = $("form", body), p = () => L.products.find(x => x.product_id === $("#f-product_id").value);
    const upd = () => {
      const q = Number($("#f-qty").value.replace(/,/g, "")) || 0, pr = Number($("#f-sell_price").value.replace(/,/g, "")) || 0, pd = p();
      const rows = [`<div><span>금액</span><b>${won(q * pr)}</b></div>`];
      if ("cost_price" in pd) { const m = pr - pd.cost_price; rows.push(`<div><span>사입가 (서버가 자동 적용)</span><span>${won(pd.cost_price)}</span></div><div><span>개당 마진</span><span class="${m < 0 ? "down" : ""}">${won(m)}${pr ? ` (${(m / pr * 100).toFixed(1)}%)` : ""}</span></div>`); }
      if (pd.type === "goods" && pd.stock != null) rows.push(`<div><span>등록 후 재고</span><span class="${pd.stock - q < 0 ? "down" : ""}">${(pd.stock - q).toLocaleString()}${esc(pd.unit)}</span></div>`);
      $("#sum").innerHTML = rows.join("");
    };
    const setPrice = () => { $("#f-sell_price").value = p().list_price; upd(); };
    $("#f-product_id").onchange = setPrice; ["#f-qty", "#f-sell_price"].forEach(s => $(s).oninput = upd); setPrice();
    f.onsubmit = async e => {
      e.preventDefault(); const errs = [];
      const qty = parseIntField($("#f-qty").value, "수량", errs, "qty", { positive: true }), price = parseIntField($("#f-sell_price").value, "판매가", errs, "sell_price");
      if (!$("#f-date").value) errs.push({ field: "date", message: "판매일: 값을 입력해 주세요" });
      if (errs.length) return showErrors(f, errs);
      showErrors(f, []);
      const r = await submitForm(f, "POST", "/api/sales", { date: $("#f-date").value, client_id: $("#f-client_id").value, product_id: $("#f-product_id").value, qty, sell_price: price }, r => `매출 #${r.sale_id} 등록 완료${r.stock_after != null ? ` · 재고 ${r.stock_after.toLocaleString()}` : ""}`);
      if (r && formTab === "sale-new") renderForm("sale-new");
    };
  }

  if (id === "sale-edit") {   // 안쪽에 자체 <form>이 있으므로 바깥은 form 이 아닌 div (form 중첩은 브라우저가 무시함)
    body.innerHTML = (`<div>
      <div class="toolbar"><select id="e-client" aria-label="거래처 필터"><option value="">전체 거래처</option>${L.clients.map(c => `<option value="${c.client_id}">${esc(c.name)}</option>`).join("")}</select>
      <select id="e-month" aria-label="월 필터"></select></div>
      <div class="pick"><table><thead><tr><th>날짜</th><th>거래처</th><th>상품</th><th class="num">수량</th><th class="num">판매가</th></tr></thead><tbody id="e-rows"></tbody></table></div>
      <div id="e-form"><div class="empty">수정할 매출을 위 목록에서 선택하세요</div></div></div>`);
    const months = [...new Set(state.dash.months.map(m => m.month))].reverse(); $("#e-month").innerHTML = months.map(m => `<option>${m}</option>`).join("");
    const load = async () => {
      const r = await api(`/api/sales?role=${encodeURIComponent(state.role)}&limit=60&month=${$("#e-month").value}${$("#e-client").value ? "&client_id=" + $("#e-client").value : ""}`);
      $("#e-rows").innerHTML = r.sales.map(s => `<tr data-id="${s.sale_id}"><td>${s.date.slice(5)}</td><td>${esc(s.client)}</td><td>${esc(s.product)}</td><td class="num">${s.qty}</td><td class="num">${s.sell_price.toLocaleString()}</td></tr>`).join("") || '<tr><td colspan="5" class="empty">해당 매출이 없어요</td></tr>';
      $("#e-rows").querySelectorAll("tr[data-id]").forEach((tr, i) => tr.onclick = () => { $("#e-rows").querySelectorAll("tr").forEach(x => x.classList.remove("sel")); tr.classList.add("sel"); editSale(r.sales[i]); });
    };
    $("#e-client").onchange = $("#e-month").onchange = load; load();
    const editSale = s => {
      $("#e-form").innerHTML = `<form novalidate><div class="formerr" hidden role="alert"></div>
        <div class="hint" style="margin-bottom:10px">매출 #${s.sale_id} · ${esc(s.client)} · ${esc(s.product)} (거래처·상품이 틀렸다면 새로 등록해 주세요)</div>
        ${field("date", "판매일", dateInp("date", s.date))}<div class="row2">${field("qty", "수량", inp("qty", s.qty))}${field("sell_price", "판매가 (개당, 원)", inp("sell_price", s.sell_price))}</div>
        <div class="actions"><button class="btn primary" type="submit">수정 저장</button></div></form>`;
      const f = $("#e-form form");
      f.onsubmit = async e => {
        e.preventDefault(); const errs = [];
        const qty = parseIntField($("#f-qty").value, "수량", errs, "qty", { positive: true }), price = parseIntField($("#f-sell_price").value, "판매가", errs, "sell_price");
        if (!$("#f-date").value) errs.push({ field: "date", message: "판매일: 값을 입력해 주세요" });
        if (errs.length) return showErrors(f, errs);
        showErrors(f, []);
        const r = await submitForm(f, "PATCH", `/api/sales/${s.sale_id}`, { date: $("#f-date").value, qty, sell_price: price }, () => `매출 #${s.sale_id} 수정 완료`);
        if (r && formTab === "sale-edit") { await load(); $("#e-form").innerHTML = '<div class="empty">수정되었어요. 다른 매출을 선택할 수 있어요</div>'; }
      };
    };
  }

  if (id === "inv") {
    body.innerHTML = shell(`
      ${field("product_id", "상품", `<select id="f-product_id">${goods.map(p => `<option value="${p.product_id}">${esc(p.name)}</option>`).join("")}</select>`)}
      <div class="row2">${field("qty", "재고 수량", inp("qty"))}${field("reorder_level", "재주문 기준", inp("reorder_level"))}</div>
      <div class="hint" style="margin:-4px 0 12px">실사 결과를 반영하는 용도예요. 매출 등록 시에는 재고가 자동으로 차감돼요.</div>
      <div class="actions"><button class="btn primary" type="submit">재고 저장</button></div>`);
    const f = $("form", body), fill = () => { const p = goods.find(x => x.product_id === $("#f-product_id").value); $("#f-qty").value = p.stock; $("#f-reorder_level").value = p.reorder_level; };
    $("#f-product_id").onchange = fill; fill();
    f.onsubmit = async e => {
      e.preventDefault(); const errs = [];
      const qty = parseIntField($("#f-qty").value, "재고 수량", errs, "qty"), rl = parseIntField($("#f-reorder_level").value, "재주문 기준", errs, "reorder_level");
      if (errs.length) return showErrors(f, errs);
      showErrors(f, []);
      const pid = $("#f-product_id").value;
      const r = await submitForm(f, "PATCH", `/api/inventory/${pid}`, { qty, reorder_level: rl }, () => "재고 저장 완료");
      if (r && formTab === "inv") { renderForm("inv"); $("#f-product_id").value = pid; $("#f-product_id").onchange(); }
    };
  }

  if (id === "client") {
    body.innerHTML = shell(`
      <h2>거래처 신규 등록</h2>
      ${field("name", "거래처명", `<input id="f-name" type="text" maxlength="50">`)}${field("joined_date", "등록일", dateInp("joined_date", L.today))}
      <div class="actions" style="margin-bottom:22px"><button class="btn primary" type="submit" id="c-new">등록</button></div>`) +
      `<hr style="border:0;border-top:1px solid var(--line);margin:8px 0 18px">` + shell(`
      <h2>거래처 수정 · 이탈 처리</h2>
      ${field("client_id", "거래처", `<select id="u-client">${L.clients.map(c => `<option value="${c.client_id}">${esc(c.name)}${c.status === "churned" ? " (이탈)" : ""}</option>`).join("")}</select>`)}
      ${field("name", "거래처명", `<input id="u-name" type="text" maxlength="50">`)}
      <div class="row2">${field("status", "상태", `<select id="u-status"><option value="active">거래중</option><option value="churned">이탈</option></select>`)}${field("churn_date", "이탈일", dateInp("churn_date", ""))}</div>
      <div class="actions"><button class="btn primary" type="submit">수정 저장</button></div>`);
    const [fNew, fUp] = body.querySelectorAll("form");
    fNew.onsubmit = async e => {
      e.preventDefault(); const errs = [], name = $("#f-name", fNew).value.trim();
      if (!name) errs.push({ field: "name", message: "거래처명: 값을 입력해 주세요" });
      if (!$("#f-joined_date", fNew).value) errs.push({ field: "joined_date", message: "등록일: 값을 입력해 주세요" });
      if (errs.length) return showErrors(fNew, errs); showErrors(fNew, []);
      const r = await submitForm(fNew, "POST", "/api/clients", { name, joined_date: $("#f-joined_date", fNew).value }, r => `거래처 ${r.client_id} 등록 완료`);
      if (r && formTab === "client") renderForm("client");
    };
    const fill = () => { const c = L.clients.find(x => x.client_id === $("#u-client").value); $("#u-name").value = c.name; $("#u-status").value = c.status; $("#f-churn_date", fUp).value = c.churn_date || ""; sync(); };
    const sync = () => { $("#f-churn_date", fUp).disabled = $("#u-status").value !== "churned"; if ($("#u-status").value !== "churned") $("#f-churn_date", fUp).value = ""; };
    $("#u-client").onchange = fill; $("#u-status").onchange = sync; fill();
    fUp.onsubmit = async e => {
      e.preventDefault(); const name = $("#u-name").value.trim(), errs = [], st = $("#u-status").value, cd = $("#f-churn_date", fUp).value;
      if (!name) errs.push({ field: "name", message: "거래처명: 값을 입력해 주세요" });
      if (st === "churned" && !cd) errs.push({ field: "churn_date", message: "이탈일: 이탈 처리하려면 이탈일을 입력해야 합니다" });
      if (errs.length) return showErrors(fUp, errs); showErrors(fUp, []);
      const id = $("#u-client").value, payload = { name, status: st }; if (st === "churned") payload.churn_date = cd;
      const r = await submitForm(fUp, "PATCH", `/api/clients/${id}`, payload, () => "거래처 수정 완료");
      if (r && formTab === "client") { renderForm("client"); $("#u-client").value = id; $("#u-client").onchange(); }
    };
  }

  if (id === "price") {
    const c = state.lookups.can;
    body.innerHTML = shell(`
      ${field("product_id", "상품", `<select id="f-product_id">${L.products.map(p => `<option value="${p.product_id}">${esc(p.name)}</option>`).join("")}</select>`)}
      <div class="row2">${field("list_price", "판매가 (정가, 원)", inp("list_price", "", c.product_list_price ? "" : "disabled"), c.product_list_price ? "" : "영업실적 수정 권한이 필요해요")}
      ${field("cost_price", "사입가 (원가, 원)", inp("cost_price", "", c.product_cost_price ? "" : "disabled"), c.product_cost_price ? "" : "상품구매 수정 권한이 필요해요")}</div>
      <div class="hint" style="margin-bottom:12px">이미 등록된 매출은 바뀌지 않아요 (판매 당시 단가가 보존돼요). 판매가는 사입가보다 낮을 수 없어요.</div>
      <div class="actions"><button class="btn primary" type="submit">단가 저장</button></div>`);
    const f = $("form", body), fill = () => { const p = L.products.find(x => x.product_id === $("#f-product_id").value); $("#f-list_price").value = p.list_price; $("#f-cost_price").value = p.cost_price ?? ""; };
    $("#f-product_id").onchange = fill; fill();
    f.onsubmit = async e => {
      e.preventDefault(); const errs = [], payload = {};
      if (c.product_list_price) payload.list_price = parseIntField($("#f-list_price").value, "판매가", errs, "list_price");
      if (c.product_cost_price) payload.cost_price = parseIntField($("#f-cost_price").value, "사입가", errs, "cost_price");
      if (errs.length) return showErrors(f, errs); showErrors(f, []);
      const pid = $("#f-product_id").value;
      const r = await submitForm(f, "PATCH", `/api/products/${pid}`, payload, () => "단가 저장 완료");
      if (r && formTab === "price") { renderForm("price"); $("#f-product_id").value = pid; $("#f-product_id").onchange(); }
    };
  }
}

// 폼 탭 활성화: 권한 키가 없는 '단가' 탭은 판매가·사입가 중 하나라도 수정할 수 있으면 연다
const formOk = perm => { const can = state.lookups.can; return perm ? can[perm] : (can.product_list_price || can.product_cost_price); };
