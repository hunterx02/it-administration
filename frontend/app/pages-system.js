/* M06 — System administration: users/RBAC, companies, integration center, scripts, logs, backups, theme, migration. */
(function (App) {
  'use strict';
  const { esc, fmtDate, fmtDT } = App;
  const ROLE_KIND = { super_admin: 'bad', it_manager: 'info', hr: 'good', finance: 'warn' };

  // ---------------- users
  App.pages.users = async el => {
    const [users, roles] = await Promise.all([App.get('/api/users'), App.get('/api/roles')]);
    const scopeTxt = u => u.all_companies ? 'كل الشركات' : (u.scopes.map(s => App.companyName(s.company_id) + (s.department_id ? ' / ' + App.deptName(s.department_id) : '')).join('، ') || App.badge('بدون نطاق', 'warn'));
    el.innerHTML = App.head('المستخدمون', 'حساب لكل شخص — الصلاحيات تتطبق على السيرفر، وإيقاف الحساب يلغي جلساته فورًا', '<button class="btn primary" id="newU">+ مستخدم</button>') +
      `<div class="panel">${App.table(users, [{ label: 'الاسم', render: u => `<b>${esc(u.name)}</b>${u.id === App.me.id ? ' ' + App.badge('أنت', 'info') : ''}` }, { label: 'البريد', render: u => `<span dir="ltr">${esc(u.email)}</span>` }, { label: 'الدور', render: u => App.badge(u.role.name_ar, ROLE_KIND[u.role.key] || 'muted') }, { label: 'النطاق', render: scopeTxt }, { label: 'الحالة', render: u => u.active ? App.badge('نشط', 'good') : App.badge('موقوف', 'muted') }, { label: '', render: u => `<button class="link" data-ed="${u.id}">تعديل</button><button class="link" data-pw="${u.id}">كلمة مرور</button>` }])}</div>`;
    const roleOpts = roles.filter(r => r.key !== 'super_admin' || App.me.role.key === 'super_admin').map(r => [r.key, r.name_ar]);
    const scopeFields = [{ name: 'all_companies', label: 'كل الشركات', type: 'checkbox' }, { name: 'scope_company', label: 'أو شركة محددة', options: [['', '—']].concat(App.companies.map(c => [c.id, c.name])) }];
    const scopes = d => { const s = d.scope_company ? [{ company_id: d.scope_company }] : []; delete d.scope_company; d.scopes = s; return d; };
    el.onclick = async ev => {
      const ed = ev.target.closest('[data-ed]'), pw = ev.target.closest('[data-pw]');
      try {
        if (ev.target.id === 'newU') return App.formModal('مستخدم جديد', [{ name: 'name', label: 'الاسم', required: true }, { name: 'email', label: 'البريد', required: true, ltr: true }, { name: 'password', label: 'كلمة مرور مبدئية (10+ حروف وأرقام)', type: 'password', required: true }, { name: 'role_key', label: 'الدور', options: roleOpts, required: true }, ...scopeFields], {},
          async d => { await App.post('/api/users', scopes(d)); App.pages.users(el); }, { intro: 'IT Staff وIT Manager مش بيشوفوا البيانات الحساسة للموظفين (الرقم القومي، المرتب، تقييم HR).' });
        if (ed) { const u = users.find(x => x.id === ed.dataset.ed); return App.formModal('تعديل ' + u.name, [{ name: 'name', label: 'الاسم', required: true }, { name: 'role_key', label: 'الدور', options: roleOpts }, { name: 'active', label: 'نشط', type: 'checkbox' }, ...scopeFields], { name: u.name, role_key: u.role.key, active: u.active, all_companies: u.all_companies, scope_company: (u.scopes[0] || {}).company_id },
          async d => { await App.patch('/api/users/' + u.id, scopes(d)); App.pages.users(el); }, { intro: 'تغيير الدور بيقفل جلسات المستخدم عشان يدخل بالصلاحيات الجديدة.' }); }
        if (pw) return App.formModal('تعيين كلمة مرور جديدة', [{ name: 'new_password', label: 'كلمة المرور', type: 'password', required: true }], {}, async d => { await App.post(`/api/users/${pw.dataset.pw}/reset-password`, d); App.toast('اتغيرت — وجلساته اتقفلت'); });
      } catch (err) { App.fail(err); }
    };
  };

  // ---------------- roles matrix (read-only view of server RBAC)
  App.pages.roles = async el => {
    const [roles, cat] = await Promise.all([App.get('/api/roles'), App.get('/api/permissions')]);
    const AR = { view: 'عرض', create: 'إضافة', edit: 'تعديل', delete: 'حذف', approve: 'اعتماد', it_approve: 'اعتماد IT', print: 'طباعة', export: 'تصدير', view_sensitive: 'بيانات حساسة', manage_users: 'المستخدمين', manage_integration: 'الربط', manage_secrets: 'الأسرار/اعتماد السكريبتات' };
    el.innerHTML = App.head('الأدوار والصلاحيات', 'الحماية على السيرفر — إخفاء زر في الواجهة مش هو الحماية') +
      Object.entries(cat.actions).map(([mod, acts]) => `<div class="panel"><h2>${esc(cat.modules[mod])}</h2><div style="overflow-x:auto"><table class="tbl compact"><thead><tr><th>الدور</th>${acts.map(a => `<th class="c">${esc(AR[a] || a)}</th>`).join('')}</tr></thead><tbody>${roles.map(r => `<tr><td>${esc(r.name_ar)}</td>${acts.map(a => `<td class="c">${r.permissions.includes(mod + ':' + a) ? '✅' : '—'}</td>`).join('')}</tr>`).join('')}</tbody></table></div></div>`).join('') +
      '<p class="muted small">التعديل التفصيلي للأدوار (Custom) هيتعمل لما السياسة تتعتمد — الأدوار الحالية هي الافتراضيات المعتمدة في المواصفات.</p>';
  };

  // ---------------- companies
  App.pages.companies = async el => {
    await App.loadCompanies(); const ed = App.can('system:edit');
    el.innerHTML = App.head('الشركات والفروع والأقسام', 'أسماء حقيقية بيدخلها صاحب النظام — مفيش بيانات مفترضة. بادئة الأكواد بتتقفل بعد أول أصل.', ed ? '<button class="btn primary" id="newC">+ شركة</button>' : '') +
      (App.companies.length ? App.companies.map(c => `<div class="panel"><div class="panel-head"><h2>${esc(c.name)} ${c.asset_prefix ? `<span class="mono badge b-info">${esc(c.asset_prefix)}</span>` : App.badge('بدون بادئة أكواد', 'warn')}</h2>${ed ? `<span><button class="btn sm" data-ec="${c.id}">تعديل</button> <button class="btn sm" data-br="${c.id}">+ فرع</button> <button class="btn sm" data-dp="${c.id}">+ قسم</button></span>` : ''}</div>
        <p><b>الفروع:</b> ${c.branches.map(b => esc(b.name)).join('، ') || '<span class="muted">—</span>'}</p><p><b>الأقسام:</b> ${c.departments.map(d => esc(d.name)).join('، ') || '<span class="muted">—</span>'}</p></div>`).join('') : App.emptyBox('مفيش شركات — ابدأ بإضافة شركتك'));
    const reload = () => App.pages.companies(el);
    el.onclick = async ev => {
      const ec = ev.target.closest('[data-ec]'), br = ev.target.closest('[data-br]'), dp = ev.target.closest('[data-dp]');
      try {
        if (ev.target.id === 'newC') return App.formModal('شركة جديدة', [{ name: 'name', label: 'الاسم', required: true }, { name: 'asset_prefix', label: 'بادئة أكواد الأصول (حروف/أرقام)', hint: 'مثال توضيحي فقط: ZWolf ← ZWolf-AIO-001', keepEmpty: true, ltr: true }], {}, async d => { d.asset_prefix = d.asset_prefix || ''; await App.post('/api/companies', d); reload(); });
        if (ec) { const c = App.companies.find(x => x.id === ec.dataset.ec); return App.formModal('تعديل ' + c.name, [{ name: 'name', label: 'الاسم', required: true }, { name: 'asset_prefix', label: 'بادئة الأكواد', keepEmpty: true, ltr: true }], c, async d => { d.asset_prefix = d.asset_prefix || ''; await App.patch('/api/companies/' + c.id, d); reload(); }); }
        if (br || dp) { const id = (br || dp).dataset[br ? 'br' : 'dp']; return App.formModal(br ? 'فرع جديد' : 'قسم جديد', [{ name: 'name', label: 'الاسم', required: true }], {}, async d => { await App.post(`/api/companies/${id}/${br ? 'branches' : 'departments'}`, d); reload(); }); }
      } catch (err) { App.fail(err); }
    };
  };

  // ---------------- integration center + scripts + conflicts
  const ST = { not_configured: 'muted', pending_live_credentials: 'warn', disabled: 'muted', healthy: 'good', stale: 'warn', error: 'bad' };
  App.pages.integrations = async (el, arg, q) => {
    const tab = q.tab || 'connectors', mi = App.can('system:manage_integration');
    const tabs = `<div class="tabs">${[['connectors', 'الموصلات'], ['conflicts', 'تعارضات المزامنة'], ['scripts', 'مكتبة السكريبتات'], ['worker', 'الـ Worker']].map(([k, l]) => `<button class="${tab === k ? 'on' : ''}" onclick="location.hash='#/integrations?tab=${k}'">${l}</button>`).join('')}</div>`;
    const head = App.head('الربط والأتمتة', 'مفيش موصل بيظهر «متصل» قبل ما يتجرب فعليًا بصلاحيات الشركة') + tabs;
    if (tab === 'connectors') {
      const s = await App.get('/api/integrations/status');
      el.innerHTML = head + `<div class="notice ${s.connected_live ? '' : 'warn'}">متصل ومُتحقق منه: <b>${s.connected_live}</b> من ${s.cards.length}. الصلاحيات (Service Account JSON) بتتحط في <span class="mono" dir="ltr">backend/data/secrets/&lt;credential_ref&gt;.json</span> — خارج الكود والشيتات.</div>
        <div class="panel">${App.table(s.cards, [{ label: 'الوحدة', key: 'module' }, { label: 'الموصل', render: c => `<b>${esc(c.title_ar)}</b><div class="small muted">${esc(c.provider)} · ${esc(c.mode)}</div>` }, { label: 'المصدر (ID)', render: c => `<span class="mono small" dir="ltr">${esc(c.source_ref || '—')}</span>` }, { label: 'مرجع الصلاحية', render: c => `<span class="mono small" dir="ltr">${esc(c.credential_ref || '—')}</span>` }, { label: 'الحالة', render: c => App.badge(c.status_ar, ST[c.status]) + (c.last_error ? `<div class="small t-bad">${esc(c.last_error.slice(0, 120))}</div>` : '') }, { label: 'آخر مزامنة', render: c => fmtDT(c.last_sync_at) }, { label: '', render: c => mi ? `<button class="link" data-cfg="${c.key}">إعداد</button>${c.enabled && c.source_ref ? `<button class="link" data-sync="${c.key}">مزامنة الآن</button>` : ''}` : '' }])}</div>`;
      el.onclick = async ev => {
        const cfg = ev.target.closest('[data-cfg]'), sy = ev.target.closest('[data-sync]');
        try {
          if (cfg) { const c = s.cards.find(x => x.key === cfg.dataset.cfg); return App.formModal('إعداد: ' + c.title_ar, [{ name: 'enabled', label: 'مفعّل', type: 'checkbox' }, { name: 'mode', label: 'الوضع', options: [['manual', 'يدوي'], ['sheets', 'Google Sheets'], ['api', 'API'], ['script', 'سكريبت']] }, { name: 'source_ref', label: 'Spreadsheet / Folder / Form ID', keepEmpty: true, ltr: true }, { name: 'credential_ref', label: 'اسم ملف الصلاحية (بدون .json)', keepEmpty: true, ltr: true, hint: 'اسم مرجعي فقط — ممنوع لصق المفتاح نفسه' }], c,
            async d => { d.source_ref = d.source_ref || ''; d.credential_ref = d.credential_ref || ''; await App.put('/api/integrations/' + c.key, d); App.pages.integrations(el, '', q); }); }
          if (sy) { const r = await App.post(`/api/system/integrations/${sy.dataset.sync}/sync`); App.toast(`النتيجة: ${r.status}${r.error ? ' — ' + r.error : ''}${r.pulled != null ? ` · سحب ${r.pulled} · دفع ${r.pushed} · إضافة ${r.appended} · تعارضات ${r.conflicts}` : ''}`, r.status === 'healthy' ? 'ok' : 'warn'); App.pages.integrations(el, '', q); }
        } catch (err) { App.fail(err); }
      };
    } else if (tab === 'conflicts') {
      const rows = await App.get('/api/system/sync/conflicts');
      const K = { concurrent_edit: 'تعديل متزامن', protected_field: 'عمود محمي', invalid_row: 'صف غير صالح', unknown_record: 'كود غير موجود' };
      el.innerHTML = head + `<div class="notice muted">التعارضات مش بتتحل تلقائيًا (لا last-write-wins). «إبقاء قيمة النظام» بيرجع قيمة النظام للشيت في المزامنة الجاية؛ لو قيمة الشيت هي الصح، عدّلها من شاشة السجل في النظام.</div><div class="panel">${App.table(rows, [{ label: 'الموصل', key: 'connector' }, { label: 'السجل', render: c => `<span class="mono">${esc(c.record_key)}</span>` }, { label: 'النوع', render: c => App.badge(K[c.kind] || c.kind, 'warn') }, { label: 'الرسالة', key: 'message' }, { label: 'قيم الشيت', render: c => `<pre class="out">${esc(JSON.stringify(c.sheet_values, null, 1))}</pre>` }, { label: '', render: c => mi ? `<button class="link" data-res="${c.id}" data-r="keep_app">إبقاء قيمة النظام</button><button class="link" data-res="${c.id}" data-r="dismissed">تجاهل</button>` : '' }], { empty: 'لا توجد تعارضات مفتوحة' })}</div>`;
      el.onclick = async ev => { const b = ev.target.closest('[data-res]'); if (!b) return; try { await App.post(`/api/system/sync/conflicts/${b.dataset.res}/resolve`, { resolution: b.dataset.r }); App.pages.integrations(el, '', q); } catch (err) { App.fail(err); } };
    } else if (tab === 'scripts') {
      const rows = await App.get('/api/system/scripts'), ms = App.can('system:manage_secrets');
      const SS = { uploaded: ['مرفوع — غير معتمد', 'warn'], active: ['معتمد وفعّال', 'good'], superseded: ['إصدار سابق', 'muted'], disabled: ['معطّل', 'muted'] };
      el.innerHTML = head + `<div class="notice warn">الرفع ≠ التشغيل. السكريبت بيشتغل بس بعد اعتماد من مستخدم عنده صلاحية الأسرار، وبصمته بتتراجع قبل كل تشغيل، بمهلة زمنية وبدون أسرار. ملحوظة: عزل الشبكة على مستوى نظام التشغيل غير مطبق على جهاز Windows المحلي — هيتطبق على VPS/Container.</div>
        ${mi ? '<div class="actions" style="margin-bottom:12px"><button class="btn primary" id="upS">⬆ رفع سكريبت / إصدار جديد</button></div>' : ''}
        <div class="panel">${App.table(rows, [{ label: 'الاسم', render: s => `<b>${esc(s.name)}</b> v${s.version}<div class="small muted">${esc(s.description)}</div>` }, { label: 'الوحدة', key: 'module' }, { label: 'المشغّل', key: 'runner' }, { label: 'الجدول', render: s => s.schedule_minutes ? 'كل ' + s.schedule_minutes + ' دقيقة' : 'يدوي' }, { label: 'البصمة', render: s => `<span class="mono small" dir="ltr">${esc(s.sha256.slice(0, 12))}…</span>` }, { label: 'الحالة', render: s => App.badge(SS[s.status][0], SS[s.status][1]) + (s.last_status ? ` <span class="small">آخر تشغيل: ${esc(s.last_status)} ${fmtDT(s.last_run_at)}</span>` : '') },
          { label: '', render: s => [ms && ['uploaded', 'superseded', 'disabled'].includes(s.status) ? `<button class="link" data-sa="${s.id}" data-k="approve">${s.status === 'superseded' ? 'استرجاع (Rollback)' : 'اعتماد'}</button>` : '', mi && s.status === 'active' ? `<button class="link" data-sa="${s.id}" data-k="run">تشغيل الآن</button><button class="link danger" data-sa="${s.id}" data-k="disable">تعطيل</button>` : '', `<button class="link" data-runs="${s.id}">السجل</button>`].join('') }], { empty: 'لا توجد سكريبتات. سكريبتات WE وCode.gs القديمة مرجع فقط وليست جاهزة للإنتاج.' })}</div>`;
      el.onclick = async ev => {
        const b = ev.target.closest('[data-sa]'), rn = ev.target.closest('[data-runs]');
        try {
          if (b) { const r = await App.post(`/api/system/scripts/${b.dataset.sa}/${b.dataset.k}`); if (r.note) App.toast(r.note); return App.pages.integrations(el, '', q); }
          if (rn) { const runs = await App.get(`/api/system/scripts/${rn.dataset.runs}/runs`); return App.modal({ title: 'سجل التشغيل', wide: true, body: App.table(runs, [{ label: 'البداية', render: r => fmtDT(r.started_at) }, { label: 'الحالة', key: 'status' }, { label: 'كود الخروج', key: 'exit_code' }, { label: 'المخرجات (مُنقّحة)', render: r => `<pre class="out">${esc(r.output)}</pre>` }], { empty: 'لم يعمل بعد' }) }); }
          if (ev.target.id === 'upS') return App.formModal('رفع سكريبت', [{ name: 'name', label: 'الاسم (حروف إنجليزية)', required: true, ltr: true }, { name: 'module', label: 'الوحدة', options: [['M04', 'M04'], ['M02', 'M02'], ['M03', 'M03'], ['M05', 'M05'], ['M06', 'M06']] }, { name: 'runner', label: 'النوع', options: [['python', 'Python قابل للتشغيل'], ['none', 'مرجع فقط (Apps Script وغيره)']] }, { name: 'file_id', label: 'الملف', type: 'file', category: 'script', required: true, accept: '.py,.gs,.js,.txt' }, { name: 'schedule_minutes', label: 'كل كام دقيقة (فاضي = يدوي)', type: 'number' }, { name: 'timeout_seconds', label: 'المهلة بالثواني', type: 'number', value: 60 }, { name: 'description', label: 'الوصف', keepEmpty: true }], {},
            async d => { d.description = d.description || ''; if (d.schedule_minutes == null) delete d.schedule_minutes; await App.post('/api/system/scripts', d); App.pages.integrations(el, '', q); });
        } catch (err) { App.fail(err); }
      };
    } else {
      const w = await App.get('/api/system/worker');
      el.innerHTML = head + `<div class="panel"><h2>حالة الـ Worker</h2><p>${App.badge(w.state_ar, w.state === 'running' ? 'good' : 'warn')}</p><dl class="kv"><dt>آخر نبضة</dt><dd>${fmtDT(w.last_beat)}</dd><dt>الجهاز</dt><dd>${esc(w.host || '—')}</dd></dl>${w.last_summary ? `<pre class="out">${esc(JSON.stringify(w.last_summary, null, 1))}</pre>` : ''}
        <p class="muted small">الـ Worker عملية مستقلة (run.bat بيشغّله). بيكمل لو قفلت المتصفح، وبيقف لو الجهاز اتقفل — وساعتها البيانات تتعلّم «قديمة». استمارة Google بتستقبل طلبات وهو واقف، وبتتسحب أول ما يرجع.</p></div>`;
    }
  };

  // ---------------- appearance & legacy migration
  App.pages.appearance = async el => {
    const cur = App.store.get('theme', 'system');
    el.innerHTML = App.head('المظهر والتخصيص', 'الاختيار بيتحفظ لحسابك على السيرفر') +
      `<div class="panel"><h2>المظهر</h2><div class="actions">${[['system', '◐ حسب الجهاز'], ['light', '☀ فاتح'], ['dark', '☾ غامق']].map(([k, l]) => `<button class="btn ${cur === k ? 'primary' : ''}" data-th="${k}">${l}</button>`).join('')}</div></div>
      ${App.can('system:edit') ? `<div class="panel"><h2>نقل بيانات النسخة القديمة (it.html)</h2><p class="small">من النسخة القديمة: المزامنة والإعدادات ← تصدير نسخة JSON، وبعدين ارفع الملف هنا. هتتعمل <b>تجربة بدون حفظ</b> الأول وتشوف التقرير.</p>
        ${App.form([{ name: 'default_company_id', label: 'الشركة المستهدفة', type: 'company', required: true }, { name: 'legacy', label: 'ملف JSON', type: 'file', accept: '.json' }], {}, 2)}<div class="actions"><button class="btn" id="dryB">تجربة (Dry run)</button><button class="btn primary" id="runB">تنفيذ النقل</button></div><div id="migOut"></div></div>` : ''}`;
    el.onclick = async ev => {
      const t = ev.target.closest('[data-th]'); if (t) { await App.setTheme(t.dataset.th); return App.pages.appearance(el); }
      if (ev.target.id === 'dryB' || ev.target.id === 'runB') {
        const out = el.querySelector('#migOut');
        try {
          const f = el.querySelector('[name=legacy]').files[0]; if (!f) throw new Error('اختار ملف JSON');
          let data = JSON.parse(await f.text()); if (data.DB) data = data.DB;
          const body = { data, default_company_id: el.querySelector('[name=default_company_id]').value, dry_run: ev.target.id === 'dryB' };
          if (!body.dry_run && !await App.confirm('تنفيذ النقل', 'هيتسجل في السيرفر (الإعادة آمنة — اللي اتنقل قبل كده بيتخطى). متأكد؟')) return;
          const r = await App.post('/api/migration/legacy-import', body);
          out.innerHTML = `<div class="notice ${r.dry_run ? '' : 'warn'}">${r.dry_run ? 'تجربة — لم يُحفظ شيء' : 'تم النقل'}: موظفين ${r.employees} · أصول ${r.assets} · شرائح ${r.sims} · خطوط ${r.lines} · اشتراكات ${r.subscriptions} · موردين ${r.suppliers} · موجود سابقًا ${r.skipped_existing}</div>${r.warnings.length ? `<div class="notice warn">${r.warnings.map(esc).join('<br>')}</div>` : ''}${r.unmapped.length ? App.table(r.unmapped, [{ label: 'النوع', key: 'type' }, { label: 'السجل', key: 'legacy' }, { label: 'السبب', key: 'reason' }], { compact: true }) : ''}`;
        } catch (err) { out.innerHTML = App.errorBox(err); }
      }
    };
  };

  // ---------------- logs & backups
  App.pages.logs = async (el, arg, q) => {
    const tab = q.tab || 'audit';
    const tabs = `<div class="tabs">${[['audit', 'سجل التدقيق'], ['backups', 'النسخ الاحتياطي']].map(([k, l]) => `<button class="${tab === k ? 'on' : ''}" onclick="location.hash='#/logs?tab=${k}'">${l}</button>`).join('')}</div>`;
    if (tab === 'audit') {
      const rows = await App.get('/api/audit?limit=300' + (q.action ? '&action=' + encodeURIComponent(q.action) : ''));
      el.innerHTML = App.head('السجلات والنسخ الاحتياطي', 'سجل غير قابل للتعديل أو الحذف — بدون كلمات مرور أو أسرار') + tabs + `<div class="panel">${App.table(rows, [{ label: 'الوقت', render: r => fmtDT(r.at) }, { label: 'العملية', render: r => `<span class="mono">${esc(r.action)}</span>` }, { label: 'الكيان', render: r => esc(r.entity) + ' <span class="mono small">' + esc((r.entity_id || '').slice(0, 12)) + '</span>' }, { label: 'التفاصيل', render: r => `<span class="small">${esc(JSON.stringify(r.details)).slice(0, 160)}</span>` }, { label: 'IP', key: 'ip' }], { compact: true })}</div>`;
    } else {
      const rows = await App.get('/api/system/backups'), can = App.can('system:edit');
      el.innerHTML = App.head('السجلات والنسخ الاحتياطي', 'نسخة = قاعدة البيانات + الملفات + بصمات. «تحقق» = استعادة تجريبية حقيقية في مجلد مؤقت') + tabs +
        `<div class="notice muted">الاستعادة الفعلية بتتعمل والسيرفر واقف: <span class="mono" dir="ltr">restore.bat &lt;اسم النسخة&gt;</span> — وبتاخد نسخة قبل الاستعادة تلقائيًا. انسخ ملفات النسخ لمكان تاني (Drive/هارد خارجي) — النسخة على نفس الجهاز مش كفاية.</div>
        ${can ? '<div class="actions" style="margin-bottom:12px"><button class="btn primary" id="bkB">💾 نسخة احتياطية الآن</button></div>' : ''}
        <div class="panel">${App.table(rows, [{ label: 'الاسم', render: b => `<span class="mono" dir="ltr">${esc(b.name)}</span>` }, { label: 'الحجم', render: b => (b.size / 1024).toFixed(0) + ' KB' }, { label: 'التاريخ', render: b => fmtDT(b.created) }, { label: '', render: b => can ? `<button class="link" data-ver="${esc(b.name)}">تحقق (استعادة تجريبية)</button>` : '' }], { empty: 'لا توجد نسخ' })}</div><div id="verOut"></div>`;
      el.onclick = async ev => {
        const v = ev.target.closest('[data-ver]');
        try {
          if (ev.target.id === 'bkB') { const r = await App.post('/api/system/backups', { label: '' }); App.toast('اتعملت ' + r.name); return App.pages.logs(el, '', q); }
          if (v) { const r = await App.post(`/api/system/backups/${encodeURIComponent(v.dataset.ver)}/verify`); el.querySelector('#verOut').innerHTML = `<div class="notice ${r.ok ? '' : 'bad'}">${r.ok ? '✅ النسخة سليمة' : '❌ ' + r.problems.map(esc).join('<br>')} · integrity: ${esc(r.integrity)} · ملفات: ${r.files}</div>${App.table(Object.entries(r.tables).map(([t, n]) => ({ t, n })), [{ label: 'الجدول', key: 't' }, { label: 'السجلات', key: 'n' }], { compact: true })}`; }
        } catch (err) { App.fail(err); }
      };
    }
  };
})(window.App);
