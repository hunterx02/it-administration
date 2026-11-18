/* M03 — Employee journey. HR owns the final decision; IT sees logistics only (server enforces it). */
(function (App) {
  'use strict';
  const { esc, fmtDate, fmtDT } = App;
  let META = null;
  const meta = async () => META || (META = await App.get('/api/journey/meta'));
  const STATUS = { onboarding: ['تعيين', 'info'], active: ['نشط', 'good'], offboarding: ['إخلاء', 'warn'], exited: ['خرج', 'muted'] };
  const DOC = { missing: ['ناقص', 'bad'], received: ['مستلم — بانتظار التحقق', 'warn'], verified: ['متحقق', 'good'], not_applicable: ['لا ينطبق', 'muted'], hr_review: ['يحتاج مراجعة HR (النوع غير محدد)', 'warn'] };
  const qrSvg = text => { if (typeof window.qrcode !== 'function') return ''; const q = window.qrcode(0, 'M'); q.addData(text); q.make(); return q.createSvgTag({ cellSize: 5, margin: 2, scalable: true }); };
  const cid = () => App.scopeCompany() || (App.companies[0] || {}).id;

  // ---------------- employees
  App.pages.employees = async (el, arg, q) => {
    const rows = await App.get('/api/employees' + App.qs({ company_id: App.scopeCompany(), status: q.status }));
    el.innerHTML = App.head('الموظفون', 'سجل الموظفين — كل موظف له كود تلقائي من بادئة الشركة · البيانات الحساسة تظهر لـ HR فقط', (App.can('employees:edit') && rows.some(r => !r.employee_code) ? `<button class="btn" id="genCodes">🏷️ توليد أكواد للناقصين (${rows.filter(r => !r.employee_code).length})</button>` : '') + (App.can('employees:create') ? '<button class="btn primary" id="newE">+ موظف</button>' : ''), 'journey', { io: 'employees' }) +
      `<div class="toolbar">${Object.entries(STATUS).map(([k, v]) => `<a class="btn sm ${q.status === k ? 'primary' : ''}" href="#/employees?status=${k}">${v[0]}</a>`).join('')}<a class="btn sm ghost" href="#/employees">الكل</a></div>
      <div class="panel">${App.table(rows, [{ label: 'الاسم', render: e => `<b>${esc(e.full_name)}</b>` }, { label: 'الكود', render: e => `<span class="mono">${esc(e.employee_code || '')}</span>` }, { label: 'الوظيفة', key: 'job_title' }, { label: 'القسم', key: 'department_name' }, { label: 'الحالة', render: e => App.badge(STATUS[e.status][0], STATUS[e.status][1]) + (e.custody_frozen ? ' ' + App.badge('عهدة مجمّدة', 'warn') : '') }], { href: e => '#/employee/' + e.id, empty: 'لا يوجد موظفون' })}</div>`;
    const g = el.querySelector('#genCodes'); if (g) g.onclick = async () => {
      try {
        if (!await App.confirm('توليد أكواد', 'هيتعمل كود تلقائي (مثال ZW-EMP-0005) لكل موظف ملوش كود. الأكواد الموجودة مش هتتغير.')) return;
        const r = await App.post('/api/employees/assign-codes' + App.qs({ company_id: App.scopeCompany() }));
        App.toast(`اتعمل ${r.assigned} كود`); App.invalidateEmployees && App.invalidateEmployees(); App.render();
      } catch (err) { App.fail(err); }
    };
    const b = el.querySelector('#newE'); if (b) b.onclick = () => {
      const c = cid();
      App.formModal('إضافة موظف حالي', [{ name: 'company_id', label: 'الشركة', type: 'company', value: c, required: true }, { name: 'full_name', label: 'الاسم', required: true }, { name: 'employee_code', label: 'الكود الوظيفي (فاضي = تلقائي)', ltr: true }, { name: 'job_title', label: 'الوظيفة', lookup: 'job_title', keepEmpty: true }, { name: 'department_id', label: 'القسم', type: 'department', company: c }, { name: 'gender', label: 'النوع (لقاعدة مستند التجنيد)', options: [['', 'غير محدد'], ['male', 'ذكر'], ['female', 'أنثى']], keepEmpty: true }, { name: 'email', label: 'الإيميل', keepEmpty: true, ltr: true }, { name: 'phone', label: 'الهاتف', keepEmpty: true, ltr: true }, { name: 'hire_date', label: 'تاريخ التعيين', type: 'date' }], {},
        async d => { ['job_title', 'email', 'phone', 'gender'].forEach(k => { d[k] = d[k] || ''; }); const e = await App.post('/api/employees', d); App.go('#/employee/' + e.id); }, { cols: 2 });
    };
  };

  App.pages.employee = async (el, id) => {
    const e = await App.get('/api/employees/' + id), m = await meta(), sens = App.can('employees:view_sensitive');
    const cust = e.custody;
    el.innerHTML = App.head(e.full_name, `${esc(e.job_title)} · ${esc(e.department_name)} · ${App.badge(STATUS[e.status][0], STATUS[e.status][1])}${e.custody_frozen ? ' ' + App.badge('ممنوع تسليم عهدة جديدة', 'warn') : ''}`,
      (App.can('assets:print') || App.can('employees:print') ? `<a class="btn ghost" target="_blank" rel="noopener" href="/api/print/custody/${id}?kind=${e.status === 'offboarding' || e.status === 'exited' ? 'offboarding' : 'onboarding'}">🖨 بيان العهد (IT)</a>` : '') +
      (App.can('assets:view_secrets') ? `<a class="btn ghost" target="_blank" rel="noopener" title="بيطبع رمز الجهاز في البيان — العملية بتتسجل" href="/api/print/custody/${id}?secret=1&kind=${e.status === 'offboarding' || e.status === 'exited' ? 'offboarding' : 'onboarding'}">🖨🔑 البيان بالرمز</a>` : '') +
      (App.can('employees:print') ? `<a class="btn ghost" target="_blank" rel="noopener" href="/api/print/work-start/${id}">🖨 إقرار استلام عمل</a>` : '') +
      (e.onboarding_id ? `<a class="btn" href="#/onboard/${e.onboarding_id}">ملف التعيين</a>` : '') + (e.offboarding_id ? `<a class="btn" href="#/offboard/${e.offboarding_id}">ملف الإخلاء</a>` : (App.can('employees:approve') && e.status !== 'exited' ? '<button class="btn danger" id="offB">🚪 فتح إخلاء طرف</button>' : '')) + (sens && App.can('employees:edit') ? '<button class="btn" id="sensB">🔒 البيانات الحساسة</button>' : ''), 'journey') +
      `<div class="blocks"><div class="panel"><h2>البيانات</h2><dl class="kv">${[['الكود', e.employee_code], ['الإيميل', e.email], ['الهاتف', e.phone], ['كود البصمة', e.biometric_code], ['تاريخ التعيين', fmtDate(e.hire_date)], ['تاريخ الخروج', fmtDate(e.exit_date)]].map(([k, v]) => `<dt>${k}</dt><dd>${esc(v || '—')}</dd>`).join('')}
        ${sens && e.sensitive ? `<dt>الرقم القومي</dt><dd class="mono">${esc(e.sensitive.national_id || '—')}</dd><dt>المرتب</dt><dd>${esc(e.sensitive.salary || '—')}</dd>` : ''}</dl></div>
      <div class="panel"><h2>العهدة الحالية</h2>${App.table(cust.assets, [{ label: 'الأصل', render: a => `<a href="#/asset/${a.id}" class="mono">${esc(a.code)}</a>` }, { label: 'الموديل', key: 'model' }, { label: 'السيريال', render: a => `<span class="mono" dir="ltr">${esc(a.serial)}</span>` }], { empty: 'لا توجد أصول' })}
        ${App.table(cust.sims, [{ label: 'الشريحة', render: s => `<a href="#/sim/${s.id}" class="mono">${esc(s.code)}</a>` }, { label: 'الرقم', render: s => `<span dir="ltr">${esc(s.number)}</span>` }], { empty: 'لا توجد شرائح' })}<div id="coForms"></div></div></div>
      <div class="blocks"><div class="panel"><h2>مستندات التعيين الخمسة</h2>${App.table(e.joining_documents, [{ label: 'المستند', key: 'label' }, { label: 'الحالة', render: d => App.badge(DOC[d.status][0], DOC[d.status][1]) }, { label: '', render: d => sens && App.can('employees:edit') && d.status !== 'not_applicable' ? `<button class="link" data-up="${d.doc_type}">رفع</button>${d.status === 'received' && App.can('employees:approve') ? `<button class="link" data-ver="${d.doc_type}">تحقق</button>` : ''}${d.file_id ? `<a class="link" href="/api/files/${d.file_id}" target="_blank" rel="noopener">عرض</a>` : ''}` : '' }])}
        ${sens ? '' : '<p class="muted small">الملفات نفسها متاحة للموارد البشرية فقط.</p>'}</div>
      <div class="panel"><h2>الإقرارات</h2>${App.table(e.acks, [{ label: 'الإقرار', render: a => esc(m.ack_kinds[a.kind] || a.kind) }, { label: 'الإصدار', render: a => a.template_version ? 'v' + a.template_version : '—' }, { label: 'الطريقة', render: a => a.method === 'paper_scan' ? 'ورقي + نسخة مرفوعة' : 'إقرار قراءة إلكتروني (ليس توقيعًا قانونيًا)' }, { label: 'التاريخ', render: a => fmtDate(a.acknowledged_on) }], { empty: 'لا توجد إقرارات' })}
        ${App.can('employees:edit') ? '<button class="btn" id="ackB">+ تسجيل إقرار</button>' : ''}</div></div>` +
      (App.mailPanel ? App.mailPanel(e) : '');
    const reload = () => App.pages.employee(el, id);
    if (App.bindMailPanel) App.bindMailPanel(el, e, reload);
    App.get('/api/print/forms?company_id=' + e.company_id).then(forms => {
      const box = el.querySelector('#coForms'); if (!box || !forms.length) return;
      box.innerHTML = `<h3 style="margin-top:12px">📄 نماذج الشركة للطباعة</h3><div class="actions">${forms.map(f => `<a class="btn sm ghost" target="_blank" rel="noopener" href="/api/files/${f.file_id}">${esc(f.label)} (v${f.version})</a>`).join('')}</div>`;
    }).catch(() => {});
    el.onclick = async ev => {
      const up = ev.target.closest('[data-up]'), ver = ev.target.closest('[data-ver]');
      try {
        if (up) return App.formModal('رفع ' + m.joining_docs[up.dataset.up], [{ name: 'file_id', label: 'الملف', type: 'file', category: 'joining_document', required: true, accept: '.pdf,.png,.jpg,.jpeg' }], { company_id: e.company_id }, async d => { await App.post(`/api/employees/${id}/joining-docs/${up.dataset.up}`, d); reload(); });
        if (ver) { await App.post(`/api/employees/${id}/joining-docs/${ver.dataset.ver}/verify`); return reload(); }
        if (ev.target.id === 'ackB') return App.formModal('تسجيل إقرار', [{ name: 'kind', label: 'الإقرار', options: Object.entries(m.ack_kinds), required: true }, { name: 'method', label: 'الطريقة', options: [['paper_scan', 'توقيع ورقي + رفع النسخة'], ['e_ack', 'إقرار قراءة إلكتروني']], required: true }, { name: 'evidence_file_id', label: 'النسخة الموقعة (مطلوبة للورقي)', type: 'file', category: 'hr_document' }, { name: 'acknowledged_on', label: 'التاريخ', type: 'date' }], { company_id: e.company_id },
          async d => { await App.post(`/api/employees/${id}/acks`, d); reload(); }, { intro: 'الإقرار بيتسجل على النسخة الفعّالة الحالية من ملف الشركة. التوقيع الإلكتروني القانوني غير مفعّل (سياسة معلقة).' });
        if (ev.target.id === 'offB') return App.formModal('فتح إخلاء طرف', [{ name: 'last_day', label: 'آخر يوم عمل', type: 'date', required: true }, { name: 'reason', label: 'السبب', lookup: 'offboarding_reason', keepEmpty: true }], {},
          async d => { d.employee_id = id; d.reason = d.reason || ''; const o = await App.post('/api/journey/offboarding', d); App.go('#/offboard/' + o.id); }, { intro: '⚠️ بمجرد الفتح: أي تسليم عهدة جديدة للموظف هيترفض من كل القنوات، والتجميد ده مش بيتلغي.' });
        if (ev.target.id === 'sensB') return App.formModal('البيانات الحساسة (HR فقط)', [{ name: 'national_id', label: 'الرقم القومي', keepEmpty: true, ltr: true }, { name: 'birth_date', label: 'تاريخ الميلاد', type: 'date' }, { name: 'salary', label: 'المرتب', keepEmpty: true }, { name: 'address', label: 'العنوان', keepEmpty: true }, { name: 'gender', label: 'النوع', options: [['', '—'], ['male', 'ذكر'], ['female', 'أنثى']] }], e.sensitive || {},
          async d => { ['national_id', 'salary', 'address'].forEach(k => { d[k] = d[k] || ''; }); await App.put(`/api/employees/${id}/sensitive`, d); reload(); });
      } catch (err) { App.fail(err); }
    };
  };

  // ---------------- recruitment: 4 HR tabs
  const TABS = [['interviews', 'المقابلات', ['new', 'interviewing']], ['waiting', 'قائمة الانتظار', ['waiting']], ['onboarding', 'مقبول — تعيين', ['onboarding']], ['archived', 'أرشيف المرفوضين', ['archived']]];
  App.pages.recruitment = async (el, arg, q) => {
    const tab = TABS.find(t => t[0] === q.tab) || TABS[0];
    const all = await App.get('/api/journey/applications' + App.qs({ company_id: App.scopeCompany() }));
    const rows = all.filter(a => tab[2].includes(a.stage));
    const c = cid(); let formUrl = '';
    if (c) { try { formUrl = (await App.get('/api/journey/form-url?company_id=' + c)).url; } catch (e) { /* optional */ } }
    el.innerHTML = App.head('التوظيف والمقابلات', 'الطلبات بتوصل من Google Form (QR) أو تتسجل يدويًا', (App.can('employees:create') ? '<button class="btn primary" id="newApp">+ طلب يدوي</button>' : '') + '<button class="btn" id="qrB">📱 QR الاستمارة</button>' + (App.can('employees:print') ? `<a class="btn ghost" target="_blank" rel="noopener" href="/api/print/application-blank${App.qs({ company_id: c })}">🖨 استمارة تقديم فاضية</a>` : ''), 'journey') +
      `<div class="tabs" role="tablist">${TABS.map(t => `<button role="tab" class="${t === tab ? 'on' : ''}" onclick="location.hash='#/recruitment?tab=${t[0]}'">${t[1]} (${all.filter(a => t[2].includes(a.stage)).length})</button>`).join('')}</div>
      <div class="panel">${App.table(rows, [{ label: 'كود المرشح', render: a => `<span class="mono">${esc(a.candidate_id)}</span>` }, { label: 'الاسم', key: 'full_name' }, { label: 'الوظيفة', key: 'position' }, { label: 'التاريخ', render: a => fmtDate(a.application_date) }, { label: 'المصدر', render: a => a.source === 'google_form' ? 'Google Form' : 'يدوي' }, { label: 'المرحلة', render: a => App.badge(a.stage_ar, 'info') }], { href: a => '#/application/' + a.id, empty: 'لا توجد طلبات هنا' })}</div>`;
    const nb = el.querySelector('#newApp'); if (nb) nb.onclick = () => App.formModal('طلب توظيف يدوي', [{ name: 'company_id', label: 'الشركة', type: 'company', value: c, required: true }, { name: 'position', label: 'الوظيفة', required: true }, { name: 'full_name', label: 'الاسم', required: true }, { name: 'gender', label: 'النوع', options: [['', '—'], ['male', 'ذكر'], ['female', 'أنثى']], keepEmpty: true }, { name: 'phone', label: 'الهاتف', keepEmpty: true, ltr: true }, { name: 'email', label: 'الإيميل', keepEmpty: true, ltr: true }, { name: 'qualification', label: 'المؤهل', keepEmpty: true }, { name: 'military_status', label: 'الموقف التجنيدي', keepEmpty: true }, { name: 'expected_salary', label: 'الراتب المتوقع (HR)', keepEmpty: true }, { name: 'national_id', label: 'الرقم القومي (HR)', keepEmpty: true, ltr: true }],
      {}, async d => { Object.keys(d).forEach(k => { if (d[k] == null) d[k] = ''; }); const a = await App.post('/api/journey/applications', d); App.go('#/application/' + a.id); }, { cols: 2 });
    el.querySelector('#qrB').onclick = () => App.modal({ title: 'QR استمارة التقديم', body: `${formUrl ? `<div style="max-width:240px;margin:auto">${qrSvg(formUrl)}</div><p class="center small" dir="ltr">${esc(formUrl)}</p><p class="muted small">الاستمارة على Google وبتستقبل الطلبات حتى لو الجهاز المحلي مقفول؛ الطلبات بتظهر هنا لما الـ Worker يزامن.</p>` : '<div class="notice warn">لسه مفيش رابط استمارة. أنشئ الفورم بسكريبت Create_Application_Form.gs ثم حط الرابط العام هنا.</div>'}
        ${App.can('employees:edit') ? App.form([{ name: 'url', label: 'رابط الاستمارة العام (https://docs.google.com/forms/... أو forms.gle)', value: formUrl, ltr: true, full: true }], {}, 1) : ''}`,
      actions: App.can('employees:edit') ? [{ label: 'حفظ الرابط', primary: true, onClick: async m => { await App.put('/api/journey/form-url', { company_id: c, url: m.querySelector('[name=url]').value.trim() }); App.toast('اتحفظ'); App.render(); } }] : [] });
  };

  App.pages.application = async (el, id) => {
    const a = await App.get('/api/journey/applications/' + id), sens = App.can('employees:view_sensitive');
    const closed = ['onboarding', 'archived'].includes(a.stage);
    const canTech = App.can('employees:it_approve') || App.can('employees:edit'), canHR = App.can('employees:edit') && sens;
    el.innerHTML = App.head(`${a.full_name} — ${a.candidate_id}`, `${esc(a.position)} · ${App.badge(a.stage_ar, 'info')}`, (App.can('employees:print') ? `<a class="btn ghost" target="_blank" rel="noopener" href="/api/print/application/${id}">🖨 استمارة التقديم F01</a>` : '') +
      (!closed && canTech ? '<button class="btn" data-iv="technical">+ تقييم فني</button>' : '') + (!closed && canHR ? '<button class="btn" data-iv="hr">+ مقابلة HR</button>' : '') + (!closed && App.can('employees:approve') ? '<button class="btn primary" id="decB">⚖️ القرار النهائي (HR)</button>' : '') + (a.employee_id ? `<a class="btn" href="#/employee/${a.employee_id}">ملف الموظف</a>` : ''), 'journey') +
      `<div class="notice muted">المصفوفة المعتمدة: فني ✓ + HR ✓ ← تعيين · فني ✓ + HR ✗ ← انتظار · فني ✗ ← أرشيف (حتى لو HR ✓). القرار النهائي زر HR فقط.</div>
      ${sens ? `<div class="panel"><h2>بيانات الطلب (HR)</h2><dl class="kv">${[['الهاتف', a.phone], ['الإيميل', a.email], ['المؤهل', a.qualification], ['سنة التخرج', a.graduation_year], ['الموقف التجنيدي', a.military_status], ['الحالة الاجتماعية', a.marital_status], ['الراتب المتوقع', a.expected_salary], ['الرقم القومي', a.national_id], ['فترة الإخطار', a.notice_period], ['الخبرات', (a.experience || []).join(' / ')], ['الدورات', (a.courses || []).join(' / ')], ['المراجع', (a.references || []).join(' / ')]].map(([k, v]) => `<dt>${k}</dt><dd>${esc(v || '—')}</dd>`).join('')}</dl></div>` : '<div class="notice">البيانات الشخصية والمالية للمرشح مخفية عن صلاحيتك.</div>'}
      <div class="panel"><h2>المقابلات</h2>${App.table(a.interviews, [{ label: 'النوع', render: i => i.kind === 'hr' ? 'HR' : 'فنية' }, { label: 'المُقيّم', key: 'interviewer' }, { label: 'التوصية', render: i => App.badge(i.recommendation === 'accepted' ? 'Accepted' : 'Not Accepted', i.recommendation === 'accepted' ? 'good' : 'bad') }, { label: 'الدرجات / الملاحظات', render: i => i.scores ? Object.entries(i.scores).map(([k, v]) => `${k}: ${v}/10`).join(' · ') + (i.notes ? '<div class="small">' + esc(i.notes) + '</div>' : '') : (i.kind === 'hr' && !i.hr_fields ? '<span class="muted small">محتوى HR مخفي</span>' : Object.entries(i.hr_fields || {}).map(([k, v]) => `${k}: ${esc(v)}`).join(' · ')) }, { label: 'الوقت', render: i => fmtDT(i.created_at) }], { empty: 'لا توجد مقابلات' })}</div>
      <div class="panel"><h2>القرارات</h2>${App.table(a.decisions, [{ label: 'فني', key: 'technical_result' }, { label: 'HR', key: 'hr_result' }, { label: 'النتيجة', key: 'outcome' }, { label: 'الوقت', render: d => fmtDT(d.decided_at) }], { empty: 'لا يوجد قرار بعد' })}</div>`;
    const reload = () => App.pages.application(el, id);
    el.onclick = async ev => {
      const iv = ev.target.closest('[data-iv]');
      try {
        if (iv && iv.dataset.iv === 'technical') return App.formModal('التقييم الفني (من 10)', [{ name: 'interviewer', label: 'المُقيّم الفني', required: true }, { name: 'held_at', label: 'وقت المقابلة', type: 'datetime-local' }, { name: 'attended', label: 'حضر', type: 'checkbox', value: true }, ...['performance', 'behavior', 'team_dynamic', 'skills'].map(k => ({ name: k, label: { performance: 'Performance', behavior: 'Behavior', team_dynamic: 'Team Dynamic', skills: 'Skills' }[k], type: 'number' })), { name: 'notes', label: 'Notes', type: 'textarea', full: true, keepEmpty: true }, { name: 'recommendation', label: 'التوصية', options: [['accepted', 'Accepted'], ['not_accepted', 'Not Accepted']], required: true }], {},
          async d => { const scores = {}; ['performance', 'behavior', 'team_dynamic', 'skills'].forEach(k => { if (d[k] != null) scores[k] = d[k]; delete d[k]; }); await App.post(`/api/journey/applications/${id}/interviews`, Object.assign(d, { kind: 'technical', scores, notes: d.notes || '' })); reload(); });
        if (iv && iv.dataset.iv === 'hr') return App.formModal('مقابلة HR (سري)', [{ name: 'interviewer', label: 'مسؤول HR', required: true }, { name: 'held_at', label: 'وقت المقابلة', type: 'datetime-local' }, { name: 'attended', label: 'حضر', type: 'checkbox', value: true }, ...[['past_exp', 'Past EXP'], ['why', 'Why'], ['strength', 'Strength'], ['weakness', 'Weakness'], ['future_plans', 'Future plans']].map(([k, l]) => ({ name: k, label: l, keepEmpty: true })), { name: 'notes', label: 'Note', type: 'textarea', full: true, keepEmpty: true }, { name: 'recommendation', label: 'التوصية', options: [['accepted', 'Accepted'], ['not_accepted', 'Not Accepted']], required: true }], {},
          async d => { const hr = {}; ['past_exp', 'why', 'strength', 'weakness', 'future_plans'].forEach(k => { hr[k] = d[k] || ''; delete d[k]; }); await App.post(`/api/journey/applications/${id}/interviews`, Object.assign(d, { kind: 'hr', hr_fields: hr, notes: d.notes || '' })); reload(); });
        if (ev.target.id === 'decB') { const c = a.company_id; return App.formModal('القرار النهائي', [{ name: 'reason', label: 'السبب / ملاحظات', keepEmpty: true }, { name: 'start_date', label: 'تاريخ المباشرة (لو تعيين)', type: 'date' }, { name: 'department_id', label: 'القسم (لو تعيين)', type: 'department', company: c }, { name: 'job_title', label: 'المسمى الوظيفي', value: a.position }], {},
          async d => { d.reason = d.reason || ''; const r = await App.post(`/api/journey/applications/${id}/decision`, d); App.toast({ onboarding: 'اتقبل — اتفتح ملف تعيين ومهام IT', waiting: 'اتنقل لقائمة الانتظار', archived: 'اتنقل لأرشيف المرفوضين' }[r.outcome]); r.onboarding_id ? App.go('#/onboard/' + r.onboarding_id) : reload(); }, { intro: 'النتيجة بتتحسب من آخر تقييم فني وآخر مقابلة HR حسب المصفوفة — مش اختيار حر.' }); }
      } catch (err) { App.fail(err); }
    };
  };

  // ---------------- onboarding
  App.pages.onboarding = async el => {
    const rows = await App.get('/api/journey/onboarding');
    el.innerHTML = App.head('التعيينات', 'تجهيز IT (حجز ≠ تسليم) + المستندات الخمسة + الإقرارات بالإصدار', '', 'journey') + `<div class="panel">${App.table(rows, [{ label: 'الموظف', render: o => `<b>${esc(o.employee_name)}</b>` }, { label: 'الوظيفة', key: 'job_title' }, { label: 'المباشرة', render: o => fmtDate(o.start_date) }, { label: 'مهام IT', render: o => `${o.tasks_done}/${o.tasks_total} ${App.progress(o.tasks_total ? o.tasks_done / o.tasks_total * 100 : 0, 'used')}` }, { label: 'بنود ناقصة', render: o => o.missing_count ? App.badge(o.missing_count + ' ناقص', 'warn') : App.badge('جاهز للإقفال', 'good') }], { href: o => '#/onboard/' + o.id, empty: 'لا توجد تعيينات مفتوحة' })}</div>`;
  };

  App.pages.onboard = async (el, id) => {
    const o = await App.get('/api/journey/onboarding/' + id), m = await meta(), e = o.employee, itOk = App.can('employees:it_approve');
    const TS = { pending: ['مفتوحة', 'muted'], done: ['نُفذت — تنتظر اعتماد IT', 'warn'], verified: ['معتمدة', 'good'], skipped: ['متخطاة بسبب', 'info'] };
    el.innerHTML = App.head('تعيين: ' + e.full_name, `مباشرة ${fmtDate(o.start_date)} · ${App.badge(o.status === 'open' ? 'مفتوح' : 'مكتمل', o.status === 'open' ? 'info' : 'good')}`,
      `<a class="btn" href="#/employee/${e.id}">ملف الموظف</a><a class="btn ghost" target="_blank" rel="noopener" href="/api/print/custody/${e.id}?kind=onboarding">🖨 بيان تسليم العهدة</a>` + (itOk && o.status === 'open' ? '<button class="btn" id="taskB">+ مهمة IT</button>' : '') + (App.can('employees:approve') && o.status === 'open' ? '<button class="btn primary" id="doneB">✓ إقفال التعيين</button>' : ''), 'journey') +
      (o.missing.length ? `<div class="notice warn"><b>بنود ناقصة قبل الإقفال:</b><ul style="margin:4px 0 0">${o.missing.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>` : '<div class="notice">كل البنود مكتملة.</div>') +
      `<div class="panel"><h2>مهام تجهيز IT</h2><p class="muted small">التجهيز (حجز جهاز) مختلف عن التسليم الفعلي — التسليم من صفحة الأصل. كلمات المرور ممنوعة؛ اكتب مرجع فقط.</p>${App.table(o.tasks, [{ label: 'المهمة', render: t => esc(t.kind_ar) + (t.description && t.description !== t.kind_ar ? ' — ' + esc(t.description) : '') }, { label: 'المرجع / الدليل', render: t => `<span class="mono">${esc(t.ref)}</span>${t.evidence_file_id ? ` <a class="small" target="_blank" rel="noopener" href="/api/files/${t.evidence_file_id}">📎 الدليل</a>` : (t.status === 'done' ? ' <span class="small t-bad">بدون دليل</span>' : '')}` }, { label: 'الحالة', render: t => App.badge(TS[t.status][0], TS[t.status][1]) + (t.notes ? `<div class="small muted">${esc(t.notes)}</div>` : '') }, { label: '', render: t => itOk && o.status === 'open' ? (t.status === 'pending' ? `<button class="link" data-t="${t.id}" data-s="done">نُفذت</button><button class="link" data-t="${t.id}" data-s="skipped">تخطي</button>` : t.status === 'done' ? `<button class="link" data-t="${t.id}" data-s="verified">اعتماد IT</button>` : '') : '' }])}</div>
      <div class="blocks"><div class="panel"><h2>المستندات الخمسة</h2>${App.table(o.joining_documents, [{ label: 'المستند', key: 'label' }, { label: 'الحالة', render: d => App.badge(DOC[d.status][0], DOC[d.status][1]) }])}<a class="small" href="#/employee/${e.id}">الرفع والتحقق من ملف الموظف (HR)</a></div>
      <div class="panel"><h2>الإقرارات المطلوبة</h2><ul>${Object.entries(m.ack_kinds).map(([k, l]) => `<li>${o.acks.includes(k) ? '✅' : '⬜'} ${esc(l)}</li>`).join('')}</ul><h3>ملفات الشركة الفعّالة</h3>${App.table(o.templates, [{ label: 'النوع', render: t => esc(m.template_kinds[t.kind] || t.kind) }, { label: 'العنوان', key: 'title' }, { label: 'الإصدار', render: t => 'v' + t.version }, { label: '', render: t => `<a class="link" target="_blank" rel="noopener" href="/api/files/${t.file_id}">معاينة</a>` }], { empty: 'لم تُرفع ملفات الشركة بعد — من «المستندات والإقرارات»' })}</div></div>
      <div class="panel"><h2>العهدة المسلّمة فعليًا</h2>${App.table(o.custody.assets, [{ label: 'الأصل', render: a => `<a href="#/asset/${a.id}" class="mono">${esc(a.code)}</a>` }, { label: 'الموديل', key: 'model' }], { empty: 'لم يُسلم شيء بعد' })}</div>`;
    const reload = () => App.pages.onboard(el, id);
    el.onclick = async ev => {
      const t = ev.target.closest('[data-t]');
      try {
        if (t) { const s = t.dataset.s; if (s === 'verified') { await App.post('/api/journey/tasks/' + t.dataset.t, { status: s, notes: '' }); return reload(); }
          return App.formModal(s === 'done' ? 'تسجيل تنفيذ المهمة' : 'تخطي المهمة', [{ name: 'ref', label: 'المرجع (كود أصل / إيميل / رقم — بدون كلمات مرور)', keepEmpty: true }, { name: 'notes', label: s === 'skipped' ? 'سبب التخطي' : 'ملاحظات', required: s === 'skipped', keepEmpty: true }, ...(s === 'done' ? [{ name: 'evidence_file_id', label: 'دليل التنفيذ (صورة / ملف) — مطلوب قبل الاعتماد', type: 'file', category: 'custody_evidence', accept: '.png,.jpg,.jpeg,.pdf' }] : [])], { company_id: o.company_id },
            async d => { await App.post('/api/journey/tasks/' + t.dataset.t, { status: s, ref: d.ref || '', notes: d.notes || '', evidence_file_id: d.evidence_file_id || null }); reload(); }, { intro: s === 'done' ? 'الخطوات: تنفيذ ← دليل ← اعتماد IT. من غير دليل مش هينفع الاعتماد.' : '' }); }
        if (ev.target.id === 'taskB') return App.formModal('مهمة IT', [{ name: 'kind', label: 'النوع', options: Object.entries(m.it_task_kinds), required: true }, { name: 'description', label: 'الوصف', keepEmpty: true }, { name: 'due_date', label: 'الاستحقاق', type: 'date' }], {}, async d => { d.description = d.description || ''; await App.post(`/api/journey/onboarding/${id}/tasks`, d); reload(); });
        if (ev.target.id === 'doneB') { await App.post(`/api/journey/onboarding/${id}/complete`); App.toast('اتقفل التعيين — الموظف نشط'); reload(); }
      } catch (err) { App.fail(err); }
    };
  };

  // ---------------- offboarding
  App.pages.offboarding = async el => {
    const rows = await App.get('/api/journey/offboarding?status=open');
    const ST = { it_review: ['مراجعة IT', 'warn'], ready_for_paper: ['جاهز للتوقيعات الورقية', 'info'], closed: ['مقفول', 'good'] };
    el.innerHTML = App.head('إخلاء الطرف', 'فتح الإخلاء = تجميد فوري لأي عهدة جديدة · IT يراجع ويعتمد · HR يطبع بدون توقيع مسبق · 6 جهات توقع ورقيًا', '', 'journey') +
      `<div class="panel">${App.table(rows, [{ label: 'الموظف', render: o => `<b>${esc(o.employee_name)}</b>` }, { label: 'آخر يوم', render: o => fmtDate(o.last_day) + ` <span class="small ${o.days_to_last_day <= 1 ? 't-bad' : ''}">(${o.days_to_last_day} يوم)</span>` }, { label: 'المرحلة', render: o => App.badge(ST[o.status][0], ST[o.status][1]) }, { label: 'عهدة متبقية', render: o => { const n = o.custody.assets.length + o.custody.sims.length; return n ? App.badge(n + ' عنصر', 'bad') : App.badge('لا يوجد', 'good'); } }, { label: 'التوقيعات', render: o => o.authorities.filter(a => a.status === 'clear').length + '/' + o.authorities.length }], { href: o => '#/offboard/' + o.id, empty: 'لا توجد إخلاءات مفتوحة — افتح الإخلاء من ملف الموظف (HR)' })}</div>`;
  };

  App.pages.offboard = async (el, id) => {
    const o = await App.get('/api/journey/offboarding/' + id), itOk = App.can('employees:it_approve'), hr = App.can('employees:approve');
    const step = ['it_review', 'ready_for_paper', 'closed'].indexOf(o.status);
    const AS = { pending: ['لم يوقّع', 'muted'], clear: ['Clear', 'good'], not_clear: ['Not Clear', 'bad'] };
    el.innerHTML = App.head('إخلاء طرف: ' + o.employee_name, `آخر يوم ${fmtDate(o.last_day)} · ${esc(o.reason)}`, `<a class="btn" href="#/employee/${o.employee_id}">ملف الموظف</a><a class="btn ghost" target="_blank" rel="noopener" href="/api/print/custody/${o.employee_id}?kind=offboarding">🖨 بيان استرجاع العهدة</a>`, 'journey') +
      `<div class="steps">${['1. مراجعة IT واعتماده', '2. طباعة وتوقيعات ورقية', '3. رفع النسخة الموقعة والإقفال'].map((s, i) => `<span class="${i < step ? 'done' : i === step ? 'on' : ''}">${s}</span>`).join('')}</div>
      <div class="notice warn">العهدة مجمّدة: أي تسليم جديد للموظف مرفوض من الواجهة والشيت والـ API.</div>
      <div class="panel"><div class="panel-head"><h2>العهدة الحالية (مراجعة عكسية)</h2>${itOk && o.status === 'it_review' ? '<span><button class="btn sm" id="accB">+ مراجعة حساب/وصول</button> <button class="btn sm primary" id="itApB">✓ اعتماد IT</button></span>' : ''}</div>
        ${App.table([].concat(o.custody.assets.map(a => ({ t: 'asset', id: a.id, code: a.code, d: a.model + ' · ' + a.serial })), o.custody.sims.map(s => ({ t: 'sim', id: s.id, code: s.code, d: s.number })), (o.mailboxes || []).filter(mb => mb.status === 'active').map(mb => ({ t: 'mailbox', id: mb.id, code: mb.code, d: mb.address }))), [{ label: 'النوع', render: x => ({ asset: 'أصل', sim: 'شريحة', mailbox: '✉️ إيميل' })[x.t] }, { label: 'الكود', render: x => `<span class="mono">${esc(x.code)}</span>` }, { label: 'التفاصيل', render: x => `<span ${x.t === 'mailbox' ? 'dir="ltr"' : ''}>${esc(x.d)}</span>` }, { label: '', render: x => itOk && o.status === 'it_review' ? (x.t === 'mailbox' ? `<button class="link" data-mbx="${x.id}">إيقاف الإيميل</button> · <a class="link" href="#/mailbox/${x.id}">نقل لموظف</a>` : `<button class="link" data-chk="${x.t}" data-id="${x.id}">تسجيل الاسترجاع</button>`) : '' }], { empty: 'لا توجد عهدة ولا إيميلات نشطة متبقية ✓' })}
        <h3 style="margin-top:12px">سجل مراجعة IT</h3>${App.table(o.checks, [{ label: 'النوع', key: 'item_type' }, { label: 'المرجع', key: 'item_ref' }, { label: 'النتيجة', key: 'result' }, { label: 'تطابق السيريال', render: c => c.serial_checked ? '✓' : '—' }, { label: 'ملاحظات', key: 'notes' }, { label: 'الوقت', render: c => fmtDT(c.at) }], { empty: 'لم تبدأ المراجعة' })}</div>
      <div class="panel"><div class="panel-head"><h2>الجهات الست (توقيع ورقي)</h2>${o.status !== 'it_review' && App.can('employees:print') ? `<a class="btn" target="_blank" rel="noopener" href="/api/journey/offboarding/${id}/clearance-print">🖨 طباعة نموذج الإخلاء</a>` : ''}</div>
        ${App.table(o.authorities, [{ label: '#', key: 'order' }, { label: 'الجهة', key: 'authority' }, { label: 'الحالة', render: a => App.badge(AS[a.status][0], AS[a.status][1]) }, { label: 'الموقّع', key: 'signer_name' }, { label: 'التاريخ', render: a => fmtDate(a.signed_date) }, { label: 'ملاحظات', key: 'notes' }, { label: '', render: a => hr && o.status === 'ready_for_paper' ? `<button class="link" data-auth="${a.id}">تسجيل</button>` : '' }])}
        <p class="muted small">«Not Clear» يمنع الإقفال النهائي — مسار الاستثناء غير مفعّل لأنه غير معتمد كسياسة.</p></div>
      <div class="panel"><h2>النسخة الموقعة والإقفال</h2>${o.signed_scan_file_id ? `<p>✅ النسخة الموقعة مرفوعة — <a target="_blank" rel="noopener" href="/api/files/${o.signed_scan_file_id}">عرض</a></p>` : '<p class="muted">لم تُرفع بعد</p>'}
        ${hr && o.status === 'ready_for_paper' ? '<button class="btn" id="scanB">⬆ رفع النسخة الموقعة</button> <button class="btn primary" id="closeB">إقفال نهائي وأرشفة</button>' : ''}${o.status === 'closed' ? App.badge('مقفول ' + fmtDT(o.closed_at), 'good') : ''}</div>`;
    const reload = () => App.pages.offboard(el, id);
    el.onclick = async ev => {
      const ck = ev.target.closest('[data-chk]'), au = ev.target.closest('[data-auth]'), mbx = ev.target.closest('[data-mbx]');
      try {
        if (mbx) return App.formModal('إيقاف إيميل الموظف', [{ name: 'notes', label: 'ملاحظات (مثلاً: اتعمل تحويل للمدير)', keepEmpty: true }], {}, async d => { await App.post(`/api/journey/offboarding/${id}/it-check`, { item_type: 'mailbox', item_ref: mbx.dataset.mbx, result: 'deprovisioned', notes: d.notes || '' }); App.toast('الإيميل اتوقف واتسجل في مراجعة IT'); reload(); }, { intro: 'الإيميل هيتحول لحالة «موقوف» في سجل الإيميلات. لو عايز تنقله لموظف تاني بدل الإيقاف، استخدم «نقل لموظف».' });
        if (ck) return App.formModal('تسجيل الاسترجاع', [{ name: 'result', label: 'النتيجة', options: [['returned', 'استُرجع سليم'], ['damaged', 'استُرجع تالف'], ['missing', 'مفقود']], required: true }, { name: 'serial_checked', label: 'تمت مطابقة السيريال / IMEI', type: 'checkbox' }, { name: 'notes', label: 'ملاحظات', keepEmpty: true }], {}, async d => { await App.post(`/api/journey/offboarding/${id}/it-check`, Object.assign(d, { item_type: ck.dataset.chk, item_ref: ck.dataset.id, notes: d.notes || '' })); reload(); });
        if (au) return App.formModal('تسجيل توقيع الجهة (من الورقة)', [{ name: 'status', label: 'الحالة', options: [['clear', 'Clear'], ['not_clear', 'Not Clear'], ['pending', 'لم يوقّع']], required: true }, { name: 'signer_name', label: 'اسم الموقّع', keepEmpty: true }, { name: 'signed_date', label: 'تاريخ التوقيع', type: 'date' }, { name: 'notes', label: 'ملاحظات / السبب', keepEmpty: true }], {}, async d => { d.signer_name = d.signer_name || ''; d.notes = d.notes || ''; await App.post(`/api/journey/offboarding/${id}/authorities/${au.dataset.auth}`, d); reload(); });
        if (ev.target.id === 'accB') return App.formModal('مراجعة حساب / وصول', [{ name: 'item_ref', label: 'الحساب (إيميل، VPN، نظام...)', required: true }, { name: 'result', label: 'النتيجة', options: [['deprovisioned', 'اتلغى فعليًا'], ['pending_verification', 'بانتظار تحقق (تكامل غير متاح)'], ['not_applicable', 'لا ينطبق']], required: true }, { name: 'notes', label: 'ملاحظات', keepEmpty: true }], {}, async d => { await App.post(`/api/journey/offboarding/${id}/it-check`, Object.assign(d, { item_type: 'account', notes: d.notes || '' })); reload(); });
        if (ev.target.id === 'itApB') { await App.post(`/api/journey/offboarding/${id}/it-approve`); App.toast('IT اعتمد — HR يقدر يطبع النموذج'); return reload(); }
        if (ev.target.id === 'scanB') return App.formModal('رفع نموذج الإخلاء الموقع', [{ name: 'file_id', label: 'الملف (PDF/صورة)', type: 'file', category: 'clearance_scan', required: true }], { company_id: o.company_id }, async d => { await App.post(`/api/journey/offboarding/${id}/signed-scan`, d); reload(); });
        if (ev.target.id === 'closeB') { if (!await App.confirm('إقفال نهائي', 'هيتقفل الإخلاء ويتأرشف الموظف. متأكد؟')) return; await App.post(`/api/journey/offboarding/${id}/close`); App.toast('اتقفل الإخلاء'); reload(); }
      } catch (err) { App.fail(err); }
    };
  };

  // ---------------- documents (company templates, versioned)
  App.pages.documents = async el => {
    const m = await meta(), rows = await App.get('/api/journey/templates' + App.qs({ company_id: App.scopeCompany() }));
    el.innerHTML = App.head('المستندات والإقرارات', 'ملفات كل شركة (NDA، اللائحة، العقد، إقرار المباشرة، بيان عهدة الحاسب الآلي، العهدة الأخرى، الإخلاء) — كل تحديث إصدار جديد ولا يغيّر الإقرارات السابقة', App.can('employees:edit') ? '<button class="btn primary" id="upT">⬆ رفع / تحديث إصدار</button>' : '', 'journey') +
      `<div class="notice muted">ممنوع استخدام شعارات النماذج المرجعية. الملفات القانونية تُرفع لكل شركة بعد اعتمادها.</div><div class="panel">${App.table(rows, [{ label: 'الشركة', render: t => esc(App.companyName(t.company_id)) }, { label: 'النوع', render: t => esc(t.kind_ar) }, { label: 'العنوان', key: 'title' }, { label: 'الإصدار', render: t => 'v' + t.version }, { label: 'الحالة', render: t => t.active ? App.badge('فعّال', 'good') : App.badge('غير فعّال', 'muted') }, { label: 'البصمة', render: t => `<span class="mono small" dir="ltr">${esc(t.sha256.slice(0, 12))}…</span>` }, { label: '', render: t => `<a class="link" target="_blank" rel="noopener" href="/api/files/${t.file_id}">معاينة</a>` + (App.can('employees:edit') ? `<button class="link" data-act="${t.id}" data-on="${t.active ? 0 : 1}">${t.active ? 'تعطيل' : 'تفعيل'}</button>` : '') }], { empty: 'لم تُرفع ملفات بعد' })}</div>`;
    el.onclick = async ev => {
      const b = ev.target.closest('[data-act]');
      try {
        if (b) { await App.post(`/api/journey/templates/${b.dataset.act}/active?active=${b.dataset.on === '1'}`); return App.pages.documents(el); }
        if (ev.target.id === 'upT') { const c = cid(); return App.formModal('رفع ملف شركة', [{ name: 'company_id', label: 'الشركة', type: 'company', value: c, required: true }, { name: 'kind', label: 'النوع', options: Object.entries(m.template_kinds), required: true }, { name: 'title', label: 'العنوان', required: true }, { name: 'file_id', label: 'الملف', type: 'file', category: 'document_template', required: true }], { company_id: c }, async d => { await App.post('/api/journey/templates', d); App.pages.documents(el); }); }
      } catch (err) { App.fail(err); }
    };
  };
})(window.App);
