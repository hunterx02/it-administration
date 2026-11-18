/* M05 — IT procurement & finance. CEO approval is on paper; the system records evidence only. */
(function (App) {
  'use strict';
  const { esc, fmtDate, fmtDT, num } = App;
  let META = null;
  const meta = async () => META || (META = await App.get('/api/finance/meta'));
  const cid = () => App.scopeCompany() || (App.companies[0] || {}).id;
  const PRB = { draft: 'muted', awaiting_approval: 'warn', approved: 'good', rejected: 'bad', in_procurement: 'info', partially_received: 'info', received: 'good', closed: 'muted', cancelled: 'muted' };
  const suppliers = async () => (App.cache.suppliers = await App.get('/api/finance/suppliers'));

  App.pages.suppliers = async el => {
    const rows = await suppliers(), m = await meta();
    el.innerHTML = App.head('الموردون', 'بدون كلمات مرور أو بيانات بنكية حساسة', App.can('finance:create') ? '<button class="btn primary" id="newSup">+ مورد</button>' : '', 'finance', { io: 'suppliers' }) +
      `<div class="panel">${App.table(rows, [{ label: 'الاسم', render: s => `<b>${esc(s.name)}</b>` }, { label: 'النشاط', key: 'activity' }, { label: 'التواصل', key: 'contact' }, { label: 'العملات', key: 'currencies' }, { label: 'شروط الدفع', key: 'payment_terms' }, { label: 'الحالة', render: s => s.active ? App.badge('نشط', 'good') : App.badge('غير نشط', 'muted') }], { empty: 'لا يوجد موردون' })}</div>`;
    const b = el.querySelector('#newSup'); if (b) b.onclick = () => App.formModal('مورد جديد', [{ name: 'name', label: 'الاسم التجاري', required: true }, { name: 'activity', label: 'النشاط', lookup: 'vendor_activity' }, { name: 'contact', label: 'التواصل', keepEmpty: true }, { name: 'currencies', label: 'العملات (EGP, SAR...)', keepEmpty: true }, { name: 'payment_terms', label: 'شروط الدفع', keepEmpty: true }, { name: 'notes', label: 'ملاحظات', type: 'textarea', full: true, keepEmpty: true }], {},
      async d => { Object.keys(d).forEach(k => { if (d[k] == null) d[k] = ''; }); await App.post('/api/finance/suppliers', d); App.pages.suppliers(el); });
  };

  // ---------------- purchase requests
  App.pages.purchases = async (el, arg, q) => {
    const m = await meta(), rows = await App.get('/api/finance/purchase-requests' + App.qs({ company_id: App.scopeCompany(), status: q.status }));
    el.innerHTML = App.head('طلبات الشراء والاستلام', 'طلب ← طباعة ← اعتماد ورقي من الرئيس التنفيذي ← شراء خارج النظام ← استلام ← فاتورة ← دفع', App.can('finance:create') ? '<button class="btn primary" id="newPR">+ طلب شراء</button>' : '', 'finance') +
      `<div class="toolbar"><select id="stF" aria-label="الحالة"><option value="">كل الحالات</option>${Object.entries(m.pr_status).map(([k, v]) => `<option value="${k}" ${q.status === k ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select></div>
      <div class="panel">${App.table(rows, [{ label: 'الطلب', render: p => `<b class="mono">${esc(p.code)}</b>` }, { label: 'التاريخ', render: p => fmtDate(p.request_date) }, { label: 'السبب', key: 'reason' }, { label: 'تقديري', render: p => num(p.estimated_total) + ' ' + p.currency }, { label: 'فعلي مستلم', render: p => num(p.actual_received_total) }, { label: 'الحالة', render: p => App.badge(p.status_ar, PRB[p.status]) + (p.approval_date && !p.approval_file_id ? ' ' + App.badge('إثبات الاعتماد ناقص', 'warn') : '') }, { label: '', render: p => App.can('finance:print') ? `<a class="btn sm ghost" target="_blank" rel="noopener" href="/api/finance/purchase-requests/${p.id}/print">🖨 طباعة</a>` : '' }], { href: p => '#/purchase/' + p.id, empty: 'لا توجد طلبات' })}</div>`;
    el.querySelector('#stF').onchange = e => App.go('#/purchases' + App.qs({ status: e.target.value }));
    const b = el.querySelector('#newPR'); if (b) b.onclick = () => newPR();
  };

  async function newPR() {
    const c = cid(), sup = await suppliers(), cats = ((App.cache.assetMeta || {}).categories || []);
    const itemRow = i => `<div class="grid4 item-row" data-i="${i}"><label class="field"><span>الصنف *</span><input name="item"></label><label class="field"><span>الكمية *</span><input name="qty" type="number" min="1" value="1"></label><label class="field"><span>سعر الوحدة التقديري *</span><input name="est" type="number" step="any" value="0"></label><label class="field"><span>ينشئ أصول من فئة</span><select name="cat"><option value="">لا (مستهلك/خدمة)</option>${cats.map(x => `<option value="${esc(x.code)}">${esc(x.code + ' — ' + x.name_ar)}</option>`).join('')}</select></label></div>`;
    const fields = [{ name: 'company_id', label: 'الشركة', type: 'company', value: c, required: true }, { name: 'requester', label: 'مقدم الطلب', required: true, value: App.me.name }, { name: 'reason', label: 'سبب الشراء', required: true }, { name: 'priority', label: 'الأولوية', options: [['normal', 'عادي'], ['high', 'مرتفع'], ['urgent', 'عاجل'], ['low', 'منخفض']] }, { name: 'currency', label: 'العملة', value: 'EGP', required: true, lookup: 'currency' }, { name: 'suggested_supplier_id', label: 'مورد مقترح', options: [['', '—']].concat(sup.map(s => [s.id, s.name])), pick: 'supplier' }, { name: 'notes', label: 'ملاحظات', keepEmpty: true }];
    App.modal({ title: 'طلب شراء IT', wide: true, body: App.form(fields, {}, 3) + `<label class="field"><span>الأقسام المستفيدة (مختلفة عن مقدم الطلب)</span><select id="deps" multiple size="4">${App.departments(c).map(d => `<option value="${d.id}">${esc(d.name)}</option>`).join('')}</select></label><h3 style="margin-top:12px">البنود</h3><div id="items">${itemRow(0)}</div><button class="btn sm" id="addIt" type="button">+ بند</button>`,
      onOpen: m => { let n = 1; m.querySelector('#addIt').onclick = () => m.querySelector('#items').insertAdjacentHTML('beforeend', itemRow(n++)); },
      actions: [{ label: 'حفظ الطلب', primary: true, onClick: async m => {
        const d = App.readForm(m, fields); d.notes = d.notes || ''; d.currency = (d.currency || '').toUpperCase();
        d.beneficiary_department_ids = Array.from(m.querySelector('#deps').selectedOptions).map(o => o.value);
        d.items = App.$$('.item-row', m).map(r => ({ item: r.querySelector('[name=item]').value.trim(), qty: +r.querySelector('[name=qty]').value, est_unit_cost: +r.querySelector('[name=est]').value, asset_category_code: r.querySelector('[name=cat]').value || null })).filter(x => x.item);
        if (!d.items.length) throw new Error('أضف بند واحد على الأقل');
        const pr = await App.post('/api/finance/purchase-requests', d); App.go('#/purchase/' + pr.id);
      } }] });
  }

  App.pages.purchase = async (el, id) => {
    const p = await App.get('/api/finance/purchase-requests/' + id), sup = await suppliers();
    const can = k => App.can('finance:' + k);
    const acts = [`<a class="btn" target="_blank" rel="noopener" href="/api/finance/purchase-requests/${id}/print" id="prt">🖨 طباعة للاعتماد الورقي</a>`,
      p.status === 'awaiting_approval' && can('approve') ? '<button class="btn primary" data-do="approval">تسجيل نتيجة الاعتماد الورقي</button>' : '',
      ['approved', 'in_procurement', 'partially_received'].includes(p.status) && can('edit') ? '<button class="btn primary" data-do="receive">📦 تسجيل استلام</button>' : '',
      ['received', 'partially_received'].includes(p.status) && can('edit') ? '<button class="btn" data-do="close">إغلاق الطلب</button>' : '',
      !['received', 'closed', 'cancelled'].includes(p.status) && can('edit') ? '<button class="btn danger" data-do="cancel">إلغاء (بتصحيح مؤرخ)</button>' : ''].join('');
    el.innerHTML = App.head(p.code, `${App.badge(p.status_ar, PRB[p.status])} · ${esc(App.companyName(p.company_id))}`, acts, 'finance') +
      (p.status === 'awaiting_approval' ? '<div class="notice warn">الطلب بانتظار الاعتماد الورقي من الرئيس التنفيذي. النظام لا يطبع توقيعًا ولا يعتبر الطباعة اعتمادًا.</div>' : '') +
      (p.approval_date && !p.approval_file_id ? '<div class="notice warn">الاعتماد مسجل بدون صورة الورقة الموقعة — «بانتظار رفع الإثبات».</div>' : '') +
      `<div class="blocks"><div class="panel"><h2>البيانات</h2><dl class="kv">${[['مقدم الطلب', p.requester], ['السبب', p.reason], ['الأولوية', p.priority], ['الأقسام المستفيدة', p.beneficiary_department_ids.map(App.deptName).join('، ')], ['المعتمد (ورقي)', p.approver_name], ['تاريخ الاعتماد', fmtDate(p.approval_date)], ['تقديري', num(p.estimated_total) + ' ' + p.currency], ['فعلي مستلم', num(p.actual_received_total)], ['الفرق', num(p.difference)]].map(([k, v]) => `<dt>${k}</dt><dd>${esc(v || '—')}</dd>`).join('')}</dl>
        ${p.approval_file_id ? `<a target="_blank" rel="noopener" href="/api/files/${p.approval_file_id}">صورة الاعتماد الورقي</a>` : ''} ${p.financial_event_id ? ` · <a href="#/event/${p.financial_event_id}">العملية المالية</a>` : ''}</div>
      <div class="panel"><h2>البنود</h2>${App.table(p.items, [{ label: 'الصنف', key: 'item' }, { label: 'الكمية', key: 'qty' }, { label: 'مستلم', key: 'received_qty' }, { label: 'متبقي', key: 'remaining_qty' }, { label: 'تقديري/وحدة', render: i => num(i.est_unit_cost) }, { label: 'أصول', render: i => i.asset_category_code || '—' }])}</div></div>
      <div class="panel"><h2>الاستلامات</h2>${App.table(p.receipts, [{ label: 'التاريخ', render: r => fmtDate(r.received_on) }, { label: 'الكمية', key: 'qty' }, { label: 'فعلي/وحدة', render: r => num(r.actual_unit_cost) }, { label: 'أصول اتعملت', render: r => r.asset_ids.map(a => `<a href="#/asset/${a}">أصل</a>`).join(' ') || '—' }, { label: 'ملاحظات', key: 'notes' }], { empty: 'لا توجد استلامات — الأصل مش بيتسجل قبل الاستلام الفعلي' })}</div>`;
    const reload = () => App.pages.purchase(el, id);
    el.onclick = async ev => {
      const b = ev.target.closest('[data-do]'); if (!b) return;
      try {
        const k = b.dataset.do;
        if (k === 'approval') return App.formModal('نتيجة الاعتماد الورقي', [{ name: 'decision', label: 'القرار', options: [['approved', 'اعتمد ورقيًا'], ['rejected', 'رفض ورقيًا']], required: true }, { name: 'approver_name', label: 'اسم المعتمد', required: true }, { name: 'approval_date', label: 'تاريخ التوقيع', type: 'date', required: true }, { name: 'approval_file_id', label: 'صورة الورقة الموقعة', type: 'file', category: 'paper_approval' }], { company_id: p.company_id },
          async d => { await App.post(`/api/finance/purchase-requests/${id}/approval`, d); reload(); }, { intro: 'لازم تكون طبعت الطلب الأول. لو مفيش صورة دلوقتي، هيتعلّم «إثبات ناقص».' });
        if (k === 'receive') { const open = p.items.filter(i => i.remaining_qty > 0); return App.formModal('تسجيل استلام (جزئي مسموح)', [{ name: 'item_id', label: 'البند', options: open.map(i => [i.id, `${i.item} (متبقي ${i.remaining_qty})${i.asset_category_code ? ' — ينشئ أصول' : ''}`]), required: true }, { name: 'qty', label: 'الكمية المستلمة', type: 'number', required: true }, { name: 'actual_unit_cost', label: 'التكلفة الفعلية للوحدة', type: 'number', required: true }, { name: 'supplier_id', label: 'المورد', options: [['', '—']].concat(sup.map(s => [s.id, s.name])), pick: 'supplier' }, { name: 'received_on', label: 'تاريخ الاستلام', type: 'date' }, { name: 'serials', label: 'السيريالات (سطر لكل جهاز — مطلوب لبنود الأصول)', type: 'textarea', full: true, keepEmpty: true }, { name: 'notes', label: 'ملاحظات', keepEmpty: true }], {},
          async d => { d.serials = (d.serials || '').split(/\n+/).map(s => s.trim()).filter(Boolean); d.notes = d.notes || ''; const r = await App.post(`/api/finance/purchase-requests/${id}/receive`, d); App.toast(r.created_asset_ids.length ? `اتسجل ${r.created_asset_ids.length} أصل في المخزن` : 'اتسجل الاستلام'); reload(); }); }
        if (k === 'close') { await App.post(`/api/finance/purchase-requests/${id}/close`); return reload(); }
        if (k === 'cancel') return App.formModal('إلغاء الطلب', [{ name: 'reason', label: 'السبب', required: true }], {}, async d => { await App.post(`/api/finance/purchase-requests/${id}/cancel`, d); reload(); });
      } catch (err) { App.fail(err); }
    };
  };

  // ---------------- expenses / events
  App.pages.expenses = async (el, arg, q) => {
    const m = await meta(), rows = await App.get('/api/finance/events' + App.qs({ company_id: App.scopeCompany(), kind: q.kind }));
    el.innerHTML = App.head('المصروفات والفواتير والمدفوعات', 'كل تكلفة = عملية اقتصادية واحدة (حتى لو مربوطة بأكتر من وحدة) — متوقع ≠ مفوتر ≠ مدفوع', App.can('finance:create') ? '<button class="btn primary" id="newEx">+ مصروف مباشر</button>' : '', 'finance') +
      `<div class="toolbar"><select id="kF" aria-label="النوع"><option value="">كل الأنواع</option>${Object.entries(m.expense_kinds).map(([k, v]) => `<option value="${k}" ${q.kind === k ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select></div>
      <div class="panel">${App.table(rows, [{ label: 'الكود', render: e => `<span class="mono">${esc(e.code)}</span>` }, { label: 'التاريخ', render: e => fmtDate(e.occurred_on) }, { label: 'النوع', key: 'kind_ar' }, { label: 'الوصف', key: 'description' }, { label: 'متوقع', render: e => num(e.committed) + ' ' + e.currency }, { label: 'مفوتر', render: e => num(e.invoiced) }, { label: 'مدفوع', render: e => num(e.paid) }, { label: 'المصدر', render: e => esc(e.source_module + ' / ' + e.source_type) }, { label: '', render: e => (e.status === 'cancelled' ? App.badge('ملغاة', 'muted') : '') + (e.missing_document ? ' ' + App.badge('مستند ناقص', 'warn') : '') }], { href: e => '#/event/' + e.id, empty: 'لا توجد مصروفات' })}</div>`;
    el.querySelector('#kF').onchange = e => App.go('#/expenses' + App.qs({ kind: e.target.value }));
    const b = el.querySelector('#newEx'); if (b) b.onclick = async () => {
      const c = cid(), sup = await suppliers();
      App.formModal('مصروف مباشر (بدون طلب شراء)', [{ name: 'company_id', label: 'الشركة', type: 'company', value: c, required: true }, { name: 'kind', label: 'النوع', options: Object.entries(m.expense_kinds), required: true }, { name: 'description', label: 'الوصف', required: true }, { name: 'amount', label: 'المبلغ', type: 'number', required: true }, { name: 'currency', label: 'العملة', value: 'EGP', required: true, lookup: 'currency' }, { name: 'occurred_on', label: 'تاريخ الواقعة', type: 'date', required: true }, { name: 'supplier_id', label: 'المورد', options: [['', '—']].concat(sup.map(s => [s.id, s.name])), pick: 'supplier' },
        { name: 'link', label: 'ربط بـ', options: [['', 'بدون ربط'], ['M02:maintenance', 'أمر صيانة (M02)'], ['M04:renewal', 'حدث تجديد (M04)']] }, { name: 'source_id', label: 'معرّف أمر الصيانة / حدث التجديد', hint: 'من صفحة الأصل أو سجل التجديدات' }], {},
      async d => { if (d.link) { const [mod, typ] = d.link.split(':'); d.source_module = mod; d.source_type = typ; } delete d.link; d.currency = d.currency.toUpperCase(); const e = await App.post('/api/finance/expenses', d); App.go('#/event/' + e.id); },
      { cols: 2, intro: 'دفع تجديد هنا مش بيغيّر موعد الخدمة — التجديد بيتسجل في الخدمات التقنية.' });
    };
  };

  App.pages.event = async (el, id) => {
    const e = await App.get('/api/finance/events/' + id), sup = await suppliers(), can = k => App.can('finance:' + k);
    const allocTotal = e.allocations.reduce((a, x) => a + Number(x.percentage), 0);
    el.innerHTML = App.head(e.code, `${esc(e.kind_ar)} · ${esc(e.description)} ${e.status === 'cancelled' ? App.badge('ملغاة: ' + e.cancel_reason, 'muted') : ''}`,
      (e.status === 'active' && can('create') ? '<button class="btn primary" id="invB">+ فاتورة</button>' : '') + (e.status === 'active' && can('edit') ? '<button class="btn" id="alB">توزيع التكلفة</button>' : '') + (e.status === 'active' && can('approve') ? '<button class="btn danger" id="cnB">إلغاء بتصحيح</button>' : ''), 'finance') +
      `<div class="cards"><div class="card"><span>متوقع / ملتزم</span><b>${num(e.committed)} ${e.currency}</b></div><div class="card"><span>مفوتر</span><b>${num(e.invoiced)}</b></div><div class="card"><span>مدفوع</span><b>${num(e.paid)}</b></div><div class="card"><span>متبقي على الفواتير</span><b class="${Number(e.outstanding) ? 't-bad' : ''}">${num(e.outstanding)}</b></div></div>
      <div class="blocks"><div class="panel"><h2>الفواتير</h2>${App.table(e.invoices, [{ label: 'الرقم', key: 'number' }, { label: 'التاريخ', render: i => fmtDate(i.invoice_date) }, { label: 'الإجمالي', render: i => num(i.total) + ' ' + i.currency }, { label: 'مدفوع', render: i => num(i.paid) }, { label: '', render: i => (i.file_id ? `<a class="link" target="_blank" rel="noopener" href="/api/files/${i.file_id}">المستند</a>` : App.badge('بدون مستند', 'warn')) + (can('create') && Number(i.paid) < Number(i.total) ? `<button class="link" data-pay="${i.id}" data-left="${Number(i.total) - Number(i.paid)}">+ دفعة</button>` : '') }], { empty: 'لا توجد فواتير — وجود الفاتورة مش معناه إنها اتدفعت' })}</div>
      <div class="panel"><h2>التوزيع على المستفيدين (${allocTotal}%)</h2>${App.table(e.allocations, [{ label: 'المستفيد', render: a => a.department_id ? esc(App.deptName(a.department_id)) : 'على مستوى الشركة' }, { label: 'النسبة', render: a => num(a.percentage) + '%' }, { label: 'السبب', key: 'reason' }], { empty: 'غير موزعة' })}<p class="muted small">تقارير الأقسام بتجمع حصة كل قسم بس — إجمالي الشركة بيتحسب مرة واحدة.</p></div></div>
      <p class="muted small">المصدر: ${esc(e.source_module)} / ${esc(e.source_type)} · <span class="mono">${esc(e.source_id)}</span></p>`;
    const reload = () => App.pages.event(el, id);
    el.onclick = async ev => {
      const pay = ev.target.closest('[data-pay]');
      try {
        if (pay) return App.formModal('تسجيل دفعة', [{ name: 'paid_on', label: 'تاريخ الدفع الفعلي', type: 'date', required: true }, { name: 'amount', label: `المبلغ (المتبقي ${pay.dataset.left})`, type: 'number', required: true }, { name: 'method', label: 'الطريقة', keepEmpty: true, lookup: 'payment_method' }, { name: 'receipt_ref', label: 'مرجع الإيصال', keepEmpty: true }, { name: 'receipt_file_id', label: 'صورة الإيصال', type: 'file', category: 'receipt' }], { company_id: e.company_id },
          async d => { d.method = d.method || ''; d.receipt_ref = d.receipt_ref || ''; await App.post(`/api/finance/invoices/${pay.dataset.pay}/payments`, d); reload(); });
        if (ev.target.id === 'invB') return App.formModal('فاتورة مورد', [{ name: 'supplier_id', label: 'المورد', options: sup.map(s => [s.id, s.name]), required: true, pick: 'supplier' }, { name: 'number', label: 'رقم الفاتورة', required: true }, { name: 'invoice_date', label: 'التاريخ', type: 'date', required: true }, { name: 'currency', label: 'العملة', value: e.currency, required: true, lookup: 'currency' }, { name: 'base_amount', label: 'المبلغ الأساسي', type: 'number', required: true }, { name: 'tax', label: 'الضريبة', type: 'number' }, { name: 'shipping', label: 'الشحن', type: 'number' }, { name: 'discount', label: 'الخصم', type: 'number' }, { name: 'file_id', label: 'مستند الفاتورة', type: 'file', category: 'invoice' }], { company_id: e.company_id },
          async d => { ['tax', 'shipping', 'discount'].forEach(k => { if (d[k] == null) d[k] = 0; }); d.financial_event_id = id; await App.post('/api/finance/invoices', d); reload(); }, { cols: 3 });
        if (ev.target.id === 'alB') {
          const deps = App.departments(e.company_id);
          return App.modal({ title: 'توزيع التكلفة (المجموع = 100%)', wide: true, body: `<p class="muted small">سيب القسم فاضي = على مستوى الشركة (Company-wide).</p><div id="al">${(e.allocations.length ? e.allocations : [{ department_id: null, percentage: 100 }]).map(a => alRow(deps, a)).join('')}</div><button type="button" class="btn sm" id="addAl">+ مستفيد</button>`,
            onOpen: m => { m.querySelector('#addAl').onclick = () => m.querySelector('#al').insertAdjacentHTML('beforeend', alRow(deps, {})); },
            actions: [{ label: 'حفظ', primary: true, onClick: async m => { const lines = App.$$('.al-row', m).map(r => ({ department_id: r.querySelector('select').value || null, percentage: Number(r.querySelector('input').value), reason: '' })).filter(x => x.percentage); await App.put(`/api/finance/events/${id}/allocations`, { lines }); reload(); } }] });
        }
        if (ev.target.id === 'cnB') return App.formModal('إلغاء العملية (تصحيح مؤرخ — لا حذف)', [{ name: 'reason', label: 'السبب', required: true }], {}, async d => { await App.post(`/api/finance/events/${id}/cancel`, d); reload(); });
      } catch (err) { App.fail(err); }
    };
  };
  const alRow = (deps, a) => `<div class="grid2 al-row"><label class="field"><span>القسم</span><select><option value="">على مستوى الشركة</option>${deps.map(d => `<option value="${d.id}" ${d.id === a.department_id ? 'selected' : ''}>${esc(d.name)}</option>`).join('')}</select></label><label class="field"><span>النسبة %</span><input type="number" step="any" value="${a.percentage == null ? '' : Number(a.percentage)}"></label></div>`;

  // ---------------- budgets & financial reports
  App.pages.budgets = async (el, arg, q) => {
    const today = new Date().toISOString().slice(0, 10), first = today.slice(0, 8) + '01';
    const f = { date_from: q.date_from || first, date_to: q.date_to || today, basis: q.basis || 'paid', company_id: App.scopeCompany() };
    const s = await App.get('/api/finance/summary' + App.qs(f));
    el.innerHTML = App.head('الميزانيات والتقارير المالية', 'كل تقرير بيوضح أساسه (مدفوع/مفوتر/متوقع) ومحور تاريخه — والعملات منفصلة', App.can('finance:edit') ? '<button class="btn" id="budB">تحديد ميزانية</button>' : '', 'finance') +
      `<div class="toolbar"><label class="mini">من <input type="date" id="df" value="${f.date_from}"></label><label class="mini">إلى <input type="date" id="dt" value="${f.date_to}"></label><select id="bs">${[['paid', 'مدفوع (تاريخ الدفع)'], ['invoiced', 'مفوتر (تاريخ الفاتورة)'], ['committed', 'متوقع/ملتزم (تاريخ الواقعة)']].map(([k, v]) => `<option value="${k}" ${f.basis === k ? 'selected' : ''}>${v}</option>`).join('')}</select><button class="btn sm" id="goB">عرض</button></div>
      <div class="cards">${Object.entries(s.totals).map(([c, v]) => `<div class="card hero"><span>الإجمالي (${c})</span><b>${num(v)}</b><small>${esc(s.basis)}</small></div>`).join('') || '<div class="card"><span>الإجمالي</span><b>0</b></div>'}</div>
      <div class="blocks"><div class="panel"><h2>حسب القسم المستفيد</h2>${App.table(Object.entries(s.by_department).map(([k, v]) => ({ k: k === 'company_wide' ? 'على مستوى الشركة' : k === 'unallocated' ? 'غير موزع' : App.deptName(k), v: Object.entries(v).map(([c, a]) => num(a) + ' ' + c).join(' · ') })), [{ label: 'القسم', key: 'k' }, { label: 'المبلغ', key: 'v' }], { empty: 'لا يوجد' })}</div>
      <div class="panel"><h2>حسب النوع</h2>${App.table(Object.entries(s.by_kind).map(([k, v]) => ({ k, v: Object.entries(v).map(([c, a]) => num(a) + ' ' + c).join(' · ') })), [{ label: 'النوع', key: 'k' }, { label: 'المبلغ', key: 'v' }], { empty: 'لا يوجد' })}</div></div>
      <div class="panel"><h2>العمليات</h2>${App.table(s.rows, [{ label: 'العملية', key: 'event' }, { label: 'النوع', key: 'kind' }, { label: 'الوصف', key: 'description' }, { label: 'التاريخ', key: 'occurred_on' }, { label: 'المبلغ', render: r => num(r.amount) + ' ' + r.currency }], { compact: true })}</div>`;
    el.querySelector('#goB').onclick = () => App.go('#/budgets' + App.qs({ date_from: el.querySelector('#df').value, date_to: el.querySelector('#dt').value, basis: el.querySelector('#bs').value }));
    const b = el.querySelector('#budB'); if (b) b.onclick = () => App.formModal('ميزانية IT', [{ name: 'company_id', label: 'الشركة', type: 'company', value: cid(), required: true }, { name: 'year', label: 'السنة', type: 'number', value: new Date().getFullYear(), required: true }, { name: 'month', label: 'الشهر (فاضي = السنة كلها)', type: 'number' }, { name: 'currency', label: 'العملة', value: 'EGP', required: true, lookup: 'currency' }, { name: 'amount', label: 'المبلغ', type: 'number', required: true }], {}, async d => { d.kind = 'all'; d.currency = d.currency.toUpperCase(); await App.put('/api/finance/budgets', d); App.toast('اتحفظت الميزانية'); });
  };
})(window.App);
