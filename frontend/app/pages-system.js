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
      `<div class="panel">${App.table(users, [{ label: 'الاسم', render: u => `<b>${esc(u.name)}</b>${u.id === App.me.id ? ' ' + App.badge('أنت', 'info') : ''}` }, { label: 'البريد', render: u => `<span dir="ltr">${esc(u.email)}</span>` }, { label: 'الدور', render: u => App.badge(u.role.name_ar, ROLE_KIND[u.role.key] || 'muted') }, { label: 'النطاق', render: scopeTxt }, { label: 'الحالة', render: u => (u.active ? App.badge('نشط', 'good') : App.badge('موقوف', 'muted')) + ' ' + (u.mfa_enabled ? App.badge('MFA', 'info') : '') }, { label: '', render: u => `<button class="link" data-ed="${u.id}">تعديل</button><button class="link" data-pw="${u.id}">كلمة مرور</button>${u.mfa_enabled ? `<button class="link" data-mfa="${u.id}">إعادة ضبط MFA</button>` : ''}` }])}</div>`;
    const roleOpts = roles.filter(r => r.key !== 'super_admin' || App.me.role.key === 'super_admin').map(r => [r.key, r.name_ar]);
    const scopeFields = [{ name: 'all_companies', label: 'كل الشركات', type: 'checkbox' }, { name: 'scope_company', label: 'أو شركة محددة', options: [['', '—']].concat(App.companies.map(c => [c.id, c.name])) }];
    const scopes = d => { const s = d.scope_company ? [{ company_id: d.scope_company }] : []; delete d.scope_company; d.scopes = s; return d; };
    el.onclick = async ev => {
      const ed = ev.target.closest('[data-ed]'), pw = ev.target.closest('[data-pw]'), mf = ev.target.closest('[data-mfa]');
      try {
        if (mf) { if (!await App.confirm('إعادة ضبط التحقق بخطوتين', 'لو المستخدم ضيّع موبايله: التحقق هيتلغي وجلساته هتتقفل، ويفعّله تاني بنفسه.')) return; await App.post(`/api/users/${mf.dataset.mfa}/mfa-reset`); return App.pages.users(el); }
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
    const chips = (c, kind, list) => list.length ? list.map(x => `<span class="badge ${x.active === false ? 'b-muted' : 'b-info'}" style="margin:2px">${esc(x.name)}${x.active === false ? ' (معطّل)' : ''}${ed ? ` <button class="link" data-ren="${kind}" data-c="${c.id}" data-id="${x.id}" data-name="${esc(x.name)}" title="تعديل الاسم">✏️</button><button class="link" data-tog="${kind}" data-c="${c.id}" data-id="${x.id}" data-on="${x.active === false ? 1 : 0}" title="${x.active === false ? 'تفعيل' : 'تعطيل'}">${x.active === false ? '↺' : '🚫'}</button>` : ''}</span>`).join('') : '<span class="muted">—</span>';
    const lh = (c, f, label) => `<div class="field"><span>${label}</span>${c[f] ? `<img src="/api/files/${c[f]}" alt="${label}" style="max-width:100%;max-height:90px;border:1px solid var(--border);border-radius:6px;background:#fff">` : '<span class="muted small">لم تُرفع</span>'}${ed ? `<span><button class="btn sm" data-lh="${f}" data-c="${c.id}">⬆ رفع صورة</button>${c[f] ? ` <button class="btn sm danger" data-lhx="${f}" data-c="${c.id}">إزالة</button>` : ''}</span>` : ''}</div>`;
    el.innerHTML = App.head('الشركات والفروع والأقسام', 'كل حاجة هنا بتضيفها وتعدّلها انت — مفيش بيانات ثابتة. بادئة الأكواد بتتقفل بعد أول أصل.', ed ? '<button class="btn primary" id="newC">+ شركة</button>' : '') +
      (App.companies.length ? App.companies.map(c => `<div class="panel"><div class="panel-head"><h2>${esc(c.name)} ${c.asset_prefix ? `<span class="mono badge b-info">${esc(c.asset_prefix)}</span>` : App.badge('بدون بادئة أكواد', 'warn')}</h2>${ed ? `<span><button class="btn sm" data-ec="${c.id}">✏️ تعديل</button> <button class="btn sm" data-br="${c.id}">+ فرع</button> <button class="btn sm" data-dp="${c.id}">+ قسم</button> <button class="btn sm" data-tx="${c.id}">📝 نصوص المطبوعات</button></span>` : ''}</div>
        <p><b>الفروع:</b> ${chips(c, 'branches', c.branches)}</p><p><b>الأقسام:</b> ${chips(c, 'departments', c.departments)}</p>
        <h3 style="margin-top:12px">دباجة المطبوعات</h3><p class="muted small">صورة PNG/JPG بعرض الصفحة. بتظهر أعلى وأسفل كل ورقة مطبوعة لهذه الشركة (طلب الشراء، العهدة، الإخلاء، التقارير). لو مش موجودة بيطبع اسم الشركة بس.</p>
        <div class="grid2">${lh(c, 'letterhead_header_file_id', 'الهيدر (أعلى الصفحة)')}${lh(c, 'letterhead_footer_file_id', 'الفوتر (أسفل الصفحة)')}</div></div>`).join('') : App.emptyBox('مفيش شركات — ابدأ بإضافة شركتك'));
    const reload = () => App.pages.companies(el);
    el.onclick = async ev => {
      const ec = ev.target.closest('[data-ec]'), br = ev.target.closest('[data-br]'), dp = ev.target.closest('[data-dp]'), ren = ev.target.closest('[data-ren]'), tog = ev.target.closest('[data-tog]'), lhb = ev.target.closest('[data-lh]'), lhx = ev.target.closest('[data-lhx]'), tx = ev.target.closest('[data-tx]');
      try {
        if (ev.target.id === 'newC') return App.formModal('شركة جديدة', [{ name: 'name', label: 'الاسم', required: true }, { name: 'asset_prefix', label: 'بادئة أكواد الأصول (حروف/أرقام)', hint: 'مثال توضيحي فقط: ZWolf ← ZWolf-AIO-001', keepEmpty: true, ltr: true }], {}, async d => { d.asset_prefix = d.asset_prefix || ''; await App.post('/api/companies', d); reload(); });
        if (ec) { const c = App.companies.find(x => x.id === ec.dataset.ec); return App.formModal('تعديل ' + c.name, [{ name: 'name', label: 'الاسم', required: true }, { name: 'asset_prefix', label: 'بادئة الأكواد', keepEmpty: true, ltr: true }], c, async d => { d.asset_prefix = d.asset_prefix || ''; await App.patch('/api/companies/' + c.id, d); reload(); }); }
        if (br || dp) { const id = (br || dp).dataset[br ? 'br' : 'dp']; return App.formModal(br ? 'فرع جديد' : 'قسم جديد', [{ name: 'name', label: 'الاسم', required: true }], {}, async d => { await App.post(`/api/companies/${id}/${br ? 'branches' : 'departments'}`, d); reload(); }); }
        if (ren) return App.formModal('تعديل الاسم', [{ name: 'name', label: 'الاسم', required: true }], { name: ren.dataset.name }, async d => { await App.patch(`/api/companies/${ren.dataset.c}/${ren.dataset.ren}/${ren.dataset.id}`, d); reload(); }, { intro: 'الاسم الجديد هيظهر في كل السجلات المربوطة.' });
        if (tog) { await App.patch(`/api/companies/${tog.dataset.c}/${tog.dataset.tog}/${tog.dataset.id}`, { active: tog.dataset.on === '1' }); return reload(); }
        if (lhb) return App.formModal('رفع صورة الدباجة', [{ name: 'file', label: 'صورة PNG أو JPG', type: 'file', category: 'letterhead', required: true, accept: '.png,.jpg,.jpeg' }], {}, async d => { await App.patch('/api/companies/' + lhb.dataset.c, { [lhb.dataset.lh]: d.file }); App.toast('اتحفظت الدباجة'); reload(); });
        if (lhx) { await App.patch('/api/companies/' + lhx.dataset.c, { [lhx.dataset.lhx]: '' }); return reload(); }
        if (tx) {
          const t = await App.get(`/api/companies/${tx.dataset.c}/texts`);
          return App.modal({ title: 'نصوص المطبوعات', wide: true, body: Object.entries(t).map(([k, v]) => `<label class="field full"><span>${esc(v.label)}</span><textarea rows="4" data-k="${k}">${esc(v.value)}</textarea></label>`).join(''),
            actions: [{ label: 'حفظ', primary: true, onClick: async m => { for (const ta of m.querySelectorAll('[data-k]')) await App.put(`/api/companies/${tx.dataset.c}/texts/${ta.dataset.k}`, { value: ta.value }); App.toast('اتحفظت النصوص'); } }] });
        }
      } catch (err) { App.fail(err); }
    };
  };

  // ---------------- Excel import / export hub (all sections in one place)
  App.pages.excel = async el => {
    const SECTIONS = [
      ['assets', '💻', 'الأجهزة والعهد', 'assets', 'الكود بيتولد تلقائي للأجهزة الجديدة · تغيير المستلم من الملف مش بيتنفذ (بيتعمل من التسليم)'],
      ['employees', '👥', 'الموظفون', 'employees', 'بيطابق بالكود الوظيفي أو الاسم داخل نفس الشركة · بدون بيانات حساسة'],
      ['sims', '📱', 'الشرائح (مصر والسعودية)', 'technical', 'عمود «الدولة»: مصر / السعودية'],
      ['lines', '🌐', 'الإنترنت والأرضي', 'technical', 'السعة والعتبات والمدة لكل خط'],
      ['subscriptions', '🪪', 'الاشتراكات والتراخيص', 'technical', 'AI والأدوات والإيميلات والدومينات'],
      ['mailboxes', '✉️', 'إيميلات الموظفين', 'technical', 'كل إيميل بكوده وصاحبه — عمود PSWD يتحفظ مشفّر'],
      ['suppliers', '🏭', 'الموردون', 'finance', 'بيطابق بالاسم'],
    ];
    const PERM = { assets: ['assets:create', 'assets:export'], employees: ['employees:create', 'employees:export'], sims: ['technical:create', 'technical:export'], lines: ['technical:create', 'technical:export'], subscriptions: ['technical:create', 'technical:export'], mailboxes: ['technical:create', 'technical:export'], suppliers: ['finance:create', 'finance:export'] };
    const visible = SECTIONS.filter(s => App.can(PERM[s[0]][0]) || App.can(PERM[s[0]][1]));
    el.innerHTML = App.head('استيراد وتصدير Excel', 'كل الأقسام في مكان واحد — صدّر ملف Excel، عدّل فيه، وارجع استورده بنفس الأعمدة') +
      `<div class="notice muted">الاستيراد بيقبل <b>ملف Excel / CSV من جهازك</b> أو <b>لينك Google Sheet</b>، وأول خطوة دايمًا <b>معاينة</b> بتوضح الجديد والتحديث والأخطاء قبل ما يتحفظ أي حاجة.</div>` +
      (visible.length ? `<div class="gauge-grid">${visible.map(([k, ic, label, , hint]) => `<div class="gauge-card"><div class="top"><b>${ic} ${esc(label)}</b></div><span class="small muted">${esc(hint)}</span>
        <div class="actions">${App.can(PERM[k][1]) ? `<a class="btn sm primary" href="/api/export/${k}?fmt=xlsx${App.scopeCompany() ? '&company_id=' + App.scopeCompany() : ''}">⬇ تصدير Excel</a><a class="btn sm ghost" href="/api/export/${k}?fmt=csv">CSV</a>` : ''}
        ${App.can(PERM[k][0]) ? `<button class="btn sm" data-import="${k}">⬆ استيراد</button><a class="btn sm ghost" href="/api/import/${k}/template">📄 قالب فاضي</a>` : ''}</div></div>`).join('')}</div>` : App.emptyBox('مفيش أقسام متاحة لصلاحيتك')) +
      `<div class="panel" style="margin-top:16px"><div class="panel-head"><h2 class="t">📗 قوالب الشيتات الخمسة (Workbooks)</h2></div><p class="small muted">قوالب كاملة بكل الأوراق والقوائم المنسدلة — ترفعها على Google Sheets، أو تستخدمها كملف Excel محلي من «الربط والأتمتة ← ⬆ ملف Excel».</p><div id="wbList" class="actions"></div></div>`;
    try { const wbs = await App.get('/api/templates/workbooks'); el.querySelector('#wbList').innerHTML = wbs.map(w => `<a class="btn sm" href="/api/templates/workbooks/${encodeURIComponent(w.name)}">⬇ ${esc(w.label)}</a>`).join(''); } catch (e) { /* optional */ }
  };

  // ---------------- editable lists (every dropdown in the system)
  App.pages.lists = async el => {
    const lists = await App.get('/api/lookups?include_inactive=true'), cats = (App.cache.assetMeta || {}).categories || [];
    lists.forEach(l => { App.lookups[l.key] = l; });
    el.innerHTML = App.head('القوائم والإعدادات', 'كل القوائم المنسدلة في البرنامج — ضيف وعدّل وعطّل من هنا أو من جنب أي قائمة (➕ ✏️ 🗑)') +
      `<div class="blocks">${lists.map(l => `<div class="panel"><div class="panel-head"><h2 class="t">${esc(l.label)}</h2>${l.can_edit ? `<button class="btn sm" data-add="${l.key}">+ إضافة</button>` : ''}</div>
        ${l.items.length ? `<ul class="notif">${l.items.map(i => `<li style="flex-direction:row;justify-content:space-between;align-items:center">${i.active ? esc(i.value) : `<span class="muted">${esc(i.value)} (معطّلة)</span>`}${l.can_edit ? `<span><button class="link" data-ed="${l.key}" data-id="${i.id}" data-v="${esc(i.value)}">✏️</button><button class="link" data-tg="${l.key}" data-id="${i.id}" data-on="${i.active ? 0 : 1}">${i.active ? '🗑' : '↺'}</button></span>` : ''}</li>`).join('')}</ul>` : '<p class="muted small">فاضية</p>'}</div>`).join('')}
      ${cats.length ? `<div class="panel"><div class="panel-head"><h2 class="t">فئات الأصول</h2>${App.can('system:edit') ? '<button class="btn sm" id="addCat">+ فئة</button>' : ''}</div><ul class="notif">${cats.map(c => `<li style="flex-direction:row;justify-content:space-between"><span><b class="mono">${esc(c.code)}</b> — ${esc(c.name_ar)}</span>${App.can('system:edit') ? `<button class="link" data-cat="${esc(c.code)}" data-v="${esc(c.name_ar)}">✏️</button>` : ''}</li>`).join('')}</ul><p class="muted small">كود الفئة مش بيتغير بعد الإضافة لأن أكواد الأصول مبنية عليه.</p></div>` : ''}</div>`;
    const reload = async () => { await App.loadLookups(); App.pages.lists(el); };
    el.onclick = async ev => {
      const add = ev.target.closest('[data-add]'), ed = ev.target.closest('[data-ed]'), tg = ev.target.closest('[data-tg]'), cat = ev.target.closest('[data-cat]');
      try {
        if (add) return App.formModal('إضافة', [{ name: 'value', label: 'القيمة', required: true }], {}, async d => { await App.post('/api/lookups/' + add.dataset.add, d); reload(); });
        if (ed) return App.formModal('تعديل', [{ name: 'value', label: 'القيمة', required: true }], { value: ed.dataset.v }, async d => { await App.patch(`/api/lookups/${ed.dataset.ed}/${ed.dataset.id}`, d); reload(); });
        if (tg) { await App.patch(`/api/lookups/${tg.dataset.tg}/${tg.dataset.id}`, { active: tg.dataset.on === '1' }); return reload(); }
        if (cat) return App.formModal('تعديل اسم الفئة ' + cat.dataset.cat, [{ name: 'name_ar', label: 'الاسم', required: true }], { name_ar: cat.dataset.v }, async d => { await App.patch('/api/assets/categories/' + cat.dataset.cat, d); App.cache.assetMeta = await App.get('/api/assets/meta'); App.pages.lists(el); });
        if (ev.target.id === 'addCat') return App.formModal('فئة أصول جديدة', [{ name: 'code', label: 'الكود (حروف/أرقام — ثابت)', required: true, ltr: true }, { name: 'name_ar', label: 'الاسم', required: true }, { name: 'seq_width', label: 'عدد خانات التسلسل', type: 'number', value: 3 }], {}, async d => { d.name_en = ''; await App.post('/api/assets/categories', d); App.cache.assetMeta = await App.get('/api/assets/meta'); App.pages.lists(el); });
      } catch (err) { App.fail(err); }
    };
  };

  // ---------------- integration center + scripts + conflicts
  const ST = { not_configured: 'muted', pending_live_credentials: 'warn', disabled: 'muted', healthy: 'good', stale: 'warn', error: 'bad' };
  App.pages.integrations = async (el, arg, q) => {
    const tab = q.tab || 'connectors', mi = App.can('system:manage_integration');
    const tabs = `<div class="tabs">${[['connectors', 'الشيتات والخدمات'], ['conflicts', 'تعارضات المزامنة'], ['scripts', 'السكريبتات'], ...(App.can('system:manage_secrets') ? [['secrets', 'الأسرار المشفّرة']] : []), ['worker', 'حالة التشغيل']].map(([k, l]) => `<button class="${tab === k ? 'on' : ''}" onclick="location.hash='#/integrations?tab=${k}'">${l}</button>`).join('')}</div>`;
    const head = App.head('الربط والأتمتة', 'لكل قسم: لينك الشيت بتاعه، وهل متصل بالنظام ولا لأ') + tabs;
    if (tab === 'connectors') {
      const s = await App.get('/api/integrations/status');
      const MOD = { M02: 'الأصول والعهد', M03: 'رحلة الموظف', M04: 'الخدمات التقنية', M05: 'الإدارة والمالية', M06: 'إدارة النظام' };
      const card = c => { const ok = c.status === 'healthy'; return `<div class="gauge-card"><div class="top"><b>${esc(c.title_ar)}</b>${ok ? App.badge('✓ متصل', 'good') : App.badge('✗ غير متصل', c.status === 'error' ? 'bad' : 'muted')}</div>
        <span class="small muted">${ok ? 'آخر تحديث ' + fmtDT(c.last_sync_at) : esc(c.status_ar)}</span>${c.last_error ? `<span class="small t-bad">${esc(c.last_error.slice(0, 140))}</span>` : ''}
        ${c.mode === 'local_xlsx' && c.source_ref ? `<span class="small">📁 ملف Excel محلي <a href="/api/system/integrations/${c.key}/workbook">⬇ نزّل النسخة الحالية</a></span>` : ''}
        <div class="actions">${c.link_url ? `<a class="btn sm" href="${esc(c.link_url)}" target="_blank" rel="noopener noreferrer">📄 افتح</a>` : '<span class="muted small">مفيش لينك</span>'}${mi ? `<button class="btn sm" data-cfg="${c.key}">⚙ إعداد</button><button class="btn sm ghost" data-wb="${c.key}" title="استخدام ملف Excel على الجهاز كمصدر للبيانات">⬆ ملف Excel</button>` : ''}${mi && c.enabled && c.source_ref ? `<button class="btn sm primary" data-sync="${c.key}">↻ حدّث دلوقتي</button>` : ''}</div></div>`; };
      const groups = Object.entries(MOD).map(([m, lbl]) => { const cs = s.cards.filter(c => c.module === m); return cs.length ? `<div class="panel"><div class="panel-head"><h2 class="t">${lbl}</h2></div><div class="gauge-grid">${cs.map(card).join('')}</div></div>` : ''; }).join('');
      el.innerHTML = head + `<div class="notice ${s.connected_live ? '' : 'warn'}">متصل: <b>${s.connected_live}</b> من ${s.cards.length}. «اللينك» للفتح السريع بس. «متصل» يعني النظام بيقرا ويكتب في الشيت فعلًا — ده محتاج ملف صلاحية Google (هنعمله مع بعض).</div>${groups}`;
      el.onclick = async ev => {
        const cfg = ev.target.closest('[data-cfg]'), sy = ev.target.closest('[data-sync]'), wbb = ev.target.closest('[data-wb]');
        try {
          if (wbb) return App.modal({ title: 'ملف Excel محلي كمصدر للبيانات', body: `<p class="small">النظام هيقرا ويكتب في الملف ده نفسه في كل مزامنة: التنسيق والقوائم المنسدلة والمعادلات بتفضل زي ما هي، وبيتاخد نسخة احتياطية (.bak) قبل أول كتابة.</p><p class="small">ابدأ من القالب الجاهز لو مفيش ملف عندك: <a href="#/excel">صفحة استيراد وتصدير Excel</a>.</p><label class="field"><span>ملف .xlsx</span><input type="file" id="wbFile" accept=".xlsx"></label>`,
            actions: [{ label: 'رفع واستخدام', primary: true, onClick: async m => { const f = m.querySelector('#wbFile').files[0]; if (!f) throw new Error('اختار الملف'); const fd = new FormData(); fd.append('file', f); await App.api('POST', `/api/system/integrations/${wbb.dataset.wb}/workbook`, fd); await App.put('/api/integrations/' + wbb.dataset.wb, { enabled: true }); App.toast('اترفع — دوس «حدّث دلوقتي» للمزامنة'); App.pages.integrations(el, '', q); } }] });
          if (cfg) { const c = s.cards.find(x => x.key === cfg.dataset.cfg); return App.formModal('إعداد: ' + c.title_ar, [{ name: 'link_url', label: 'لينك الشيت (للزرار «افتح الشيت»)', keepEmpty: true, ltr: true, full: true, hint: 'انسخه من شريط العنوان في Google Sheets' }, { name: 'enabled', label: 'ربط تلقائي بالنظام (قراءة/كتابة)', type: 'checkbox' }, { name: 'mode', label: 'طريقة الربط', options: [['manual', 'يدوي'], ['sheets', 'Google Sheets'], ['local_xlsx', 'ملف Excel محلي'], ['api', 'API'], ['script', 'سكريبت']] }, { name: 'source_ref', label: 'Spreadsheet ID (للربط التلقائي)', keepEmpty: true, ltr: true, hint: 'الجزء اللي بين /d/ و /edit في اللينك — بيتملا تلقائي من اللينك' }, { name: 'credential_ref', label: 'اسم ملف الصلاحية (بدون .json)', keepEmpty: true, ltr: true, hint: 'اسم مرجعي فقط — ممنوع لصق المفتاح نفسه' }], c,
            async d => { d.link_url = (d.link_url || '').trim(); d.credential_ref = d.credential_ref || ''; const m = d.link_url.match(/\/spreadsheets\/d\/([A-Za-z0-9_-]+)/); d.source_ref = d.source_ref || (m ? m[1] : ''); await App.put('/api/integrations/' + c.key, d); App.cache.links = await App.get('/api/integrations/links'); App.pages.integrations(el, '', q); }, { cols: 2 }); }
          if (sy) { const r = await App.post(`/api/system/integrations/${sy.dataset.sync}/sync`); App.toast(`النتيجة: ${r.status}${r.error ? ' — ' + r.error : ''}${r.pulled != null ? ` · سحب ${r.pulled} · دفع ${r.pushed} · إضافة ${r.appended} · تعارضات ${r.conflicts}` : ''}`, r.status === 'healthy' ? 'ok' : 'warn'); App.pages.integrations(el, '', q); }
        } catch (err) { App.fail(err); }
      };
    } else if (tab === 'secrets') {
      const rows = await App.get('/api/system/secrets');
      el.innerHTML = head + `<div class="notice">الأسرار (ملف صلاحية Google، باسوردات حسابات الخدمات…) بتتحفظ <b>مشفّرة AES-256</b> في ملف <b>خارج فولدر المشروع</b>. القيمة مش بتظهر تاني بعد الحفظ — تقدر بس تستبدلها أو تمسحها. اسم السر هو اللي بتكتبه في «اسم ملف الصلاحية» في إعداد الموصل.</div>
        <div class="actions" style="margin-bottom:12px"><button class="btn primary" id="addSec">+ سر جديد / استبدال</button></div>
        <div class="panel">${App.table(rows, [{ label: 'الاسم', render: s => `<span class="mono" dir="ltr">${esc(s.name)}</span>` }, { label: 'آخر تحديث', render: s => fmtDT(s.updated_at) }, { label: '', render: s => `<button class="link" data-rep="${esc(s.name)}">استبدال</button><button class="link danger" data-del="${esc(s.name)}">مسح</button>` }], { empty: 'مفيش أسرار متخزنة' })}</div>`;
      el.onclick = async ev => {
        const rep = ev.target.closest('[data-rep]'), del = ev.target.closest('[data-del]');
        try {
          if (ev.target.id === 'addSec' || rep) return App.formModal('سر مشفّر', [{ name: 'name', label: 'الاسم (حروف إنجليزية وأرقام و - _ .)', required: true, ltr: true, value: rep ? rep.dataset.rep : '' }, { name: 'value', label: 'القيمة (مثلًا محتوى ملف JSON للـ Service Account)', type: 'textarea', required: true, full: true }], {},
            async d => { await App.put('/api/system/secrets/' + encodeURIComponent(d.name), { value: d.value }); App.toast('اتحفظ مشفّر'); App.pages.integrations(el, '', q); });
          if (del) { if (!await App.confirm('مسح السر', 'أي موصل بيستخدمه هيقف لحد ما تضيفه تاني.')) return; await App.api('DELETE', '/api/system/secrets/' + encodeURIComponent(del.dataset.del)); App.pages.integrations(el, '', q); }
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
      <div class="panel"><h2>التحقق بخطوتين (MFA)</h2>${App.me.mfa_enabled ? `<p>${App.badge('مفعّل', 'good')} الدخول بيطلب كود من تطبيق Authenticator.</p><button class="btn danger" id="mfaOff">إلغاء التحقق بخطوتين</button>`
        : `<p class="small">بيحمي حسابك حتى لو كلمة المرور اتسربت. محتاج تطبيق زي Google Authenticator أو Microsoft Authenticator على الموبايل.</p><button class="btn primary" id="mfaOn">تفعيل التحقق بخطوتين</button>`}</div>
      ${App.can('system:edit') ? `<div class="panel"><h2>نقل بيانات النسخة القديمة (it.html)</h2><p class="small">من النسخة القديمة: المزامنة والإعدادات ← تصدير نسخة JSON، وبعدين ارفع الملف هنا. هتتعمل <b>تجربة بدون حفظ</b> الأول وتشوف التقرير.</p>
        ${App.form([{ name: 'default_company_id', label: 'الشركة المستهدفة', type: 'company', required: true }, { name: 'legacy', label: 'ملف JSON', type: 'file', accept: '.json' }], {}, 2)}<div class="actions"><button class="btn" id="dryB">تجربة (Dry run)</button><button class="btn primary" id="runB">تنفيذ النقل</button></div><div id="migOut"></div></div>` : ''}`;
    el.onclick = async ev => {
      const t = ev.target.closest('[data-th]'); if (t) { await App.setTheme(t.dataset.th); return App.pages.appearance(el); }
      try {
        if (ev.target.id === 'mfaOn') {
          const s = await App.post('/api/auth/me/mfa/setup');
          const qr = typeof window.qrcode === 'function' ? (() => { const q = window.qrcode(0, 'M'); q.addData(s.otpauth_uri); q.make(); return q.createSvgTag({ cellSize: 5, margin: 2, scalable: true }); })() : '';
          return App.modal({ title: 'تفعيل التحقق بخطوتين', body: `<ol class="small"><li>افتح تطبيق Authenticator على الموبايل ← إضافة حساب ← مسح QR.</li><li>اكتب الكود اللي ظهر في التطبيق (6 أرقام).</li></ol><div style="max-width:220px;margin:auto;background:#fff;padding:6px;border-radius:8px">${qr}</div><p class="small center">أو اكتب المفتاح يدويًا: <span class="mono" dir="ltr">${esc(s.secret)}</span></p><label class="field"><span>الكود</span><input id="mfaCode" inputmode="numeric" maxlength="6" dir="ltr"></label>`,
            actions: [{ label: 'تفعيل', primary: true, onClick: async m => { await App.post('/api/auth/me/mfa/enable', { code: m.querySelector('#mfaCode').value }); App.me.mfa_enabled = true; App.toast('اتفعّل — الدخول الجاي هيطلب الكود'); App.pages.appearance(el); } }] });
        }
        if (ev.target.id === 'mfaOff') return App.formModal('إلغاء التحقق بخطوتين', [{ name: 'password', label: 'كلمة المرور', type: 'password', required: true }, { name: 'code', label: 'الكود الحالي من التطبيق', required: true, ltr: true }], {}, async d => { await App.post('/api/auth/me/mfa/disable', d); App.me.mfa_enabled = false; App.pages.appearance(el); });
      } catch (err) { return App.fail(err); }
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
      el.innerHTML = App.head('السجلات والنسخ الاحتياطي', 'سجل غير قابل للتعديل أو الحذف — كل حدث مربوط بالحدث اللي قبله بـ hash', '<button class="btn" id="verB">🔒 تحقق من سلامة السجل</button>') + tabs + `<div id="verOut2"></div><div class="panel">${App.table(rows, [{ label: 'الوقت', render: r => fmtDT(r.at) }, { label: 'العملية', render: r => `<span class="mono">${esc(r.action)}</span>` }, { label: 'الكيان', render: r => esc(r.entity) + ' <span class="mono small">' + esc((r.entity_id || '').slice(0, 12)) + '</span>' }, { label: 'التفاصيل', render: r => `<span class="small">${esc(JSON.stringify(r.details)).slice(0, 160)}</span>` }, { label: 'IP', key: 'ip' }], { compact: true })}</div>`;
      el.querySelector('#verB').onclick = async () => { try { const v = await App.get('/api/audit/verify'); el.querySelector('#verOut2').innerHTML = v.ok ? `<div class="notice">✅ السجل سليم — اتراجع ${v.checked} حدث ومفيش أي تعديل أو حذف.</div>` : `<div class="notice bad">⛔ السجل اتلعب فيه عند الحدث رقم ${v.broken_at}: ${esc(v.reason)}</div>`; } catch (e) { App.fail(e); } };
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
