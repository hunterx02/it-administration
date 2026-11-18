/* M04 — Technical services. No default periods; usage & due states come from the server rules. */
(function (App) {
  'use strict';
  const { esc, fmtDate, fmtDT, num } = App;
  let META = null;
  const meta = async () => META || (META = await App.get('/api/technical/meta'));
  const canEdit = () => App.can('technical:edit');
  const dueB = i => App.badge(App.DUE[i.due_state][0], App.DUE[i.due_state][1]) + (i.next_due ? ` <span class="small">${fmtDate(i.next_due)} · ${i.days_left} يوم</span>` : '');
  const useB = s => (App.USAGE[s] ? App.badge(App.USAGE[s][0], App.USAGE[s][1]) : App.badge('غير متابع', 'muted'));
  const units = () => [['', '—'], ['days', 'أيام'], ['months', 'شهور']];
  const cid = () => App.scopeCompany() || (App.companies[0] || {}).id;
  const clean = d => { Object.keys(d).forEach(k => { if (d[k] === null && /^(name|number|iccid|operator|possession|charge_owner|agent_details|backup_email|notes|provider|landline_number|plan|account_owner|service_type|usage_unit|period_unit|net_period_unit|land_period_unit)$/.test(k)) d[k] = ''; }); return d; };

  const renewModal = (targetType, targetId, reload, label) => App.formModal('تسجيل دفع وتجديد — ' + (label || ''), [
    { name: 'renewed_on', label: 'تاريخ الدفع/التجديد المؤكد', type: 'date', required: true },
    { name: 'period_value', label: 'المدة (لو اتغيرت)', type: 'number' }, { name: 'period_unit', label: 'الوحدة', options: units() },
    { name: 'receipt_ref', label: 'مرجع الإيصال', keepEmpty: true }, { name: 'notes', label: 'ملاحظات', keepEmpty: true }], {},
  async d => { d.target_type = targetType; d.target_id = targetId; d.receipt_ref = d.receipt_ref || ''; d.notes = d.notes || ''; if (!d.period_unit) delete d.period_unit; d.idempotency_key = targetType + '-' + targetId + '-' + d.renewed_on; await App.post('/api/technical/renewals', d); App.toast('اتسجل التجديد — الموعد الجاي محسوب من التاريخ الجديد'); reload(); },
  { intro: 'التجديد هنا هو مصدر الحقيقة لموعد الخدمة. التكلفة تتسجل في المالية كمصروف مربوط بحدث التجديد (مرة واحدة).' });
  const readingModal = (type, id, reload, unit) => App.formModal('إضافة قراءة يدوية', [{ name: 'remaining', label: `المتبقي (${unit})`, type: 'number' }, { name: 'used', label: 'المستهلك (اختياري)', type: 'number' }, { name: 'total', label: 'السعة الكلية (لو اتغيرت)', type: 'number' }, { name: 'read_at', label: 'وقت القراءة', type: 'datetime-local' }], {},
    async d => { d.target_type = type; d.target_id = id; d.source = 'manual'; const r = await App.post('/api/technical/readings', d); App.toast(r.reading.applied ? 'اتسجلت القراءة' : r.reading.note, r.reading.applied ? 'ok' : 'warn'); reload(); });

  // ---------------- renewals center
  App.pages.renewals = async (el, arg, q) => {
    const days = q.days || 30, d = await App.get('/api/technical/renewals/upcoming?days=' + days);
    const link = r => '#/' + r.link;
    el.innerHTML = App.head('مركز التجديدات', 'كل المواعيد في مكان واحد — محسوبة من آخر دفع مؤكد + مدة كل سجل', `<select id="daysSel" aria-label="الفترة">${[7, 14, 30, 60, 90].map(n => `<option ${+days === n ? 'selected' : ''} value="${n}">خلال ${n} يوم</option>`).join('')}</select>`, 'technical') +
      `<div class="panel">${App.table(d.items, [{ label: 'النوع', key: 'kind_ar' }, { label: 'الكود', render: r => `<span class="mono">${esc(r.code)}</span>` }, { label: 'الاسم', key: 'title' }, { label: 'الاستحقاق', render: dueB }], { href: link, empty: 'لا توجد تجديدات في الفترة دي' })}</div>
      ${d.incomplete.length ? `<div class="panel"><h2>سجلات بيانات تجديدها ناقصة (${d.incomplete.length})</h2><p class="muted small">مفيش آخر دفع مؤكد أو مدة — النظام مش بيفترض مدة.</p>${App.table(d.incomplete, [{ label: 'النوع', key: 'kind_ar' }, { label: 'الكود', render: r => `<span class="mono">${esc(r.code)}</span>` }, { label: 'الاسم', key: 'title' }], { href: link })}</div>` : ''}`;
    el.querySelector('#daysSel').onchange = e => App.go('#/renewals?days=' + e.target.value);
  };

  // ---------------- lines
  const lineFields = c => [{ name: 'company_id', label: 'الشركة', type: 'company', value: c, required: true }, { name: 'name', label: 'اسم الخط' }, { name: 'landline_number', label: 'رقم الأرضي', ltr: true }, { name: 'provider', label: 'المزود' }, { name: 'is_primary', label: 'خط أساسي (يظهر في الداشبورد)', type: 'checkbox' },
    { name: 'quota_gb', label: 'سعة الباقة GB', type: 'number' }, { name: 'warn_gb', label: 'عتبة تجهيز الرصيد GB', type: 'number', hint: 'أقل من القيمة = جهز رصيد' }, { name: 'critical_gb', label: 'عتبة التجديد العاجل GB', type: 'number', hint: 'أقل من أو يساوي = تجديد عاجل' },
    { name: 'net_last_payment', label: 'آخر دفع إنترنت', type: 'date' }, { name: 'net_period_value', label: 'مدة الإنترنت', type: 'number' }, { name: 'net_period_unit', label: 'وحدة مدة الإنترنت', options: units() }, { name: 'net_alert_days', label: 'تنبيه الإنترنت قبل (يوم)', type: 'number' },
    { name: 'land_last_payment', label: 'آخر دفع أرضي', type: 'date' }, { name: 'land_period_value', label: 'مدة الأرضي', type: 'number' }, { name: 'land_period_unit', label: 'وحدة مدة الأرضي', options: units() }, { name: 'land_alert_days', label: 'تنبيه الأرضي قبل (يوم)', type: 'number' }, { name: 'notes', label: 'ملاحظات', type: 'textarea', full: true }];

  // ---------------- WE packages review via the "TP IT — تحديث باقات WE" browser extension
  // Protocol (unchanged extension): bridge.js sets <html data-tp-we="version">; page -> {source:'tp-it', type:'TP_WE_REFRESH', lines}
  // extension -> {source:'tp-we-ext', type:'TP_WE_STATUS'|'TP_WE_RESULT'|'TP_WE_DONE', code, number, text, result}
  // Results are saved as readings (source=script). WE's renewal date is shown/noted only — it never renews the line (M04 §0.1).
  const WE = { run: null };
  const weInstalled = () => document.documentElement.dataset.tpWe || '';
  window.addEventListener('message', async e => {
    const m = e.data;
    if (e.source !== window || !m || m.source !== 'tp-we-ext' || !WE.run) return;
    const r = WE.run;
    const row = m.code ? (r.rows[m.code] || (r.rows[m.code] = { code: m.code, number: m.number || '', state: 'wait', text: '' })) : {};
    if (m.type === 'TP_WE_STATUS') { row.state = 'busy'; row.text = m.text || ''; }
    if (m.type === 'TP_WE_RESULT') {
      const res = m.result || {};
      if (!res.ok) { row.state = 'bad'; row.text = res.error || 'فشل'; }
      else {
        const ln = r.lines.find(l => l.code === m.code);
        if (!ln) { row.state = 'warn'; row.text = `الخط ${m.code} مش متسجل في النظام — متبقي ${res.remaining} من ${res.quota} GB (ما اتسجلش)`; row.result = res; }
        else {
          try {
            const note = 'my.te.eg عبر الإضافة' + (res.renewalDate ? ' · تاريخ تجديد WE ' + res.renewalDate : '');
            const out = await App.post('/api/technical/readings', { target_type: 'line', target_id: ln.id, remaining: res.remaining, used: res.used, total: res.quota, source: 'script', note });
            row.state = out.reading.applied ? 'ok' : 'warn';
            row.text = `متبقي ${res.remaining} من ${res.quota} GB · ${App.USAGE[out.target.usage_state][0]}${res.renewalDate ? ' · تجديد WE ' + res.renewalDate : ''}${out.reading.applied ? '' : ' — ' + out.reading.note}`;
            row.usage = out.target.usage_state;
            if (res.renewalDate && out.target.internet.next_due && out.target.internet.next_due !== res.renewalDate) row.text += ` ⚠ مختلف عن استحقاق النظام (${out.target.internet.next_due})`;
          } catch (err) { row.state = 'bad'; row.text = 'اتقرت من WE بس ما اتحفظتش: ' + err.message; }
        }
      }
    }
    if (m.type === 'TP_WE_DONE') { r.done = true; }
    drawWE();
    if (r.done && r.onDone) { const f = r.onDone; r.onDone = null; f(); }
  });
  function drawWE() {
    const box = document.getElementById('weRun'); if (!box || !WE.run) return;
    const ST = { wait: ['⏳', 'muted'], busy: ['🔄', 'info'], ok: ['✓', 'good'], warn: ['⚠', 'warn'], bad: ['✕', 'bad'] };
    const rows = Object.values(WE.run.rows);
    box.innerHTML = `<div class="panel"><div class="panel-head"><h2>مراجعة الباقات من WE ${WE.run.done ? App.badge('خلصت', 'good') : App.badge('شغالة…', 'info')}</h2><span class="muted small">بدأت الساعة ${new Date(WE.run.at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}</span></div>
      ${App.table(rows, [{ label: '', render: r => App.badge(ST[r.state][0], ST[r.state][1]) }, { label: 'الخط', render: r => `<span class="mono">${esc(r.code)}</span>` }, { label: 'الرقم', render: r => `<span dir="ltr">${esc(r.number)}</span>` }, { label: 'الحالة', render: r => esc(r.text || 'في الانتظار') }], { compact: true, empty: 'مستني رد الإضافة…' })}
      <p class="muted small">الإضافة بتفتح تاب my.te.eg لكل خط ورا بعض. لو WE طلبت كود تحقق اكتبه في التاب. تاريخ التجديد اللي WE بتقوله بيتسجل كملاحظة بس — موعد التجديد في النظام بيتغير بـ «تسجيل دفع وتجديد».</p></div>`;
  }
  async function startWE(rows, reload) {
    if (!weInstalled()) return App.modal({ title: 'إضافة باقات WE مش موجودة', body: `<div class="notice warn">الزرار ده بيشتغل مع إضافة المتصفح «TP IT — تحديث باقات WE».</div><ol class="small"><li>افتح النظام في <b>نفس بروفايل Chrome</b> اللي الإضافة متثبتة عليه (كل بروفايل ليه إضافاته).</li><li>افتح <span dir="ltr">chrome://extensions</span>، وفعّل <b>وضع المطور (Developer mode)</b>، لأن الإضافات غير المضغوطة بتتوقف من غيره. اتأكد إن الإضافة مفعّلة، ودوس ↻ إعادة تحميل.</li><li>ارجع للنظام واعمل تحديث للصفحة (Ctrl+F5).</li><li>لو مش متثبتة: «تحميل إضافة غير مضغوطة» ← فولدر الإضافة. وسجّل كل خط من نافذة الإضافة بنفس <b>كود الخط</b> اللي في النظام (مثلاً NET-001).</li></ol><p class="muted small">المتصفح الحالي: <span dir="ltr">${esc(navigator.userAgent.match(/(Edg|Chrome)\/[\d.]+/) ? navigator.userAgent.match(/(Edg|Chrome)\/[\d.]+/)[0] : navigator.userAgent)}</span> · الصفحة: <span dir="ltr">${esc(location.origin)}</span></p>` });
    if (WE.run && !WE.run.done && Date.now() - WE.run.at < 10 * 60 * 1000) return App.toast('في مراجعة شغالة بالفعل', 'warn');
    const lines = rows.map(l => ({ code: l.code, number: (l.landline_number || '').replace(/\D/g, '') }));
    WE.run = { at: Date.now(), lines: rows, rows: {}, done: false, onDone: reload };
    lines.filter(l => l.number).forEach(l => { WE.run.rows[l.code] = { code: l.code, number: l.number, state: 'wait', text: '' }; });
    drawWE();
    window.postMessage({ source: 'tp-it', type: 'TP_WE_REFRESH', code: lines[0] && lines[0].code, number: lines[0] && lines[0].number, lines }, location.origin);
  }

  App.pages.lines = async el => {
    const rows = await App.get('/api/technical/lines' + App.qs({ company_id: App.scopeCompany() }));
    const weBtn = canEdit() ? `<button class="btn" id="weB" title="${weInstalled() ? 'الإضافة متثبتة (v' + esc(weInstalled()) + ')' : 'الإضافة مش متثبتة'}">🔄 مراجعة الباقات من WE${weInstalled() ? '' : ' ⚠'}</button>` : '';
    const gauges = rows.length ? `<div class="gauge-grid">${rows.map(l => `<a class="gauge-card" href="#/line/${l.id}"><div class="top"><b>${esc(l.name || l.landline_number || l.code)}${l.is_primary ? ' ⭐' : ''}</b>${useB(l.usage_state)}</div>
        ${App.gauge({ remaining: l.remaining_gb, quota: l.quota_gb, warn: l.warn_gb, critical: l.critical_gb, state: l.usage_state, label: l.name || l.code })}
        <span class="small muted" style="text-align:center">آخر قراءة ${fmtDT(l.last_reading_at)}${l.last_reading_source ? ' · ' + esc(l.last_reading_source) : ''}</span>
        <span class="small">الباقة: ${dueB(l.internet)}</span></a>`).join('')}</div>
      <div class="gauge-legend"><span><i style="background:var(--c-used)"></i>المتبقي</span><span><i style="background:var(--c-repair)"></i>علامة عتبة تجهيز الرصيد</span><span><i style="background:var(--bad)"></i>علامة عتبة التجديد العاجل</span></div>` : '';
    el.innerHTML = App.head('الإنترنت والأرضي', 'استهلاك الباقة وتجديدها منفصلين عن اشتراك الأرضي — لكل خط عتباته ومدده', weBtn + (App.can('technical:create') ? '<button class="btn primary" id="newL">+ خط</button>' : ''), 'technical') + '<div id="weRun"></div>' + gauges +
      `<div class="panel">${App.table(rows, [{ label: 'الكود', render: l => `<b class="mono">${esc(l.code)}</b>${l.is_primary ? ' ⭐' : ''}` }, { label: 'الاسم', render: l => esc(l.name || l.landline_number) }, { label: 'المتبقي', render: l => l.remaining_gb == null ? '<span class="muted">لا توجد قراءة</span>' : `${num(l.remaining_gb)} / ${num(l.quota_gb)} GB` }, { label: 'الرصيد', render: l => useB(l.usage_state) }, { label: 'آخر قراءة', render: l => fmtDT(l.last_reading_at) + ' ' + esc(l.last_reading_source) }, { label: 'باقة الإنترنت', render: l => dueB(l.internet) }, { label: 'الأرضي', render: l => l.landline_number ? dueB(l.landline) : '—' }], { href: l => '#/line/' + l.id, empty: 'لا توجد خطوط' })}</div>`;
    drawWE();
    const wb = el.querySelector('#weB'); if (wb) wb.onclick = () => startWE(rows, () => App.render());
    const b = el.querySelector('#newL'); if (b) b.onclick = () => App.formModal('خط جديد', lineFields(cid()), {}, async d => { d.is_primary = !!d.is_primary; const l = await App.post('/api/technical/lines', clean(d)); App.go('#/line/' + l.id); }, { wide: true, cols: 4, intro: 'مثال توضيحي فقط: باقة 500GB بعتبات 100 و20 — حدد قيم كل خط بنفسك.' });
  };

  App.pages.line = async (el, id) => {
    const l = await App.get('/api/technical/lines/' + id), ed = canEdit();
    el.innerHTML = App.head(l.name || l.code, `${esc(l.provider)} · ${useB(l.usage_state)}`, ed ? '<button class="btn" id="rdB">+ قراءة يدوية</button><button class="btn primary" id="rnN">تجديد الباقة</button>' + (l.landline_number ? '<button class="btn" id="rnL">تجديد الأرضي</button>' : '') + '<button class="btn" id="edB">✏️ تعديل</button>' : '', 'technical') +
      `<div class="blocks"><div class="panel"><h2>الرصيد</h2>${App.gauge({ remaining: l.remaining_gb, quota: l.quota_gb, warn: l.warn_gb, critical: l.critical_gb, state: l.usage_state, label: l.name || l.code })}
        ${l.remaining_gb == null ? '<p class="muted center">لا توجد قراءة — مفيش تنبيه استهلاك.</p>' : ''}
        <dl class="kv"><dt>عتبة التجهيز</dt><dd>${num(l.warn_gb)}</dd><dt>عتبة العاجل</dt><dd>${num(l.critical_gb)}</dd><dt>آخر قراءة</dt><dd>${fmtDT(l.last_reading_at)} · ${esc(l.last_reading_source || '—')}</dd></dl></div>
      <div class="panel"><h2>المواعيد (مستقلة)</h2><p>باقة الإنترنت: ${dueB(l.internet)}</p><p>الأرضي ${esc(l.landline_number)}: ${l.landline_number ? dueB(l.landline) : '—'}</p></div></div>
      <div class="panel"><h2>المتبقي عبر الوقت</h2>${App.usageChart(l.readings.filter(r => r.applied).slice().reverse().map(r => ({ at: r.read_at, remaining: r.remaining })), { quota: l.quota_gb, warn: l.warn_gb, critical: l.critical_gb })}</div>
      <div class="panel"><h2>سجل القراءات</h2>${App.table(l.readings, [{ label: 'الوقت', render: r => fmtDT(r.read_at) }, { label: 'المتبقي', render: r => num(r.remaining) }, { label: 'المصدر', key: 'source' }, { label: 'مطبقة', render: r => r.applied ? '✓' : App.badge('لا', 'warn') + ' <span class="small">' + esc(r.note) + '</span>' }], { compact: true, empty: 'لا توجد قراءات' })}</div>
      <div class="panel"><h2>سجل التجديدات</h2>${App.table(l.renewals, [{ label: 'النوع', render: r => r.target_type === 'line_internet' ? 'إنترنت' : 'أرضي' }, { label: 'التاريخ', render: r => fmtDate(r.renewed_on) }, { label: 'المدة', render: r => (r.period_value || '—') + ' ' + esc(r.period_unit) }, { label: 'الاستحقاق التالي', render: r => fmtDate(r.next_due) }, { label: 'تكلفة', render: r => r.financial_event_id ? `<a href="#/event/${r.financial_event_id}">مرتبطة</a>` : '—' }], { compact: true, empty: 'لا يوجد' })}</div>`;
    const reload = () => App.pages.line(el, id);
    const on = (s, f) => { const b = el.querySelector(s); if (b) b.onclick = f; };
    on('#rdB', () => readingModal('line', id, reload, 'GB'));
    on('#rnN', () => renewModal('line_internet', id, reload, 'باقة الإنترنت'));
    on('#rnL', () => renewModal('line_landline', id, reload, 'الأرضي'));
    on('#edB', () => App.formModal('تعديل الخط', lineFields(l.company_id).filter(f => !['company_id', 'net_last_payment', 'land_last_payment'].includes(f.name)), l, async d => { d.version = l.version; d.is_primary = !!d.is_primary; await App.patch('/api/technical/lines/' + id, clean(d)); reload(); }, { wide: true, cols: 4, intro: 'آخر دفع بيتغير بتسجيل تجديد فقط (عشان يتحفظ السجل).' }));
  };

  // ---------------- SIMs (EG / SA separate registers)
  App.pages.sims = async (el, arg, q, page) => {
    const country = page.split('-')[1] || 'EG', m = await meta(), SA = country === 'SA';
    const rows = await App.get('/api/technical/sims' + App.qs({ country, company_id: App.scopeCompany() }));
    el.innerHTML = App.head(SA ? 'الشرائح السعودية' : 'الشرائح المصرية', SA ? '«مع الوكيل» = مكان الحيازة في السعودية — مش تلف ولا صيانة' : 'سجل الشرائح المصرية وتسليمها', App.can('technical:create') ? '<button class="btn primary" id="newS">+ شريحة</button>' : '', 'technical') +
      `<div class="panel">${App.table(rows, [{ label: 'الكود', render: s => `<b class="mono">${esc(s.code)}</b>` }, { label: 'الرقم', render: s => `<span dir="ltr">${esc(s.number)}</span>` }, { label: 'المشغل', key: 'operator' }, { label: 'مع', render: s => esc(s.holder_name || '—') }, { label: 'الاستخدام', render: s => App.badge(m.allocation[s.allocation_status], s.allocation_status === 'assigned' ? 'good' : 'muted') }, { label: 'الشبكة', render: s => esc(m.network[s.network_status]) },
        ...(SA ? [{ label: 'الحيازة', key: 'possession' }, { label: 'التجديد', render: dueB }, { label: 'Backup', render: s => s.backup_state === 'overdue' ? App.badge('متأخر', 'bad') : s.last_backup_at ? fmtDate(s.last_backup_at) : App.badge('لا يوجد', 'muted') }] : [{ label: 'آخر تسليم', render: s => fmtDate(s.last_handover) }])], { href: s => '#/sim/' + s.id, empty: 'لا توجد شرائح' })}</div>`;
    const b = el.querySelector('#newS'); if (b) b.onclick = () => { const c = cid(); App.formModal('شريحة جديدة', [{ name: 'company_id', label: 'الشركة', type: 'company', value: c, required: true }, { name: 'code', label: 'الكود (فاضي = تلقائي)' }, { name: 'name', label: 'اسم الشريحة' }, { name: 'number', label: 'الرقم', ltr: true }, { name: 'iccid', label: 'السيريال ICCID', ltr: true }, { name: 'operator', label: 'المشغل', options: [['', '—']].concat((m.lists[SA ? 'sim_operator_sa' : 'sim_operator_eg'] || []).map(x => [x, x])) }, { name: 'department_id', label: 'القسم', type: 'department', company: c }, { name: 'network_status', label: 'الشبكة', options: Object.entries(m.network) }, { name: 'calls_enabled', label: 'مكالمات', type: 'checkbox' },
      ...(SA ? [{ name: 'possession', label: 'مكان الحيازة', options: [['', '—']].concat(m.lists.sim_possession.map(x => [x, x])) }, { name: 'charge_owner', label: 'مسؤول الشحن/المتابعة' }, { name: 'backup_email', label: 'بريد Backup', ltr: true }, { name: 'backup_interval_days', label: 'تذكير Backup كل (يوم)', type: 'number' }, { name: 'last_payment', label: 'آخر دفع مؤكد', type: 'date' }, { name: 'period_value', label: 'مدة التجديد', type: 'number' }, { name: 'period_unit', label: 'الوحدة', options: units() }, { name: 'alert_days', label: 'التنبيه قبل (يوم)', type: 'number' }, { name: 'agent_details', label: 'تفاصيل الوكيل', type: 'textarea', full: true }] : []), { name: 'notes', label: 'ملاحظات', type: 'textarea', full: true }],
      { network_status: 'unknown' }, async d => { d.country = country; d.calls_enabled = !!d.calls_enabled; if (!d.code) delete d.code; const s = await App.post('/api/technical/sims', clean(d)); App.go('#/sim/' + s.id); }, { wide: true, cols: 3 }); };
  };
  App.pages['sims-EG'] = (el, a, q) => App.pages.sims(el, a, q, 'sims-EG');
  App.pages['sims-SA'] = (el, a, q) => App.pages.sims(el, a, q, 'sims-SA');

  App.pages.sim = async (el, id) => {
    const s = await App.get('/api/technical/sims/' + id), m = await meta(), ed = canEdit(), SA = s.country === 'SA';
    el.innerHTML = App.head(`${s.code} — ${s.number}`, `${SA ? 'شريحة سعودية' : 'شريحة مصرية'} · ${App.badge(m.allocation[s.allocation_status], 'info')} · الشبكة: ${esc(m.network[s.network_status])}${SA && s.possession ? ' · ' + esc(s.possession) : ''}`,
      ed ? [s.allocation_status === 'available' ? '<button class="btn primary" data-do="handover">📤 تسليم</button>' : '', s.allocation_status === 'assigned' ? '<button class="btn primary" data-do="return">📥 استرجاع</button>' : '', SA ? '<button class="btn" data-do="poss">نقل الحيازة</button><button class="btn" data-do="backup">✓ تأكيد Backup</button>' : '', '<button class="btn" data-do="renew">تسجيل دفع وتجديد</button><button class="btn" data-do="edit">✏️ تعديل</button>'].join('') : '', 'technical') +
      `<div class="blocks"><div class="panel"><h2>البيانات</h2><dl class="kv">${[['المشغل', s.operator], ['ICCID', s.iccid], ['مع', s.holder_name], ['القسم', App.deptName(s.department_id)], ['مكالمات', s.calls_enabled ? 'نعم' : 'لا'], ['مسؤول الشحن', s.charge_owner], ['تفاصيل الوكيل', s.agent_details], ['بريد Backup', s.backup_email], ['ملاحظات', s.notes]].map(([k, v]) => `<dt>${k}</dt><dd>${esc(v || '—')}</dd>`).join('')}</dl></div>
      <div class="panel"><h2>التجديد والنسخ</h2><p>التجديد: ${dueB(s)}</p>${SA ? `<p>آخر Backup مؤكد: <b>${fmtDate(s.last_backup_at)}</b> ${s.backup_state === 'overdue' ? App.badge('متأخر', 'bad') : ''}</p><p class="muted small">التاريخ بيتحدث بتأكيد شخص بعد عمل النسخة فعلًا — فتح الصفحة مش بيحدّثه.</p>` : ''}</div></div>
      <div class="panel"><h2>الحركات</h2>${App.table(s.events, [{ label: 'الوقت', render: e => fmtDT(e.at) }, { label: 'الحركة', key: 'kind' }, { label: 'النتيجة', key: 'result' }, { label: 'تفاصيل', key: 'detail' }], { compact: true })}</div>
      <div class="blocks"><div class="panel"><h2>التجديدات</h2>${App.table(s.renewals, [{ label: 'التاريخ', render: r => fmtDate(r.renewed_on) }, { label: 'الاستحقاق التالي', render: r => fmtDate(r.next_due) }, { label: 'إيصال', key: 'receipt_ref' }], { compact: true, empty: 'لا يوجد' })}</div>
      ${SA ? `<div class="panel"><h2>تأكيدات Backup</h2>${App.table(s.backups, [{ label: 'التاريخ', render: b => fmtDate(b.performed_on) }, { label: 'طريقة التحقق', key: 'method' }, { label: 'ملاحظات', key: 'notes' }], { compact: true, empty: 'لا يوجد' })}</div>` : ''}</div>`;
    const reload = () => App.pages.sim(el, id);
    el.onclick = async ev => {
      const b = ev.target.closest('[data-do]'); if (!b) return;
      try {
        const k = b.dataset.do;
        if (k === 'handover') return App.formModal('تسليم الشريحة', [{ name: 'employee_id', label: 'الموظف', options: await App.empOptions(x => x.company_id === s.company_id), required: true }, { name: 'notes', label: 'ملاحظات', keepEmpty: true }], {}, async d => { d.notes = d.notes || ''; await App.post(`/api/technical/sims/${id}/handover`, d); reload(); });
        if (k === 'return') return App.formModal('استرجاع الشريحة', [{ name: 'result', label: 'النتيجة', options: [['returned', 'استُرجعت'], ['damaged', 'تالفة'], ['missing', 'مفقودة']], required: true }, { name: 'notes', label: 'ملاحظات', keepEmpty: true }], {}, async d => { d.notes = d.notes || ''; await App.post(`/api/technical/sims/${id}/return`, d); reload(); });
        if (k === 'poss') return App.formModal('نقل الحيازة (حركة مسجلة)', [{ name: 'possession', label: 'مكان الحيازة', options: m.lists.sim_possession.map(x => [x, x]), required: true }, { name: 'charge_owner', label: 'مسؤول الشحن', value: s.charge_owner }, { name: 'agent_details', label: 'تفاصيل الوكيل', value: s.agent_details }, { name: 'notes', label: 'ملاحظات', keepEmpty: true }], {}, async d => { d.notes = d.notes || ''; await App.post(`/api/technical/sims/${id}/possession`, d); reload(); });
        if (k === 'backup') return App.formModal('تأكيد عمل Backup واتساب', [{ name: 'performed_on', label: 'تاريخ عمل النسخة فعلًا', type: 'date', required: true }, { name: 'method', label: 'طريقة التحقق', required: true }, { name: 'notes', label: 'ملاحظات', keepEmpty: true }], {}, async d => { d.notes = d.notes || ''; await App.post(`/api/technical/sims/${id}/backup-confirm`, d); reload(); });
        if (k === 'renew') return renewModal('sim', id, reload, s.code);
        if (k === 'edit') return App.formModal('تعديل الشريحة', [{ name: 'name', label: 'الاسم' }, { name: 'number', label: 'الرقم', ltr: true }, { name: 'iccid', label: 'ICCID', ltr: true }, { name: 'operator', label: 'المشغل' }, { name: 'department_id', label: 'القسم', type: 'department', company: s.company_id }, { name: 'network_status', label: 'الشبكة', options: Object.entries(m.network) }, { name: 'calls_enabled', label: 'مكالمات', type: 'checkbox' }, { name: 'backup_email', label: 'بريد Backup', ltr: true }, { name: 'backup_interval_days', label: 'تذكير Backup كل (يوم)', type: 'number' }, { name: 'period_value', label: 'مدة التجديد', type: 'number' }, { name: 'period_unit', label: 'الوحدة', options: units() }, { name: 'alert_days', label: 'التنبيه قبل (يوم)', type: 'number' }, { name: 'notes', label: 'ملاحظات', type: 'textarea', full: true }],
          s, async d => { d.version = s.version; d.calls_enabled = !!d.calls_enabled; await App.patch('/api/technical/sims/' + id, clean(d)); reload(); }, { wide: true, cols: 3 });
      } catch (err) { App.fail(err); }
    };
  };

  // ---------------- subscriptions
  const subFields = (c, m) => [{ name: 'company_id', label: 'الشركة', type: 'company', value: c, required: true }, { name: 'name', label: 'الاسم', required: true }, { name: 'service_type', label: 'نوع الخدمة', options: [['', '—']].concat(m.lists.service_type.map(x => [x, x])) }, { name: 'provider', label: 'المزوّد' }, { name: 'plan', label: 'الخطة' }, { name: 'account_owner', label: 'مسؤول الحساب / البريد', ltr: true },
    { name: 'last_payment', label: 'آخر دفع مؤكد', type: 'date' }, { name: 'period_value', label: 'المدة', type: 'number' }, { name: 'period_unit', label: 'الوحدة', options: units() }, { name: 'alert_days', label: 'التنبيه قبل (يوم)', type: 'number' },
    { name: 'usage_unit', label: 'وحدة الرصيد (لو متابع)', options: [['', 'غير متابع']].concat(m.lists.usage_unit.map(x => [x, x])) }, { name: 'quota_total', label: 'الإجمالي', type: 'number' }, { name: 'warn_level', label: 'عتبة جهّز رصيد', type: 'number' }, { name: 'critical_level', label: 'عتبة الاستنفاد العاجل', type: 'number' }, { name: 'notes', label: 'ملاحظات', type: 'textarea', full: true }];
  App.pages.subscriptions = async el => {
    const m = await meta(), rows = await App.get('/api/technical/subscriptions' + App.qs({ company_id: App.scopeCompany() }));
    el.innerHTML = App.head('الاشتراكات والتراخيص', 'AI والأدوات والإيميلات المدفوعة والدومينات وSSL — الإدخال اليدوي متاح دايمًا', App.can('technical:create') ? '<button class="btn primary" id="newSub">+ اشتراك</button>' : '', 'technical') +
      `<div class="panel">${App.table(rows, [{ label: 'الكود', render: s => `<span class="mono">${esc(s.code)}</span>` }, { label: 'الاسم', render: s => `<b>${esc(s.name)}</b><div class="small muted">${esc(s.provider)} ${esc(s.plan)}</div>` }, { label: 'النوع', key: 'service_type' }, { label: 'التجديد', render: dueB }, { label: 'الرصيد', render: s => s.usage_unit ? `${num(s.remaining)} ${esc(s.usage_unit)} ${useB(s.usage_state)}` : '<span class="muted">غير متابع</span>' }, { label: 'الحالة', key: 'status' }], { href: s => '#/subscription/' + s.id, empty: 'لا توجد اشتراكات' })}</div>`;
    const b = el.querySelector('#newSub'); if (b) b.onclick = () => App.formModal('اشتراك جديد', subFields(cid(), m), {}, async d => { const s = await App.post('/api/technical/subscriptions', clean(d)); App.go('#/subscription/' + s.id); }, { wide: true, cols: 3, intro: 'مفيش افتراض إن الخدمة ليها API استهلاك. الرصيد يتدخل يدوي لحد ما موصل موثّق يتعمل من «الربط والأتمتة».' });
  };
  App.pages.subscription = async (el, id) => {
    const s = await App.get('/api/technical/subscriptions/' + id), m = await meta(), ed = canEdit();
    el.innerHTML = App.head(s.name, `${esc(s.provider)} · ${esc(s.service_type)} · ${dueB(s)}`, ed ? (s.usage_unit ? '<button class="btn" id="rd">+ قراءة رصيد</button>' : '') + '<button class="btn primary" id="rn">تسجيل دفع وتجديد</button><button class="btn" id="ed">✏️ تعديل</button>' : '', 'technical') +
      `<div class="blocks"><div class="panel"><h2>الرصيد</h2>${s.usage_unit ? `${App.gauge({ remaining: s.remaining, quota: s.quota_total, warn: s.warn_level, critical: s.critical_level, state: s.usage_state, unit: s.usage_unit, label: s.name })}<p class="center">${useB(s.usage_state)}</p><p class="small muted center">آخر قراءة ${fmtDT(s.last_reading_at)} · ${esc(s.last_reading_source || '—')}</p>${App.usageChart(s.readings.filter(r => r.applied).slice().reverse().map(r => ({ at: r.read_at, remaining: r.remaining })), { quota: s.quota_total, warn: s.warn_level, critical: s.critical_level, unit: s.usage_unit })}` : '<p class="muted">الرصيد غير متابع لهذه الخدمة</p>'}</div>
      <div class="panel"><h2>البيانات</h2><dl class="kv"><dt>الحساب</dt><dd dir="ltr">${esc(s.account_owner || '—')}</dd><dt>آخر دفع</dt><dd>${fmtDate(s.last_payment)}</dd><dt>المدة</dt><dd>${s.period_value || '—'} ${esc(s.period_unit)}</dd><dt>ملاحظات</dt><dd>${esc(s.notes || '—')}</dd></dl></div></div>
      <div class="panel"><h2>التجديدات</h2>${App.table(s.renewals, [{ label: 'التاريخ', render: r => fmtDate(r.renewed_on) }, { label: 'الاستحقاق التالي', render: r => fmtDate(r.next_due) }, { label: 'تكلفة', render: r => r.financial_event_id ? `<a href="#/event/${r.financial_event_id}">مرتبطة</a>` : '—' }], { compact: true, empty: 'لا يوجد' })}</div>`;
    const reload = () => App.pages.subscription(el, id), on = (q, f) => { const b = el.querySelector(q); if (b) b.onclick = f; };
    on('#rd', () => readingModal('subscription', id, reload, s.usage_unit));
    on('#rn', () => renewModal('subscription', id, reload, s.name));
    on('#ed', () => App.formModal('تعديل الاشتراك', subFields(s.company_id, m).filter(f => !['company_id', 'last_payment'].includes(f.name)).concat([{ name: 'status', label: 'الحالة', options: [['active', 'نشط'], ['paused', 'موقوف'], ['cancelled', 'ملغي']] }]), s, async d => { d.version = s.version; await App.patch('/api/technical/subscriptions/' + id, clean(d)); reload(); }, { wide: true, cols: 3 }));
  };

  // ---------------- security & backup
  App.pages.security = async el => {
    const sims = await App.get('/api/technical/sims?country=SA');
    el.innerHTML = App.head('الأمان والنسخ الاحتياطي', 'نسخ WhatsApp المرتبط بالشرائح السعودية — والتذكير حسب مدة كل شريحة', '', 'technical') +
      `<div class="cards"><div class="card"><span>متأخر</span><b class="t-bad">${sims.filter(s => s.backup_state === 'overdue').length}</b></div><div class="card"><span>منتظم</span><b>${sims.filter(s => s.backup_state === 'ok').length}</b></div><div class="card"><span>بدون بيانات</span><b>${sims.filter(s => s.backup_state === 'incomplete').length}</b></div></div>
      <div class="panel">${App.table(sims, [{ label: 'الشريحة', render: s => `<span class="mono">${esc(s.code)}</span>` }, { label: 'الرقم', render: s => `<span dir="ltr">${esc(s.number)}</span>` }, { label: 'بريد Backup', render: s => `<span dir="ltr">${esc(s.backup_email || '—')}</span>` }, { label: 'آخر Backup مؤكد', render: s => fmtDate(s.last_backup_at) }, { label: 'الاستحقاق', render: s => s.backup_due ? fmtDate(s.backup_due) + ' ' + (s.backup_state === 'overdue' ? App.badge('متأخر', 'bad') : '') : App.badge('حدد المدة', 'muted') }], { href: s => '#/sim/' + s.id, empty: 'لا توجد شرائح سعودية' })}</div>`;
  };
})(window.App);
