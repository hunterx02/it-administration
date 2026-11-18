/* IT Administration — production shell (API-backed). No secrets, no business data in localStorage.
   localStorage holds UI conveniences only (sidebar collapse, last company filter). */
(function (global) {
  'use strict';
  const App = global.App = { pages: {}, me: null, companies: [], cache: {} };

  // ------------------------------------------------------------ utils
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = (s, r) => (r || document).querySelector(s), $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const fmtDate = v => v ? String(v).slice(0, 10) : '—';
  const fmtDT = v => v ? String(v).slice(0, 16).replace('T', ' ') : '—';
  const num = v => v == null || v === '' ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
  const store = { get(k, d) { try { const v = localStorage.getItem('itadm.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('itadm.' + k, JSON.stringify(v)); } catch (e) { /* optional */ } } };
  Object.assign(App, { esc, $, $$, fmtDate, fmtDT, num, store });

  // ------------------------------------------------------------ API
  async function api(method, path, body, opts) {
    opts = opts || {};
    const init = { method, credentials: 'same-origin', headers: { 'X-ITADM-Request': '1' } };
    if (body instanceof FormData) init.body = body;
    else if (body !== undefined) { init.body = JSON.stringify(body); init.headers['Content-Type'] = 'application/json'; }
    let res;
    try { res = await fetch(path, init); } catch (e) { throw new Error('السيرفر مش متاح — اتأكد إن run.bat شغال'); }
    if (res.status === 401 && !opts.noAuthRedirect) { App.showLogin(); throw new Error('سجّل الدخول'); }
    const ct = res.headers.get('content-type') || '';
    const data = ct.includes('json') ? await res.json() : await res.text();
    if (!res.ok) {
      let d = data && data.detail !== undefined ? data.detail : data;
      if (Array.isArray(d)) d = d.map(x => (x.loc ? x.loc.slice(-1)[0] + ': ' : '') + x.msg).join(' · ');
      else if (d && typeof d === 'object') d = (d.message || '') + (d.missing ? '\n• ' + d.missing.join('\n• ') : '');
      const err = new Error(String(d || res.statusText)); err.status = res.status; throw err;
    }
    return data;
  }
  App.api = api;
  App.get = p => api('GET', p);
  App.post = (p, b) => api('POST', p, b === undefined ? {} : b);
  App.put = (p, b) => api('PUT', p, b);
  App.patch = (p, b) => api('PATCH', p, b);
  App.qs = o => { const u = new URLSearchParams(); Object.entries(o || {}).forEach(([k, v]) => { if (v !== '' && v != null) u.set(k, v); }); const s = u.toString(); return s ? '?' + s : ''; };
  App.upload = async (file, category, companyId) => {
    const fd = new FormData(); fd.append('category', category); fd.append('file', file); if (companyId) fd.append('company_id', companyId);
    return api('POST', '/api/files', fd);
  };

  // ------------------------------------------------------------ UI kit
  App.toast = (msg, kind) => {
    const t = document.createElement('div'); t.className = 'toast ' + (kind || 'ok'); t.setAttribute('role', 'status');
    t.textContent = msg; $('#toasts').appendChild(t); setTimeout(() => t.remove(), kind === 'bad' ? 7000 : 3500);
  };
  App.fail = e => { console.error(e); App.toast(e.message || String(e), 'bad'); };
  App.badge = (text, kind) => `<span class="badge b-${kind || 'muted'}"><i aria-hidden="true">•</i>${esc(text)}</span>`;
  App.loading = el => { el.innerHTML = '<div class="empty" aria-busy="true">جاري التحميل…</div>'; };
  App.emptyBox = (msg, action) => `<div class="empty">${esc(msg)}${action ? '<div style="margin-top:10px">' + action + '</div>' : ''}</div>`;
  App.errorBox = e => `<div class="notice bad" role="alert">${esc(e.message || e)}</div>`;

  App.modal = function ({ title, body, wide, actions, onOpen }) {
    const wrap = document.createElement('div'); wrap.className = 'modal-wrap';
    wrap.innerHTML = `<div class="modal ${wide ? 'wide' : ''}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <header><h2 style="margin:0">${esc(title)}</h2><button class="icon-btn" data-x aria-label="إغلاق">✕</button></header>
      <div class="modal-body">${body || ''}</div>
      ${actions && actions.length ? `<footer>${actions.map((a, i) => `<button class="btn ${a.primary ? 'primary' : ''} ${a.danger ? 'danger' : ''}" data-a="${i}">${esc(a.label)}</button>`).join('')}<span class="spacer"></span><button class="btn ghost" data-x>إلغاء</button></footer>` : ''}</div>`;
    const close = () => { wrap.remove(); document.removeEventListener('keydown', onKey); };
    const onKey = e => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    wrap.addEventListener('click', async e => {
      if (e.target === wrap || e.target.closest('[data-x]')) return close();
      const b = e.target.closest('[data-a]'); if (!b) return;
      const a = actions[+b.dataset.a]; b.disabled = true;
      try { const r = await a.onClick(wrap, close); if (r !== false && !a.keepOpen) close(); } catch (err) { App.fail(err); } finally { b.disabled = false; }
    });
    document.body.appendChild(wrap);
    const first = wrap.querySelector('input,select,textarea,button:not([data-x])'); if (first) first.focus();
    if (onOpen) onOpen(wrap, close);
    return close;
  };
  App.confirm = (title, text) => new Promise(res => App.modal({ title, body: `<p>${esc(text)}</p>`, actions: [{ label: 'تأكيد', primary: true, onClick: () => res(true) }] }));

  /* ---- editable dropdowns: every list gets ➕ add / ✏️ rename / 🗑 remove next to it (when permitted) ---- */
  App.lookups = {};
  App.lookupValues = key => ((App.lookups[key] || {}).items || []).filter(i => i.active !== false).map(i => i.value);
  App.loadLookups = async () => { try { (await App.get('/api/lookups')).forEach(l => { App.lookups[l.key] = l; }); } catch (e) { /* optional */ } };
  function pickBtns(f) {
    const kind = f.lookup ? 'lookup' : f.pick;
    const can = { lookup: () => (App.lookups[f.lookup] || {}).can_edit, department: () => App.can('system:edit'), employee: () => App.can('employees:create'), supplier: () => App.can('finance:create') }[kind];
    if (!can || !can()) return '';
    const full = kind === 'lookup' || kind === 'department';
    return `<span class="pick-btns" data-kind="${kind}" data-key="${esc(f.lookup || '')}" data-company="${esc(f.company || '')}">
      <button type="button" class="icon-btn" data-pick="add" title="إضافة جديد" aria-label="إضافة جديد">➕</button>${full ? '<button type="button" class="icon-btn" data-pick="edit" title="تعديل الاسم المختار" aria-label="تعديل">✏️</button><button type="button" class="icon-btn" data-pick="del" title="حذف المختار من القائمة" aria-label="حذف">🗑</button>' : ''}</span>`;
  }
  const askText = (title, value) => new Promise(res => App.modal({ title, body: `<label class="field"><span>الاسم</span><input id="askV" value="${esc(value || '')}"></label>`,
    onOpen: m => { const i = m.querySelector('#askV'); i.focus(); i.select(); i.onkeydown = e => { if (e.key === 'Enter') m.querySelector('[data-a="0"]').click(); }; },
    actions: [{ label: 'حفظ', primary: true, onClick: m => { const v = m.querySelector('#askV').value.trim(); if (!v) throw new Error('اكتب الاسم'); res(v); } }] }));
  document.addEventListener('click', async e => {
    const b = e.target.closest('[data-pick]'); if (!b) return;
    const box = b.closest('.pick-btns'), sel = box.parentElement.querySelector('select'), kind = box.dataset.kind, key = box.dataset.key, act = b.dataset.pick;
    const form = b.closest('form') || document;
    const company = (form.querySelector('[name="company_id"]') || {}).value || box.dataset.company || App.scopeCompany() || (App.companies[0] || {}).id;
    const setOpt = (value, label) => { let o = Array.from(sel.options).find(x => x.value === value); if (!o) { o = new Option(label, value); sel.add(o); } o.textContent = label; sel.value = value; sel.dispatchEvent(new Event('change', { bubbles: true })); };
    try {
      if (kind === 'lookup') {
        const items = (App.lookups[key] || {}).items || [], cur = items.find(i => i.value === sel.value);
        if (act === 'add') { const v = await askText('إضافة إلى «' + ((App.lookups[key] || {}).label || '') + '»'); const r = await App.post('/api/lookups/' + key, { value: v }); items.push(r); setOpt(r.value, r.value); }
        if (act === 'edit') { if (!cur) throw new Error('اختار قيمة الأول'); const v = await askText('تعديل الاسم', cur.value); const r = await App.patch(`/api/lookups/${key}/${cur.id}`, { value: v }); Object.assign(cur, r); const o = sel.selectedOptions[0]; o.value = r.value; o.textContent = r.value; }
        if (act === 'del') { if (!cur) throw new Error('اختار قيمة الأول'); if (!await App.confirm('حذف من القائمة', `«${cur.value}» هتختفي من الاختيارات الجديدة، والسجلات القديمة هتفضل زي ما هي.`)) return; await App.api('DELETE', `/api/lookups/${key}/${cur.id}`); cur.active = false; sel.selectedOptions[0].remove(); sel.value = ''; }
      } else if (kind === 'department') {
        if (!company) throw new Error('اختار الشركة الأول');
        if (act === 'add') { const v = await askText('قسم جديد'); const r = await App.post(`/api/companies/${company}/departments`, { name: v }); await App.loadCompanies(); setOpt(r.id, r.name); }
        if (act === 'edit') { if (!sel.value) throw new Error('اختار القسم الأول'); const v = await askText('تعديل اسم القسم', sel.selectedOptions[0].textContent); await App.patch(`/api/companies/${company}/departments/${sel.value}`, { name: v }); await App.loadCompanies(); setOpt(sel.value, v); }
        if (act === 'del') { if (!sel.value) throw new Error('اختار القسم الأول'); if (!await App.confirm('تعطيل القسم', 'القسم هيختفي من الاختيارات الجديدة، والسجلات القديمة هتفضل مربوطة بيه.')) return; await App.patch(`/api/companies/${company}/departments/${sel.value}`, { active: false }); await App.loadCompanies(); sel.selectedOptions[0].remove(); sel.value = ''; }
      } else if (kind === 'employee' && act === 'add') {
        const fields = [{ name: 'full_name', label: 'الاسم', required: true }, { name: 'department_id', label: 'القسم', type: 'department', company }, { name: 'job_title', label: 'الوظيفة', lookup: 'job_title' }];
        App.formModal('موظف جديد', fields, {}, async d => { d.company_id = company; d.job_title = d.job_title || ''; const r = await App.post('/api/employees', d); App.cache.employees = null; setOpt(r.id, r.full_name); });
      } else if (kind === 'supplier' && act === 'add') {
        const v = await askText('مورد جديد'); const r = await App.post('/api/finance/suppliers', { name: v }); App.cache.suppliers = null; setOpt(r.id, r.name);
      }
    } catch (err) { App.fail(err); }
  });

  /* form fields: {name,label,type:text|number|date|select|textarea|checkbox|file|company|department|employee|hidden, options, required, hint, full, value} */
  App.field = f => {
    const v = f.value == null ? '' : f.value, req = f.required ? ' required' : '', id = 'f_' + f.name + '_' + Math.random().toString(36).slice(2, 7);
    const lab = `<span>${esc(f.label)}${f.required ? ' *' : ''}</span>`;
    const hint = f.hint ? `<small>${esc(f.hint)}</small>` : '';
    if (f.type === 'hidden') return `<input type="hidden" name="${f.name}" value="${esc(v)}">`;
    if (f.type === 'checkbox') return `<label class="field check ${f.full ? 'full' : ''}"><input type="checkbox" name="${f.name}" ${v ? 'checked' : ''}> ${esc(f.label)}</label>`;
    /* checkgroup: several ticks side by side in one row; value = array of the checked keys */
    if (f.type === 'checkgroup') {
      const on = new Set(Array.isArray(v) ? v : String(v || '').split(/[,،]\s*/).filter(Boolean));
      return `<div class="field full checkgroup" data-group="${esc(f.name)}">${lab}<div class="chk-row">${(f.options || []).map(o => {
        const [k, l] = Array.isArray(o) ? o : [o, o];
        return `<label class="chk-pill"><input type="checkbox" name="${esc(f.name)}" value="${esc(k)}" ${on.has(k) || on.has(l) ? 'checked' : ''}><span>${esc(l)}</span></label>`;
      }).join('')}</div>${hint}</div>`;
    }
    let input;
    let opts = f.options;
    if (f.lookup) { const items = App.lookupValues(f.lookup); opts = [['', '—']].concat(items.map(x => [x, x])); if (v && !items.includes(v)) opts.push([v, v + ' (غير موجودة في القائمة)']); }
    if (f.type === 'company') opts = App.companies.map(c => [c.id, c.name]);
    if (f.type === 'department') { opts = [['', '—']].concat(App.departments(f.company).filter(d => d.active !== false || d.id === v).map(d => [d.id, d.name])); f.pick = f.pick || 'department'; }
    if (f.type === 'select' || f.type === 'company' || f.type === 'department' || opts) {
      const list = Array.isArray(opts) ? opts : Object.entries(opts || {});
      input = `<select id="${id}" name="${f.name}"${req}>${list.map(o => { const [k, l] = Array.isArray(o) ? o : [o, o]; return `<option value="${esc(k)}" ${String(k) === String(v) ? 'selected' : ''}>${esc(l)}</option>`; }).join('')}</select>`;
      if (f.lookup || f.pick) input = `<div class="pick-wrap">${input}${pickBtns(f)}</div>`;
    } else if (f.type === 'textarea') input = `<textarea id="${id}" name="${f.name}" rows="3"${req}>${esc(v)}</textarea>`;
    else if (f.type === 'file') input = `<input id="${id}" type="file" name="${f.name}" accept="${esc(f.accept || '.pdf,.png,.jpg,.jpeg,.docx,.xlsx')}"${req}>`;
    else input = `<input id="${id}" type="${f.type || 'text'}" name="${f.name}" value="${esc(v)}"${req}${f.type === 'number' ? ' step="any" dir="ltr"' : ''}${f.ltr ? ' dir="ltr"' : ''}>`;
    return `<label class="field ${f.full ? 'full' : ''}" for="${id}">${lab}${input}${hint}</label>`;
  };
  App.form = (fields, values, cols) => `<form class="grid${cols || 2}" onsubmit="return false">${fields.map(f => App.field(Object.assign({}, f, { value: values && values[f.name] !== undefined ? values[f.name] : f.value }))).join('')}</form>`;
  App.readForm = (root, fields) => {
    const out = {};
    fields.forEach(f => {
      if (f.type === 'checkgroup') { out[f.name] = Array.from(root.querySelectorAll(`[name="${f.name}"]:checked`)).map(x => x.value); return; }
      const el = root.querySelector(`[name="${f.name}"]`); if (!el || f.type === 'file') return;
      if (f.type === 'checkbox') out[f.name] = el.checked;
      else if (f.type === 'number') out[f.name] = el.value === '' ? null : Number(el.value);
      else out[f.name] = el.value === '' ? (f.keepEmpty ? '' : null) : el.value;
      if (f.required && (out[f.name] == null || out[f.name] === '')) throw new Error('حقل مطلوب: ' + f.label);
    });
    return out;
  };
  App.formModal = (title, fields, values, onSave, opts) => App.modal({
    title, wide: opts && opts.wide, body: (opts && opts.intro ? `<div class="notice">${opts.intro}</div>` : '') + App.form(fields, values, opts && opts.cols),
    onOpen: opts && opts.onOpen,
    actions: [{ label: (opts && opts.saveLabel) || 'حفظ', primary: true, onClick: async (el, close) => {
      const data = App.readForm(el, fields);
      for (const f of fields.filter(x => x.type === 'file')) {
        const inp = el.querySelector(`[name="${f.name}"]`);
        if (inp && inp.files[0]) data[f.name] = (await App.upload(inp.files[0], f.category, data.company_id || (values && values.company_id))).id;
        else if (f.required) throw new Error('ارفع ملف: ' + f.label);
        else data[f.name] = null;
      }
      await onSave(data, el); return true;
    } }],
  });

  /* table: cols [{label, render(row) or key, cls}] */
  App.table = (rows, cols, opts) => {
    opts = opts || {};
    if (!rows.length) return App.emptyBox(opts.empty || 'لا توجد بيانات', opts.emptyAction);
    return `<div style="overflow-x:auto"><table class="tbl ${opts.compact ? 'compact' : ''}"><thead><tr>${cols.map(c => `<th class="${c.cls || ''}">${esc(c.label)}</th>`).join('')}</tr></thead><tbody>
      ${rows.map((r, i) => `<tr ${opts.href ? `data-href="${esc(opts.href(r))}" style="cursor:pointer"` : ''} data-i="${i}">${cols.map(c => `<td class="${c.cls || ''}">${c.render ? c.render(r) : esc(r[c.key] == null ? '' : r[c.key])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  };
  document.addEventListener('click', e => { const tr = e.target.closest('tr[data-href]'); if (tr && !e.target.closest('a,button,input,select')) location.hash = tr.dataset.href; });

  // ------------------------------------------------------------ charts (inline SVG, labelled, theme-aware via CSS vars)
  const PALETTE = { assigned: 'var(--c-used)', available: 'var(--c-stock)', in_stock: '#d4a72c', reserved: '#8b5cf6', in_maintenance: 'var(--c-repair)', lost: 'var(--bad)', retired: 'var(--muted)', pending_wipe: '#64748b' };
  App.color = k => PALETTE[k] || ['var(--s1)', 'var(--s2)', '#0f766e', '#8b5cf6', '#d4a72c', '#64748b'][Math.abs(String(k).split('').reduce((a, c) => a + c.charCodeAt(0), 0)) % 6];
  App.donut = (parts, center, sub, opts) => {
    const tot = parts.reduce((a, p) => a + p.value, 0); let acc = 0; const R = 15.915;
    const segs = tot ? parts.filter(p => p.value).map(p => { const len = p.value / tot * 100; const s = `<circle r="${R}" cx="18" cy="18" fill="none" stroke="${p.color}" stroke-width="3.8" stroke-dasharray="${len} ${100 - len}" stroke-dashoffset="${25 - acc}"><title>${esc(p.label)}: ${p.value}</title></circle>`; acc += len; return p.href ? `<a href="${esc(p.href)}">${s}</a>` : s; }).join('') : '';
    const legend = parts.filter(p => p.value).map(p => `<li>${p.href ? `<a href="${esc(p.href)}">` : ''}<i class="dot" style="background:${p.color}"></i>${esc(p.label)} <b>${p.value}</b>${p.href ? '</a>' : ''}</li>`).join('');
    return `<div class="donut-box"><svg viewBox="0 0 36 36" class="donut" role="img" aria-label="${esc(center + ' ' + (sub || ''))}"><circle r="${R}" cx="18" cy="18" fill="none" stroke="var(--surface-2)" stroke-width="3.8"/>${segs}<text x="18" y="18.6" class="d1">${esc(center)}</text><text x="18" y="23.4" class="d2">${esc(sub || '')}</text></svg>${opts && opts.noLegend ? '' : `<ul class="legend">${legend || '<li class="muted">لا توجد بيانات</li>'}</ul>`}</div>`;
  };
  App.bars = (data, opts) => {
    opts = opts || {}; const entries = Object.entries(data || {}); if (!entries.length) return '<div class="muted small">لا توجد بيانات</div>';
    const max = Math.max(1, ...entries.map(e => Number(e[1]) || 0));
    return `<div class="bars">${entries.map(([k, v]) => { const w = (Number(v) || 0) / max * 100; const inner = `<span class="bl">${esc(k)}</span><span class="bt"><i style="width:${w}%;background:${opts.color || 'var(--s1)'}"></i></span><b>${num(v)}</b>`; return opts.href ? `<a class="br" href="${esc(opts.href(k))}">${inner}</a>` : `<div class="br">${inner}</div>`; }).join('')}</div>`;
  };
  App.progress = (pct, kind, label) => `<span class="hbar ${kind || ''}" role="progressbar" aria-valuenow="${Math.round(pct)}" aria-valuemin="0" aria-valuemax="100" title="${esc(label || '')}"><i style="width:${Math.max(0, Math.min(100, pct))}%"></i></span>`;
  App.spark = pts => {
    if (!pts || pts.length < 2) return '<span class="muted small">لا يوجد سجل كافٍ للرسم</span>';
    const vals = pts.map(p => Number(p)), mx = Math.max(...vals), mn = Math.min(...vals), w = 120, h = 28;
    const d = vals.map((v, i) => `${(i / (vals.length - 1) * w).toFixed(1)},${(h - (mx === mn ? h / 2 : (v - mn) / (mx - mn) * (h - 4) - 2)).toFixed(1)}`).join(' ');
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="اتجاه آخر ${vals.length} قراءات" class="spark"><polyline fill="none" stroke="var(--s1)" stroke-width="2" points="${d}"/></svg>`;
  };
  App.line = data => App.spark(Object.values(data || {}));

  /* Semicircle gauge for remaining quota. Arc = remaining share; ticks = per-line thresholds.
     Color follows the server-computed state (never inferred client-side); text carries the meaning too. */
  const STATE_COLOR = { normal: 'var(--c-used)', prepare: 'var(--c-repair)', urgent: 'var(--bad)' };
  App.gauge = ({ remaining, quota, warn, critical, state, unit, label }) => {
    unit = unit || 'GB';
    const W = 200, cx = 100, cy = 100, R = 80, SW = 16;
    const pt = f => { const a = Math.PI * (1 - f); return [cx + R * Math.cos(a), cy - R * Math.sin(a)]; }; // f: 0 = left, 1 = right
    const arc = (f0, f1) => { const [x0, y0] = pt(f0), [x1, y1] = pt(f1); return `M${x0.toFixed(1)},${y0.toFixed(1)} A${R},${R} 0 0 1${x1.toFixed(1)},${y1.toFixed(1)}`; };
    const has = remaining != null && quota;
    const f = has ? Math.max(0, Math.min(1, remaining / quota)) : 0;
    const col = STATE_COLOR[state] || 'var(--muted)';
    const tick = (v, c, t) => { if (v == null || !quota) return ''; const ff = Math.max(0, Math.min(1, v / quota)), a = Math.PI * (1 - ff); const x0 = cx + (R - SW / 2 - 4) * Math.cos(a), y0 = cy - (R - SW / 2 - 4) * Math.sin(a), x1 = cx + (R + SW / 2 + 4) * Math.cos(a), y1 = cy - (R + SW / 2 + 4) * Math.sin(a); return `<line x1="${x0.toFixed(1)}" y1="${y0.toFixed(1)}" x2="${x1.toFixed(1)}" y2="${y1.toFixed(1)}" stroke="${c}" stroke-width="3" stroke-linecap="round"><title>${esc(t)}: ${v} ${unit}</title></line>`; };
    const pct = has ? Math.round(f * 100) : null;
    const aria = has ? `${label || ''} متبقي ${remaining} من ${quota} ${unit} (${pct}%)` : `${label || ''} لا توجد قراءة`;
    return `<svg class="gauge" viewBox="0 0 ${W} 122" role="img" aria-label="${esc(aria)}" direction="ltr">
      <path d="${arc(0, 1)}" fill="none" stroke="var(--surface-2)" stroke-width="${SW}"/>
      ${has && f > 0.002 ? `<path d="${arc(0, f)}" fill="none" stroke="${col}" stroke-width="${SW}"><title>${esc(aria)}</title></path>` : ''}
      ${tick(warn, 'var(--c-repair)', 'عتبة تجهيز الرصيد')}${tick(critical, 'var(--bad)', 'عتبة التجديد العاجل')}
      <text x="${cx}" y="${cy - 14}" text-anchor="middle" class="g1" fill="var(--text)">${has ? esc(Number(remaining).toLocaleString('en-US', { maximumFractionDigits: 1 })) : '—'}</text>
      <text x="${cx}" y="${cy + 4}" text-anchor="middle" class="g2" fill="var(--muted)" direction="rtl">${has ? `${unit} متبقي من ${esc(Number(quota).toLocaleString('en-US'))} · ${pct}%` : 'لا توجد قراءة'}</text>
      <text x="${pt(0)[0]}" y="118" text-anchor="middle" class="g3" fill="var(--muted)">0</text><text x="${pt(1)[0]}" y="118" text-anchor="middle" class="g3" fill="var(--muted)">${has ? esc(String(quota)) : ''}</text></svg>`;
  };

  /* Grouped vertical columns. groups: [{label, values:{seriesName: number}}], series: [{name, color}] */
  App.columns = (groups, series, opts) => {
    opts = opts || {};
    const max = Math.max(1, ...groups.flatMap(g => series.map(s => Number(g.values[s.name]) || 0)));
    if (!groups.length || max <= 1 && groups.every(g => series.every(s => !Number(g.values[s.name])))) return '<div class="muted small">لا توجد بيانات في الفترة</div>';
    const W = 640, H = 220, L = 52, Rp = 10, T = 14, B = 34, iw = W - L - Rp, ih = H - T - B, gw = iw / groups.length, bw = Math.min(26, (gw - 14) / series.length);
    const Y = v => T + ih - v / max * ih;
    const grid = [0, .5, 1].map(k => `<line x1="${L}" x2="${W - Rp}" y1="${Y(max * k).toFixed(1)}" y2="${Y(max * k).toFixed(1)}" stroke="var(--border)"/><text x="${L - 6}" y="${(Y(max * k) + 4).toFixed(1)}" text-anchor="end" class="g3" fill="var(--muted)">${num(Math.round(max * k))}</text>`).join('');
    const bars = groups.map((g, gi) => series.map((s, si) => { const v = Number(g.values[s.name]) || 0, x = L + gi * gw + (gw - bw * series.length) / 2 + si * bw; return `<rect x="${x.toFixed(1)}" y="${Y(v).toFixed(1)}" width="${(bw - 3).toFixed(1)}" height="${(T + ih - Y(v)).toFixed(1)}" rx="3" fill="${s.color}"><title>${esc(g.label)} · ${esc(s.name)}: ${num(v)}${opts.unit ? ' ' + esc(opts.unit) : ''}</title></rect>`; }).join('') + `<text x="${(L + gi * gw + gw / 2).toFixed(1)}" y="${H - 12}" text-anchor="middle" class="g3" fill="var(--muted)">${esc(g.label)}</text>`).join('');
    const legend = series.map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join('');
    return `<svg class="usage-chart" viewBox="0 0 ${W} ${H}" direction="ltr" role="img" aria-label="${esc(opts.label || 'رسم أعمدة')}">${grid}${bars}</svg><div class="gauge-legend">${legend}</div>`;
  };

  /* Remaining-over-time chart with threshold lines. points: [{at, remaining}] oldest -> newest. */
  App.usageChart = (points, { quota, warn, critical, unit } = {}) => {
    unit = unit || 'GB';
    if (!points || points.length < 2) return '<div class="muted small">محتاج قراءتين على الأقل عشان يترسم الاتجاه.</div>';
    const W = 640, H = 220, L = 46, Rp = 12, T = 12, B = 30, iw = W - L - Rp, ih = H - T - B;
    const ts = points.map(p => new Date(p.at).getTime()), t0 = Math.min(...ts), t1 = Math.max(...ts);
    const ymax = Math.max(quota || 0, ...points.map(p => Number(p.remaining) || 0)) || 1;
    const X = t => L + (t1 === t0 ? iw / 2 : (t - t0) / (t1 - t0) * iw), Y = v => T + ih - (v / ymax) * ih;
    const pts = points.map((p, i) => [X(ts[i]), Y(Number(p.remaining) || 0)]);
    const line = pts.map(p => p.map(n => n.toFixed(1)).join(',')).join(' ');
    const area = `M${pts[0][0].toFixed(1)},${(T + ih).toFixed(1)} L${line.replace(/ /g, ' L')} L${pts[pts.length - 1][0].toFixed(1)},${(T + ih).toFixed(1)} Z`;
    const hline = (v, c, t) => v == null ? '' : `<line x1="${L}" x2="${W - Rp}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" stroke="${c}" stroke-dasharray="5 4" stroke-width="1.5"/><text x="${W - Rp}" y="${(Y(v) - 4).toFixed(1)}" text-anchor="start" direction="rtl" class="g3" fill="${c}">${esc(t)} (${v})</text>`;
    const grid = [0, 0.25, 0.5, 0.75, 1].map(k => { const v = Math.round(ymax * k); return `<line x1="${L}" x2="${W - Rp}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" stroke="var(--border)" stroke-width="1"/><text x="${L - 6}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end" class="g3" fill="var(--muted)">${v}</text>`; }).join('');
    const d = t => new Date(t).toISOString().slice(5, 10);
    const dots = pts.map((p, i) => `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="3.5" fill="var(--s1)"><title>${esc(fmtDT(points[i].at))}: ${points[i].remaining} ${unit}</title></circle>`).join('');
    return `<svg class="usage-chart" viewBox="0 0 ${W} ${H}" direction="ltr" role="img" aria-label="المتبقي بالـ ${unit} عبر ${points.length} قراءة: من ${points[0].remaining} إلى ${points[points.length - 1].remaining}">
      ${grid}<path d="${area}" fill="var(--s1)" opacity=".12"/><polyline fill="none" stroke="var(--s1)" stroke-width="2.5" points="${line}"/>${dots}
      ${hline(warn, 'var(--c-repair)', 'تجهيز رصيد')}${hline(critical, 'var(--bad)', 'تجديد عاجل')}
      <text x="${L}" y="${H - 8}" class="g3" fill="var(--muted)">${d(t0)}</text><text x="${W - Rp}" y="${H - 8}" text-anchor="end" class="g3" fill="var(--muted)">${d(t1)}</text></svg>`;
  };

  // ------------------------------------------------------------ permissions / lookups
  App.can = p => !!(App.me && App.me.permissions.includes(p));
  App.canAny = list => list.some(App.can);
  App.departments = companyId => { const c = App.companies.find(x => x.id === companyId) || App.companies[0]; return c ? c.departments : []; };
  App.deptName = id => { for (const c of App.companies) { const d = c.departments.find(x => x.id === id); if (d) return d.name; } return ''; };
  App.companyName = id => (App.companies.find(c => c.id === id) || {}).name || '';
  App.scopeCompany = () => store.get('company', '');
  App.loadCompanies = async () => { App.companies = await App.get('/api/companies'); return App.companies; };
  App.employees = async force => { if (!App.cache.employees || force) App.cache.employees = App.can('employees:view') ? await App.get('/api/employees') : []; return App.cache.employees; };
  App.empOptions = async (filter) => [['', '— اختر الموظف —']].concat((await App.employees(true)).filter(filter || (() => true)).map(e => [e.id, e.full_name + (e.department_name ? ' · ' + e.department_name : '') + (e.custody_frozen ? ' (إخلاء — مجمّد)' : '')]));

  // ------------------------------------------------------------ navigation (M01: 6 groups; sub-pages per approved specs)
  App.NAV = [
    { group: 'الرئيسية', items: [['dashboard', '📊', 'Dashboard', 'dashboard:view']] },
    { group: 'الأصول والعهد', items: [['assets', '💻', 'الأجهزة والموبايلات', 'assets:view'], ['custody', '🔁', 'العهد والحركات', 'assets:view'], ['stock', '📦', 'المخازن والجرد', 'assets:view'], ['maintenance', '🔧', 'الصيانة والضمان', 'assets:view']] },
    { group: 'رحلة الموظف', items: [['employees', '👥', 'الموظفون', 'employees:view'], ['recruitment', '🧑‍💼', 'التوظيف والمقابلات', 'employees:view'], ['onboarding', '🟢', 'التعيينات', 'employees:view'], ['offboarding', '🚪', 'إخلاء الطرف', 'employees:view'], ['documents', '📄', 'المستندات والإقرارات', 'employees:view']] },
    { group: 'الخدمات التقنية', items: [['renewals', '🔁', 'مركز التجديدات', 'technical:view'], ['lines', '🌐', 'الإنترنت والأرضي', 'technical:view'], ['sims-EG', '🇪🇬', 'الشرائح المصرية', 'technical:view'], ['sims-SA', '🇸🇦', 'الشرائح السعودية', 'technical:view'], ['subscriptions', '🪪', 'الاشتراكات والتراخيص', 'technical:view'], ['mailboxes', '✉️', 'الإيميلات والحسابات', 'technical:view'], ['security', '🛡️', 'الأمان والنسخ الاحتياطي', 'technical:view']] },
    { group: 'الإدارة والمالية', items: [['suppliers', '🏭', 'الموردون', 'finance:view'], ['purchases', '🛒', 'طلبات الشراء والاستلام', 'finance:view'], ['expenses', '🧾', 'المصروفات والفواتير والمدفوعات', 'finance:view'], ['budgets', '📊', 'الميزانيات والتقارير المالية', 'finance:view']] },
    { group: 'إدارة النظام', items: [['users', '👤', 'المستخدمون', 'system:manage_users'], ['roles', '🔑', 'الأدوار والصلاحيات', 'system:view'], ['companies', '🏢', 'الشركات والفروع والأقسام', 'system:view'], ['excel', '📥', 'استيراد وتصدير Excel', 'dashboard:view'], ['lists', '🗂️', 'القوائم والإعدادات', 'dashboard:view'], ['integrations', '🔌', 'الربط والأتمتة', 'system:view'], ['appearance', '🎨', 'المظهر والتخصيص', 'dashboard:view'], ['logs', '🕒', 'السجلات والنسخ الاحتياطي', 'system:view']] },
  ];
  const pagePerm = key => { for (const g of App.NAV) for (const it of g.items) if (it[0] === key) return it[3]; return null; };
  App.extraPerms = { asset: 'assets:view', employee: 'employees:view', application: 'employees:view', onboard: 'employees:view', offboard: 'employees:view', sim: 'technical:view', line: 'technical:view', subscription: 'technical:view', mailbox: 'technical:view', purchase: 'finance:view', event: 'finance:view', alerts: 'dashboard:view' };

  function buildNav() {
    const closed = store.get('navClosed', {}), cur = App.route().page;
    const groups = App.NAV.map((g, gi) => ({ g, gi, items: g.items.filter(it => App.can(it[3])) })).filter(x => x.items.length);
    $('#nav').innerHTML = groups.map(({ g, gi, items }) => {
      const solo = g.items.length === 1, open = solo || items.some(it => it[0] === cur) || !closed[g.group];
      const head = solo ? `<div class="nav-title">${esc(g.group)}</div>` : `<button type="button" class="nav-title nav-toggle" aria-expanded="${open}" aria-controls="ng${gi}" data-group="${esc(g.group)}"><span>${esc(g.group)}</span><span class="chev" aria-hidden="true">▾</span></button>`;
      return `<div class="nav-group${open ? '' : ' collapsed'}">${head}<div class="nav-items" id="ng${gi}">${items.map(([k, ic, l]) => `<a href="#/${k}" data-page="${k}"><span class="ic" aria-hidden="true">${ic}</span>${esc(l)}</a>`).join('')}</div></div>`;
    }).join('');
    $('#nav').onclick = e => { const b = e.target.closest('.nav-toggle'); if (!b) return; const c = b.parentElement.classList.toggle('collapsed'); b.setAttribute('aria-expanded', String(!c)); closed[b.dataset.group] = c; store.set('navClosed', closed); };
  }

  App.route = () => {
    const raw = (location.hash || '#/dashboard').slice(2); const [path, q] = raw.split('?');
    const [page, ...rest] = path.split('/');
    return { page: page || 'dashboard', arg: decodeURIComponent(rest.join('/') || ''), query: Object.fromEntries(new URLSearchParams(q || '')) };
  };
  App.go = h => { location.hash = h; };
  async function render() {
    const { page, arg, query } = App.route();
    const perm = pagePerm(page) || App.extraPerms[page.split('-')[0]] || App.extraPerms[page];
    $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.page === page));
    const act = $('#nav a.active'), grp = act && act.closest('.nav-group.collapsed'); if (grp) grp.classList.remove('collapsed');
    const main = $('#main'); main.innerHTML = ''; document.body.classList.remove('nav-open');
    const fn = App.pages[page] || App.pages[page.split('-')[0]];
    if (!fn) { main.innerHTML = App.errorBox('الصفحة غير موجودة'); return; }
    if (perm && !App.can(perm)) { main.innerHTML = App.errorBox('ليس لديك صلاحية فتح هذه الصفحة'); return; }
    App.loading(main);
    try { await fn(main, arg, query, page); } catch (e) { main.innerHTML = App.errorBox(e); console.error(e); }
    main.focus({ preventScroll: true });
  }
  App.render = render;

  // ------------------------------------------------------------ page header helper with the mandatory «تجهيز تقرير»
  /* opts.io = import/export entity key; the module's Google Sheet link button appears automatically */
  const IO_PERMS = { assets: ['assets:create', 'assets:export'], employees: ['employees:create', 'employees:export'], suppliers: ['finance:create', 'finance:export'], sims: ['technical:create', 'technical:export'], lines: ['technical:create', 'technical:export'], subscriptions: ['technical:create', 'technical:export'], mailboxes: ['technical:create', 'technical:export'] };
  const SHEET_MODULE = { assets: 'assets', journey: 'employees', technical: 'technical', finance: 'finance' };
  App.head = (title, sub, actions, reportModule, opts) => {
    opts = opts || {};
    const links = (App.cache.links || []).filter(l => l.module === SHEET_MODULE[reportModule]);
    const sheet = links.map(l => `<a class="btn ghost" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer" title="${esc(l.title_ar)}">📄 افتح الشيت</a>`).join('');
    const io = opts.io && IO_PERMS[opts.io] ? (App.can(IO_PERMS[opts.io][0]) ? `<button class="btn ghost" data-import="${opts.io}">⬆ استيراد</button>` : '') + (App.can(IO_PERMS[opts.io][1]) ? `<button class="btn ghost" data-export="${opts.io}">⬇ تصدير</button>` : '') : '';
    return `<div class="page-head"><div><h1>${esc(title)}</h1>${sub ? `<p class="sub">${sub}</p>` : ''}</div><div class="actions">${actions || ''}${sheet}${io}${reportModule && App.can('reports:view') ? `<button class="btn" data-report="${reportModule}">📊 تجهيز تقرير</button>` : ''}</div></div>`;
  };
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-report]'); if (b) return App.reportModal(b.dataset.report);
    const ex = e.target.closest('[data-export]'); if (ex) return App.modal({ title: 'تصدير', body: '<p>الملف بيطلع بنفس أعمدة الاستيراد — تقدر تعدّل فيه وترجّع تستورده.</p>', actions: [{ label: 'Excel (xlsx)', primary: true, onClick: () => { location.href = `/api/export/${ex.dataset.export}?fmt=xlsx${App.scopeCompany() ? '&company_id=' + App.scopeCompany() : ''}`; } }, { label: 'CSV', onClick: () => { location.href = `/api/export/${ex.dataset.export}?fmt=csv`; } }] });
    const im = e.target.closest('[data-import]'); if (im) return App.importModal(im.dataset.import);
  });

  App.importModal = entity => App.modal({ title: 'استيراد بيانات', wide: true,
    body: `<div class="tabs"><button type="button" class="on" data-src="file">📁 ملف من الجهاز</button><button type="button" data-src="url">🔗 لينك Google Sheet</button></div>
      <div id="srcFile"><label class="field"><span>ملف Excel أو CSV</span><input type="file" id="impFile" accept=".xlsx,.csv"></label><label class="field"><span>اسم الورقة (اختياري — للملفات اللي فيها أكتر من ورقة)</span><input id="impSheet"></label></div>
      <div id="srcUrl" hidden><label class="field"><span>لينك الشيت</span><input id="impUrl" dir="ltr" placeholder="https://docs.google.com/spreadsheets/d/..."></label><p class="muted small">لازم الشيت يكون متشارك «Anyone with the link — Viewer». لو خاص، اربطه من «الربط والأتمتة».</p></div>
      <p class="small"><a href="/api/import/${esc(entity)}/template">⬇ نزّل قالب فاضي بالأعمدة المطلوبة</a> · أول خطوة «معاينة» ومش بتحفظ حاجة.</p><div id="impOut"></div>`,
    onOpen: m => { m.querySelectorAll('[data-src]').forEach(t => { t.onclick = () => { m.querySelectorAll('[data-src]').forEach(x => x.classList.toggle('on', x === t)); m.querySelector('#srcFile').hidden = t.dataset.src !== 'file'; m.querySelector('#srcUrl').hidden = t.dataset.src !== 'url'; }; }); },
    actions: [{ label: 'معاينة', keepOpen: true, onClick: m => runImport(m, entity, false) }, { label: 'حفظ الاستيراد', primary: true, keepOpen: true, onClick: m => runImport(m, entity, true) }] });
  async function runImport(m, entity, commit) {
    const fd = new FormData(), f = m.querySelector('#impFile').files[0], url = m.querySelector('#impUrl').value.trim(), useUrl = !m.querySelector('#srcUrl').hidden;
    if (useUrl) { if (!url) throw new Error('حط لينك الشيت'); fd.append('url', url); } else { if (!f) throw new Error('اختار ملف'); fd.append('file', f); fd.append('sheet', m.querySelector('#impSheet').value.trim()); }
    fd.append('commit', commit ? 'true' : 'false');
    const out = m.querySelector('#impOut'); App.loading(out);
    try {
      const r = await App.api('POST', `/api/import/${entity}`, fd);
      const ST = { new: ['جديد', 'good'], update: ['تحديث', 'info'], same: ['بدون تغيير', 'muted'], error: ['خطأ', 'bad'] };
      out.innerHTML = `<div class="notice ${commit ? '' : 'muted'}">${commit ? '✅ اتحفظ' : 'معاينة — لسه ما اتحفظش'}: ${r.total} صف · جديد ${r.counts.new} · تحديث ${r.counts.update} · بدون تغيير ${r.counts.same} · أخطاء ${r.counts.error}</div>` +
        App.table(r.rows, [{ label: 'صف', key: 'row' }, { label: 'الحالة', render: x => App.badge(ST[x.status][0], ST[x.status][1]) }, { label: 'المفتاح', key: 'key' }, { label: 'التفاصيل', key: 'message' }], { compact: true });
      if (commit) { App.cache.employees = null; App.render(); }
    } catch (e) { out.innerHTML = App.errorBox(e); }
  }

  // ------------------------------------------------------------ report builder modal (CROSS addendum)
  App.reportModal = async function (module, preset) {
    let defs;
    try { defs = (await App.get('/api/reports' + App.qs({ module }))); } catch (e) { return App.fail(e); }
    if (!defs.length) return App.toast('مفيش تقارير متاحة لصلاحيتك هنا', 'warn');
    const filterHtml = d => d.filters.map(f => {
      const t = f.type;
      if (t === 'date') return App.field({ name: f.key, label: f.label, type: 'date' });
      if (t === 'company') return App.field({ name: f.key, label: f.label, options: [['', 'كل الشركات']].concat(App.companies.map(c => [c.id, c.name])), value: App.scopeCompany() });
      if (t === 'department') return App.field({ name: f.key, label: f.label, options: [['', 'كل الأقسام']].concat([].concat(...App.companies.map(c => c.departments.map(x => [x.id, (App.companies.length > 1 ? c.name + ' · ' : '') + x.name]))))});
      if (t === 'asset_category') return App.field({ name: f.key, label: f.label, options: [['', 'الكل']].concat(((App.cache.assetMeta || {}).categories || []).map(c => [c.code, c.code + ' — ' + c.name_ar])) });
      if (t === 'select') return App.field({ name: f.key, label: f.label, options: [['', 'الكل']].concat(Object.entries(f.options)) });
      return App.field({ name: f.key, label: f.label });
    }).join('');
    const close = App.modal({ title: 'تجهيز تقرير', wide: true, body: `<label class="field"><span>نوع التقرير</span><select id="rKey">${defs.map(d => `<option value="${d.key}">${esc(d.title)}</option>`).join('')}</select></label>
      <p class="muted small" id="rAxis"></p><form class="grid3" id="rF" onsubmit="return false"></form><div class="actions"><button class="btn primary" id="rRun">معاينة</button>
      ${App.can('reports:print') ? '<button class="btn" id="rPrint">🖨 طباعة / PDF</button>' : ''}${App.can('reports:export') ? '<button class="btn" id="rCsv">CSV</button><button class="btn" id="rXlsx">XLSX</button>' : ''}</div><div id="rOut" style="margin-top:14px"></div>`,
    onOpen: el => {
      const sel = el.querySelector('#rKey');
      const draw = () => { const d = defs.find(x => x.key === sel.value); el.querySelector('#rF').innerHTML = filterHtml(d); el.querySelector('#rAxis').textContent = 'محور التاريخ: ' + d.date_axis; if (preset) Object.entries(preset).forEach(([k, v]) => { const i = el.querySelector(`#rF [name="${k}"]`); if (i) i.value = v; }); };
      if (preset && preset.key) sel.value = preset.key;
      sel.onchange = draw; draw();
      const filters = () => { const o = {}; $$('#rF [name]', el).forEach(i => { if (i.value) o[i.name] = i.value; }); return o; };
      el.querySelector('#rRun').onclick = async () => {
        const out = el.querySelector('#rOut'); App.loading(out);
        try {
          const r = await App.post(`/api/reports/${sel.value}/run`, filters());
          const ch = r.chart && Object.keys(r.chart.data || {}).length ? (r.chart.type === 'line' ? App.line(r.chart.data) : r.chart.type === 'doughnut' ? App.donut(Object.entries(r.chart.data).map(([k, v]) => ({ label: k, value: v, color: App.color(k) })), String(r.rows.length), 'سجل') : App.bars(r.chart.data)) : '';
          out.innerHTML = `<div class="notice muted">أُنشئ ${fmtDT(r.generated_at)} بواسطة ${esc(r.generated_by)} · ${r.rows.length} سجل</div>
            <div class="cards">${Object.entries(r.summary || {}).map(([k, v]) => `<div class="card"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('')}</div>${ch ? `<div class="panel">${ch}</div>` : ''}
            ${r.breakdown ? Object.entries(r.breakdown).map(([k, v]) => `<div class="panel"><h3>${esc(k)}</h3>${App.table(Object.entries(v).map(([n, cur]) => ({ n, v: Object.entries(cur).map(([c, a]) => a + ' ' + c).join(' · ') })), [{ label: 'البند', key: 'n' }, { label: 'المبلغ', key: 'v' }], { compact: true })}</div>`).join('') : ''}
            ${App.table(r.rows.slice(0, 300), r.columns.map(c => ({ label: c.label, key: c.key })), { compact: true, empty: 'لا توجد بيانات للفلاتر المختارة' })}${r.rows.length > 300 ? '<p class="muted small">المعاينة أول 300 سجل — التصدير فيه الكل</p>' : ''}`;
        } catch (e) { out.innerHTML = App.errorBox(e); }
      };
      const dl = fmt => { location.href = `/api/reports/${sel.value}/export?fmt=${fmt}&filters=${encodeURIComponent(JSON.stringify(filters()))}`; };
      const pr = el.querySelector('#rPrint'); if (pr) pr.onclick = () => window.open(`/api/reports/${sel.value}/print?filters=${encodeURIComponent(JSON.stringify(filters()))}`, '_blank', 'noopener');
      const c1 = el.querySelector('#rCsv'); if (c1) c1.onclick = () => dl('csv');
      const c2 = el.querySelector('#rXlsx'); if (c2) c2.onclick = () => dl('xlsx');
    } });
    return close;
  };

  // ------------------------------------------------------------ theme (Light / Dark / System) — saved per user on the server
  const THEMES = { system: ['◐', 'المظهر: حسب الجهاز'], light: ['☀', 'المظهر: فاتح'], dark: ['☾', 'المظهر: غامق'] };
  App.applyTheme = mode => {
    const r = document.documentElement; if (mode === 'light' || mode === 'dark') r.dataset.theme = mode; else { delete r.dataset.theme; mode = 'system'; }
    const b = $('#themeBtn'); if (b) { b.textContent = THEMES[mode][0]; b.title = THEMES[mode][1]; b.setAttribute('aria-label', THEMES[mode][1]); }
    store.set('theme', mode); return mode;
  };
  App.setTheme = async mode => { App.applyTheme(mode); if (App.me) { try { await App.put('/api/auth/me/theme', { theme: mode }); App.me.theme = mode; } catch (e) { App.fail(e); } } };

  // ------------------------------------------------------------ notifications bell
  async function refreshBell() {
    try { const n = await App.get('/api/notifications?limit=30'); const b = $('#bellCount'); b.textContent = n.unread || ''; b.hidden = !n.unread; App.cache.notifications = n; } catch (e) { /* offline: keep last */ }
  }
  function openBell() {
    const n = App.cache.notifications || { items: [] };
    App.modal({ title: 'الإشعارات', wide: false, body: n.items.length ? `<ul class="notif">${n.items.map(i => `<li class="${i.read ? '' : 'unread'}"><a href="#/${esc(i.link)}">${App.badge(i.severity === 'critical' ? 'حرج' : i.severity === 'high' ? 'عالي' : 'متوسط', i.severity === 'critical' ? 'bad' : i.severity === 'high' ? 'warn' : 'info')} ${esc(i.title)}</a><small class="muted">${fmtDT(i.created_at)}</small></li>`).join('')}</ul><p class="muted small">الإشعارات بيولّدها الـ Worker. لو الجهاز مقفول مش هتتحدث لحد ما يشتغل تاني.</p>` : App.emptyBox('مفيش إشعارات — أو الـ Worker لسه ما اشتغلش'),
      actions: n.unread ? [{ label: 'تعليم الكل كمقروء', onClick: async () => { await App.post('/api/notifications/read-all'); refreshBell(); } }] : [] });
  }

  // ------------------------------------------------------------ login
  App.showLogin = function () {
    if ($('.login-wrap2')) return;
    document.body.classList.add('locked');
    const w = document.createElement('div'); w.className = 'login-wrap2';
    w.innerHTML = `<form class="login-card2" novalidate><h1>IT Administration</h1><p class="muted">تسجيل الدخول</p>
      <label class="field"><span>البريد الإلكتروني</span><input type="email" name="email" autocomplete="username" dir="ltr" required></label>
      <label class="field"><span>كلمة المرور</span><input type="password" name="password" autocomplete="current-password" dir="ltr" required></label>
      <label class="field" id="otpBox" hidden><span>كود التحقق من تطبيق Authenticator</span><input name="otp" inputmode="numeric" autocomplete="one-time-code" dir="ltr" maxlength="6" placeholder="123456"></label>
      <div class="login-err" role="alert"></div><button class="btn primary block" type="submit">دخول</button>
      <p class="muted small">الحساب الأول بيتعمل من setup.bat. مفيش حسابات تجريبية.</p></form>`;
    document.body.appendChild(w); w.querySelector('[name=email]').focus();
    w.querySelector('form').onsubmit = async e => {
      e.preventDefault(); const f = e.target, err = f.querySelector('.login-err'); err.textContent = '';
      const otpBox = f.querySelector('#otpBox');
      try { await api('POST', '/api/auth/login', { email: f.email.value, password: f.password.value, otp: otpBox.hidden ? null : f.otp.value.trim() }, { noAuthRedirect: true }); w.remove(); document.body.classList.remove('locked'); await boot(); }
      catch (x) {
        if (x.message === 'mfa_required') { otpBox.hidden = false; err.textContent = 'الحساب عليه تحقق بخطوتين — اكتب الكود من تطبيق Authenticator'; f.otp.focus(); return; }
        err.textContent = x.message; if (otpBox.hidden) f.password.select(); else f.otp.select();
      }
    };
  };

  // ------------------------------------------------------------ boot
  async function boot() {
    try { App.me = await api('GET', '/api/auth/me', undefined, { noAuthRedirect: true }); }
    catch (e) { if (e.status === 401) return App.showLogin(); $('#main').innerHTML = App.errorBox(e); return; }
    App.applyTheme(App.me.theme || store.get('theme', 'system'));
    $('#userName').textContent = App.me.name + ' · ' + App.me.role.name_ar;
    await App.loadCompanies();
    const cf = $('#companyFilter');
    if (App.companies.length > 1) {
      cf.innerHTML = '<option value="">كل الشركات</option>' + App.companies.map(c => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
      cf.value = App.companies.some(c => c.id === store.get('company', '')) ? store.get('company', '') : '';
      cf.hidden = false; cf.onchange = () => { store.set('company', cf.value); render(); };
    } else { cf.hidden = true; store.set('company', ''); }
    if (App.can('assets:view')) { try { App.cache.assetMeta = await App.get('/api/assets/meta'); } catch (e) { /* optional */ } }
    await App.loadLookups();
    try { App.cache.links = await App.get('/api/integrations/links'); } catch (e) { App.cache.links = []; }
    buildNav();
    refreshBell(); clearInterval(App._bell); App._bell = setInterval(refreshBell, 60000);
    render();
  }

  document.addEventListener('DOMContentLoaded', () => {
    App.applyTheme(store.get('theme', 'system'));
    $('#themeBtn').onclick = () => { const order = ['system', 'light', 'dark']; App.setTheme(order[(order.indexOf(store.get('theme', 'system')) + 1) % 3]); };
    $('#menuBtn').onclick = () => document.body.classList.toggle('nav-open');
    $('#bellBtn').onclick = openBell;
    $('#logoutBtn').onclick = async () => { try { await App.post('/api/auth/logout'); } catch (e) { /* ignore */ } location.reload(); };
    window.addEventListener('hashchange', render);
    boot();
  });
})(window);
