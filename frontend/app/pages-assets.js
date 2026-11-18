/* M02 — Assets & custody pages. Custody changes only through action buttons (events), never by editing a cell. */
(function (App) {
  'use strict';
  const { esc, fmtDate, fmtDT } = App;
  const GROUPS = { desktops: ['CMP', 'CMPM', 'CMPH', 'AIO', 'MAC'], laptops: ['LTP'], mobiles: ['MOB'], small: ['PRN', 'Hard', 'mon'] };
  const ACC_AR = { keyboard: 'كيبورد', mouse: 'ماوس', headset: 'سماعة', cables: 'كابلات', pad: 'بادة', charger: 'شاحن' };
  const meta = () => App.cache.assetMeta || { categories: [], allocation: {}, condition: {}, accessories: [] };
  const allocBadge = a => App.badge(a.allocation_ar, { assigned: 'good', available: 'info', in_stock: 'warn', reserved: 'info', in_maintenance: 'warn', lost: 'bad', retired: 'muted', pending_wipe: 'muted' }[a.allocation_status]);
  const condBadge = a => App.badge(a.condition_ar, { good: 'good', needs_repair: 'warn', damaged: 'bad', unknown: 'muted' }[a.tech_condition]);
  const accChips = a => `<span class="chk">${Object.keys(ACC_AR).map(k => `<span class="${(a.accessories || {})[k] ? 'on' : ''}" title="${ACC_AR[k]}">${ACC_AR[k][0]}</span>`).join('')}</span>`;

  const assetFields = (companyId) => [
    { name: 'company_id', label: 'الشركة', type: 'company', required: true, value: companyId },
    { name: 'category_code', label: 'الفئة', options: meta().categories.map(c => [c.code, c.code + ' — ' + c.name_ar]), required: true },
    { name: 'brand', label: 'الماركة', lookup: 'brand' }, { name: 'model', label: 'الموديل' }, { name: 'serial', label: 'السيريال / IMEI', ltr: true },
    { name: 'hostname', label: 'اسم الجهاز', ltr: true }, { name: 'cpu', label: 'المعالج' }, { name: 'ram_gb', label: 'RAM (GB)' },
    { name: 'storage', label: 'التخزين' }, { name: 'gpu', label: 'كارت الشاشة' }, { name: 'os', label: 'نظام التشغيل' },
    { name: 'screen_info', label: 'الشاشة', lookup: 'screen_type' }, { name: 'department_id', label: 'القسم', type: 'department', company: companyId },
    { name: 'location', label: 'الموقع', lookup: 'location' },
    { name: 'allocation_status', label: 'الحالة عند الإضافة', options: [['in_stock', 'في المخزن'], ['available', 'خالي / متاح']] },
    { name: 'tech_condition', label: 'الحالة الفنية', options: Object.entries(meta().condition) },
    { name: 'purchase_date', label: 'تاريخ الشراء', type: 'date' }, { name: 'warranty_end', label: 'نهاية الضمان', type: 'date' },
    ...(App.can('assets:view_secrets') ? [{ name: 'device_password', label: 'كلمة مرور الجهاز (PSWD)', type: 'password', hint: 'بتتحفظ مشفّرة خارج قاعدة البيانات — ومش بتظهر في أي قائمة' }] : []),
    { name: 'acc', label: 'الملحقات', type: 'checkgroup', options: Object.entries(ACC_AR) },
    { name: 'notes', label: 'ملاحظات', type: 'textarea', full: true },
  ];
  const packAcc = d => { const on = new Set(d.acc || []); delete d.acc; d.accessories = {}; Object.keys(ACC_AR).forEach(k => { d.accessories[k] = on.has(k); }); Object.keys(d).forEach(k => { if (d[k] === null && !['department_id', 'purchase_date', 'warranty_end'].includes(k)) d[k] = ''; }); return d; };

  /* Change category: the device type was registered wrong (e.g. CMP -> CMPM). New code, old one kept in history. */
  App.changeCategory = (a, done) => {
    const cats = meta().categories.filter(c => c.code !== a.category_code);
    App.formModal('تغيير فئة ' + a.code, [{ name: 'category_code', label: 'الفئة الجديدة', options: cats.map(c => [c.code, c.code + ' — ' + c.name_ar]), required: true }, { name: 'reason', label: 'السبب', required: true }], {},
      async d => { const r = await App.post(`/api/assets/${a.id}/category`, d); App.toast(`الكود الجديد ${r.code} — اطبع ملصق الجهاز من جديد`); done && done(); },
      { intro: `الفئة الحالية <b>${esc(a.category_code)}</b>. استخدم ده لو نوع الجهاز متسجل غلط. <b>لو الموظف بيغيّر جهازه استخدم «🔄 استبدال الجهاز»</b> بدل كده.` });
  };
  /* Employee changes device: return the current one + hand over the replacement in one atomic step. */
  App.swapDevice = async (a, done) => {
    const pool = (await App.get('/api/assets' + App.qs({ company_id: a.company_id }))).filter(x => x.id !== a.id && ['in_stock', 'available'].includes(x.allocation_status) && x.tech_condition !== 'damaged');
    if (!pool.length) return App.toast('مفيش أجهزة متاحة في المخزن للاستبدال', 'warn');
    App.formModal(`استبدال جهاز ${a.holder_name || ''}`, [{ name: 'new_asset_id', label: 'الجهاز الجديد', options: pool.map(x => [x.id, `${x.code} — ${(x.brand + ' ' + x.model).trim()}`]), required: true },
      { name: 'old_result', label: `حالة الجهاز القديم ${a.code}`, options: [['returned', 'استُرجع سليم'], ['damaged', 'استُرجع تالف'], ['missing', 'مفقود']], required: true }, { name: 'notes', label: 'ملاحظات', keepEmpty: true }], {},
      async d => { d.notes = d.notes || ''; const r = await App.post(`/api/assets/${a.id}/swap`, d); App.toast(`اتسلّم ${r.new.code} ورجع ${r.old.code}`); done && done(); },
      { intro: 'الاسترجاع والتسليم بيتعملوا مع بعض. لو الموظف عليه إخلاء طرف هيترفض والجهاز القديم هيفضل معاه.' });
  };

  App.newAsset = () => {
    const cid = App.scopeCompany() || (App.companies[0] || {}).id;
    if (!cid) return App.toast('أضف شركة الأول من إدارة النظام', 'warn');
    const fields = assetFields(cid);
    App.formModal('إضافة أصل', fields, { allocation_status: 'in_stock', tech_condition: 'unknown' }, async d => {
      const pw = d.device_password; delete d.device_password;
      const a = await App.post('/api/assets', packAcc(d));
      if (pw) await App.put(`/api/assets/${a.id}/password`, { password: pw });
      App.toast('اتسجل بكود ' + a.code); App.go('#/asset/' + a.id);
    }, { wide: true, cols: 3, intro: 'الكود بيتولد تلقائيًا: بادئة الشركة - الفئة - رقم تسلسلي، ومش بيتغير بعد كده.' });
  };

  App.pages.assets = async (el, arg, q) => {
    const m = meta(), f = Object.assign({ company_id: App.scopeCompany() }, q);
    let rows = await App.get('/api/assets' + App.qs({ company_id: f.company_id, category: f.category, allocation: f.allocation, condition: f.condition, department_id: f.department_id, q: f.q }));
    if (f.group && GROUPS[f.group]) rows = rows.filter(a => GROUPS[f.group].includes(a.category_code));
    const sel = (name, opts, label) => `<select data-f="${name}" aria-label="${label}"><option value="">${label}</option>${opts.map(([k, l]) => `<option value="${esc(k)}" ${f[name] === k ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
    el.innerHTML = App.head('الأجهزة والموبايلات', 'سجل الأصول — التخصيص منفصل عن الحالة الفنية', App.can('assets:create') ? '<button class="btn primary" id="newA">+ أصل جديد</button>' : '', 'assets', { io: 'assets' }) +
      `<div class="toolbar">${sel('category', m.categories.map(c => [c.code, c.code + ' — ' + c.name_ar]), 'كل الفئات')}${sel('allocation', Object.entries(m.allocation), 'كل حالات التخصيص')}${sel('condition', Object.entries(m.condition), 'كل الحالات الفنية')}
      ${sel('department_id', [].concat(...App.companies.map(c => c.departments.map(d => [d.id, d.name]))), 'كل الأقسام')}<input type="search" data-f="q" placeholder="بحث بالكود / السيريال / الموديل" value="${esc(f.q || '')}">
      ${f.group ? `<span class="badge b-info">${esc(f.group)} <a href="#/assets">✕</a></span>` : ''}</div>
      <div class="cards"><div class="card"><span>العدد</span><b>${rows.length}</b></div><div class="card"><span>مسلّم</span><b>${rows.filter(a => a.allocation_status === 'assigned').length}</b></div><div class="card"><span>في المخزن / متاح</span><b>${rows.filter(a => ['in_stock', 'available'].includes(a.allocation_status)).length}</b></div><div class="card"><span>تالف</span><b class="t-bad">${rows.filter(a => a.tech_condition === 'damaged').length}</b></div></div>
      <div class="panel">${App.table(rows, [
        { label: 'الكود', render: a => `<b class="mono" dir="ltr">${esc(a.code)}</b>${a.has_password ? ' <span title="كلمة المرور محفوظة مشفّرة">🔑</span>' : ''}` },
        { label: 'الفئة', render: a => { const c = m.categories.find(x => x.code === a.category_code); const badge = `<span class="badge b-info mono">${esc(a.category_code)}${App.can('assets:edit') ? ' ✏️' : ''}</span>`; return (App.can('assets:edit') ? `<button class="link" style="padding:0" data-recat="${a.id}" title="تغيير الفئة">${badge}</button>` : badge) + `<div class="small muted">${esc(c ? c.name_ar : '')}</div>`; } },
        { label: 'الجهاز', render: a => `${esc((a.brand + ' ' + a.model).trim() || '—')}<div class="muted small mono" dir="ltr">${esc(a.serial)}</div>` },
        { label: 'اسم الموظف', render: a => a.holder_name ? `<b>${esc(a.holder_name)}</b>${App.can('assets:edit') ? ` <button class="link" data-swap="${a.id}" title="الموظف بيغيّر جهازه">🔄 استبدال</button>` : ''}` : a.reserved_for_name ? '📌 محجوز لـ ' + esc(a.reserved_for_name) : '<span class="muted">— في العهدة العامة</span>' },
        { label: 'الشركة', render: a => esc(App.companyName(a.company_id)) },
        { label: 'القسم', render: a => esc(App.deptName(a.department_id)) }, { label: 'التخصيص', render: allocBadge }, { label: 'الحالة الفنية', render: condBadge },
        { label: 'ملحقات', render: accChips }], { href: a => '#/asset/' + a.id, empty: 'لا توجد أصول بالفلاتر دي', emptyAction: App.can('assets:create') ? '<button class="btn primary" onclick="App.newAsset()">+ أضف أول أصل</button>' : '' })}</div>`;
    const nb = el.querySelector('#newA'); if (nb) nb.onclick = App.newAsset;
    el.onclick = e => {
      const rc = e.target.closest('[data-recat]'), sw = e.target.closest('[data-swap]');
      if (rc) { e.stopPropagation(); App.changeCategory(rows.find(x => x.id === rc.dataset.recat), () => App.render()); }
      if (sw) { e.stopPropagation(); App.swapDevice(rows.find(x => x.id === sw.dataset.swap), () => App.render()).catch(App.fail); }
    };
    el.querySelectorAll('[data-f]').forEach(i => { i.onchange = () => { const nq = Object.assign({}, f, { [i.dataset.f]: i.value }); delete nq.company_id; App.go('#/assets' + App.qs(nq)); }; });
  };

  App.pages.asset = async (el, id) => {
    const a = await App.get('/api/assets/' + id), can = App.can('assets:edit');
    const btn = (k, l, cls) => can ? `<button class="btn ${cls || ''}" data-do="${k}">${l}</button>` : '';
    const st = a.allocation_status;
    const actions = [st === 'in_stock' || st === 'available' ? btn('reserve', '📌 حجز لموظف') : '', st === 'reserved' ? btn('unreserve', 'فك الحجز') : '',
      ['in_stock', 'available', 'reserved'].includes(st) ? btn('handover', '📤 تسليم', 'primary') : '', st === 'assigned' ? btn('return', '📥 استرجاع', 'primary') : '', st === 'assigned' ? btn('swap', '🔄 استبدال الجهاز') : '',
      !['in_maintenance', 'lost', 'retired'].includes(st) ? btn('maint', '🔧 فتح صيانة') : '', !['assigned', 'reserved', 'in_maintenance'].includes(st) ? btn('status', 'تغيير الحالة') : '',
      btn('link', '🖥 ربط شاشة/ملحق'), btn('edit', '✏️ تعديل البيانات'), btn('recat', '🏷 تغيير الفئة'),
      App.can('assets:view_secrets') ? `<button class="btn" data-do="pw">🔑 كلمة المرور${a.has_password ? '' : ' (غير محفوظة)'}</button>` : '',
      App.can('assets:print') ? `<a class="btn ghost" target="_blank" rel="noopener" href="/api/print/custody-item/${id}">🖨 نموذج عهدة منفصلة</a>` : ''].join('');
    el.innerHTML = App.head(a.code, `${esc(a.brand)} ${esc(a.model)} · ${allocBadge(a)} ${condBadge(a)}`, actions, 'assets') +
      `<div class="blocks"><div class="panel"><h2>البيانات</h2><dl class="kv">${[['الشركة', App.companyName(a.company_id)], ['الفئة', a.category_code], ['الفئة', (meta().categories.find(x => x.code === a.category_code) || {}).name_ar || a.category_code], ['السيريال / IMEI', a.serial], ['اسم الجهاز', a.hostname], ['المعالج', a.cpu], ['RAM', a.ram_gb], ['التخزين', a.storage], ['كارت الشاشة', a.gpu], ['النظام', a.os], ['الشاشة', a.screen_info], ['القسم', App.deptName(a.department_id)], ['الموقع', a.location], ['مع', a.holder_name || '—'], ['محجوز لـ', a.reserved_for_name || '—'], ['تاريخ الشراء', fmtDate(a.purchase_date)], ['الضمان حتى', fmtDate(a.warranty_end)], ['كلمة مرور الجهاز', a.has_password ? '🔑 محفوظة مشفّرة' : '—'], ['المصدر', a.source], ['ملاحظات', a.notes]].map(([k, v]) => `<dt>${k}</dt><dd>${esc(v || '—')}</dd>`).join('')}</dl><p>${accChips(a)}</p><div id="coForms"></div></div>
      <div class="panel"><h2>الشاشات والملحقات المربوطة</h2>${App.table(a.links, [{ label: 'الأصل', render: l => `<a href="#/asset/${l.asset_id}" class="mono">${esc(l.code)}</a>` }, { label: 'الموديل', key: 'model' }, { label: 'العلاقة', render: l => l.role === 'child' ? 'مربوط بالجهاز' : 'الجهاز الأساسي' }, { label: '', render: l => can ? `<button class="link danger" data-unlink="${l.id}">فك</button>` : '' }], { empty: 'لا يوجد' })}
      <h2 style="margin-top:14px">الصيانة</h2>${App.table(a.maintenance, [{ label: 'العطل', key: 'issue' }, { label: 'الجهة', render: m => esc(m.vendor || 'داخلي') }, { label: 'فتح', render: m => fmtDate(m.opened_at) }, { label: 'الحالة', render: m => m.status === 'open' ? (can ? `<button class="link" data-close="${m.id}">إقفال</button>` : 'مفتوحة') : 'مقفولة ' + fmtDate(m.closed_at) }, { label: 'تكلفة', render: m => m.financial_event_id ? `<a href="#/event/${m.financial_event_id}">مرتبطة</a>` : '—' }], { empty: 'لا توجد صيانات' })}</div></div>
      <div class="panel"><h2>سجل الحركات (غير قابل للتعديل)</h2>${App.table(a.events, [{ label: 'الوقت', render: e => fmtDT(e.at) }, { label: 'الحركة', key: 'kind' }, { label: 'من → إلى', render: e => esc((e.from_status || '') + (e.to_status ? ' → ' + e.to_status : '')) }, { label: 'النتيجة', key: 'result' }, { label: 'ملاحظات', key: 'notes' }], { compact: true })}</div>`;
    const reload = () => App.pages.asset(el, id);
    App.get('/api/print/forms?company_id=' + a.company_id).then(forms => {
      const box = el.querySelector('#coForms'); if (!box || !forms.length) return;
      box.innerHTML = `<h3 style="margin-top:12px">📄 نماذج الشركة</h3><div class="actions">${forms.map(f => `<a class="btn sm ghost" target="_blank" rel="noopener" href="/api/files/${f.file_id}">${esc(f.label)} (v${f.version})</a>`).join('')}</div>`;
    }).catch(() => {});
    const empAction = async (path, title, filter) => App.formModal(title, [{ name: 'employee_id', label: 'الموظف', options: await App.empOptions(filter), required: true, pick: 'employee', company: a.company_id }, { name: 'notes', label: 'ملاحظات', keepEmpty: true }], {},
      async d => { await App.post(`/api/assets/${id}/${path}`, Object.assign(d, { notes: d.notes || '', idempotency_key: path + '-' + id + '-' + Date.now() })); App.toast('تم'); reload(); },
      { intro: 'الموظفين اللي عليهم إخلاء طرف مفتوح ممنوع يستلموا عهدة جديدة (السيرفر هيرفض).' });
    el.onclick = async e => {
      const b = e.target.closest('[data-do]'), ul = e.target.closest('[data-unlink]'), cl = e.target.closest('[data-close]');
      try {
        if (ul) { if (await App.confirm('فك الربط', 'متأكد؟')) { await App.api('DELETE', '/api/assets/links/' + ul.dataset.unlink); reload(); } return; }
        if (cl) return App.formModal('إقفال الصيانة', [{ name: 'result', label: 'النتيجة', required: true }, { name: 'tech_condition', label: 'الحالة الفنية بعد الصيانة', options: Object.entries(meta().condition), required: true }, { name: 'closed_at', label: 'تاريخ الإقفال', type: 'date' }], { tech_condition: 'good' }, async d => { await App.post(`/api/assets/maintenance/${cl.dataset.close}/close`, d); reload(); });
        if (!b) return;
        const k = b.dataset.do;
        if (k === 'reserve') return empAction('reserve', 'حجز الأصل لموظف (تجهيز — ليس تسليمًا)', x => x.company_id === a.company_id && !x.custody_frozen);
        if (k === 'handover') return empAction('handover', 'تسليم الأصل', x => x.company_id === a.company_id);
        if (k === 'unreserve') { await App.post(`/api/assets/${id}/unreserve`); return reload(); }
        if (k === 'return') return App.formModal('استرجاع الأصل', [{ name: 'result', label: 'نتيجة الاسترجاع', options: [['returned', 'استُرجع سليم'], ['damaged', 'استُرجع تالف'], ['missing', 'مفقود — لم يُسترجع']], required: true }, { name: 'notes', label: 'ملاحظات', keepEmpty: true }, { name: 'evidence_file_id', label: 'إثبات (اختياري)', type: 'file', category: 'custody_evidence' }], {}, async d => { d.notes = d.notes || ''; d.idempotency_key = 'ret-' + id + '-' + Date.now(); await App.post(`/api/assets/${id}/return`, d); reload(); });
        if (k === 'maint') return App.formModal('فتح صيانة', [{ name: 'issue', label: 'العطل', required: true }, { name: 'vendor', label: 'جهة الصيانة (فاضي = داخلي)', keepEmpty: true }, { name: 'opened_at', label: 'التاريخ', type: 'date' }], {}, async d => { d.vendor = d.vendor || ''; await App.post(`/api/assets/${id}/maintenance`, d); reload(); }, { intro: 'التكلفة بتتسجل مرة واحدة من المالية ← مصروف مباشر مربوط بأمر الصيانة.' });
        if (k === 'status') return App.formModal('تغيير حالة التخصيص', [{ name: 'allocation_status', label: 'الحالة', options: [['in_stock', 'في المخزن'], ['available', 'خالي / متاح'], ['lost', 'مفقود'], ['retired', 'مكهّن'], ['pending_wipe', 'بانتظار مسح البيانات']], required: true }, { name: 'reason', label: 'السبب', required: true }], {}, async d => { await App.post(`/api/assets/${id}/status`, d); reload(); });
        if (k === 'link') {
          const all = await App.get('/api/assets' + App.qs({ company_id: a.company_id }));
          return App.formModal('ربط شاشة أو ملحق', [{ name: 'child_asset_id', label: 'الأصل', options: all.filter(x => x.id !== id).map(x => [x.id, x.code + ' — ' + x.model]), required: true }], {}, async d => { await App.post(`/api/assets/${id}/links`, d); reload(); });
        }
        if (k === 'recat') return App.changeCategory(a, reload);
        if (k === 'swap') return App.swapDevice(a, () => App.go('#/assets'));
        if (k === 'pw') {
          const showPw = async () => {
            const r = await App.post(`/api/assets/${id}/password/reveal`);
            App.modal({ title: 'كلمة مرور ' + a.code, body: `<p class="muted small">العرض ده اتسجل في سجل التدقيق. هتختفي بعد 30 ثانية.</p><div class="pick-wrap"><input id="pwv" readonly dir="ltr" class="mono" value="${esc(r.password)}"><button type="button" class="btn sm" id="pwc">📋 نسخ</button></div>`,
              onOpen: (m, close) => { m.querySelector('#pwc').onclick = async () => { try { await navigator.clipboard.writeText(r.password); App.toast('اتنسخت'); } catch (e) { m.querySelector('#pwv').select(); } }; setTimeout(close, 30000); } });
          };
          return App.modal({ title: '🔑 كلمة مرور الجهاز ' + a.code, body: `<p class="small">${a.has_password ? 'محفوظة مشفّرة في مخزن الأسرار (مش في قاعدة البيانات ولا في الشيت).' : 'مفيش كلمة مرور محفوظة للجهاز ده.'}</p>`,
            actions: [...(a.has_password ? [{ label: '👁 عرض', primary: true, onClick: showPw }] : []),
              { label: a.has_password ? 'تغيير' : 'حفظ كلمة مرور', onClick: () => App.formModal('كلمة مرور الجهاز', [{ name: 'password', label: 'كلمة المرور', type: 'password', required: true }], {}, async d => { await App.put(`/api/assets/${id}/password`, d); App.toast('اتحفظت مشفّرة'); reload(); }) },
              ...(a.has_password ? [{ label: 'مسح', danger: true, onClick: async () => { if (await App.confirm('مسح كلمة المرور', 'هتتمسح من المخزن نهائيًا.')) { await App.api('DELETE', `/api/assets/${id}/password`); reload(); } } }] : [])] });
        }
        if (k === 'edit') {
          const fields = assetFields(a.company_id).filter(f => !['company_id', 'category_code', 'allocation_status', 'device_password'].includes(f.name));
          const vals = Object.assign({}, a); vals.acc = Object.keys(ACC_AR).filter(x => (a.accessories || {})[x]);
          return App.formModal('تعديل ' + a.code, fields, vals, async d => { packAcc(d); d.version = a.version; await App.patch('/api/assets/' + id, d); App.toast('اتحفظ'); reload(); }, { wide: true, cols: 3 });
        }
      } catch (err) { App.fail(err); }
    };
  };

  async function reportView(el, title, sub, key, filters) {
    const r = await App.post(`/api/reports/${key}/run`, Object.assign({ company_id: App.scopeCompany() }, filters || {}));
    el.innerHTML = App.head(title, sub, '', 'assets') + `<div class="cards">${Object.entries(r.summary || {}).map(([k, v]) => `<div class="card"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('')}</div><div class="panel">${App.table(r.rows, r.columns.map(c => ({ label: c.label, key: c.key })), { empty: 'لا توجد بيانات' })}</div>`;
  }
  App.pages.custody = el => App.can('reports:view') ? reportView(el, 'العهد والحركات', 'كل تسليم/استرجاع/حجز بيتسجل كحدث — مفيش تعديل مباشر على خانة المستلم', 'assets.custody') : App.pages.assets(el, '', { allocation: 'assigned' });
  App.pages.maintenance = el => App.can('reports:view') ? reportView(el, 'الصيانة والضمان', 'أوامر الصيانة — والتكلفة مرتبطة بعملية مالية واحدة', 'assets.maintenance') : App.pages.assets(el, '', { allocation: 'in_maintenance' });
  App.pages.stock = async el => {
    const rows = (await App.get('/api/assets' + App.qs({ company_id: App.scopeCompany() }))).filter(a => ['in_stock', 'available', 'pending_wipe'].includes(a.allocation_status));
    const byCat = {}; rows.forEach(a => { byCat[a.category_code] = (byCat[a.category_code] || 0) + 1; });
    el.innerHTML = App.head('المخازن والجرد', 'الأصول غير المسلّمة (في المخزن / متاح / بانتظار مسح) — للجرد قارن السيريال بالواقع', '', 'assets') +
      `<div class="panel"><h2>حسب الفئة</h2>${App.bars(byCat, { href: k => '#/assets?category=' + encodeURIComponent(k) })}</div><div class="panel">${App.table(rows, [{ label: 'الكود', render: a => `<b class="mono">${esc(a.code)}</b>` }, { label: 'الجهاز', render: a => esc(a.brand + ' ' + a.model) }, { label: 'السيريال', render: a => `<span class="mono" dir="ltr">${esc(a.serial)}</span>` }, { label: 'الموقع', key: 'location' }, { label: 'التخصيص', render: allocBadge }, { label: 'الحالة الفنية', render: condBadge }], { href: a => '#/asset/' + a.id, empty: 'المخزن فاضي' })}</div>`;
  };
})(window.App);
