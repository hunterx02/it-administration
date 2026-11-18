/* Employee e-mail accounts register — each account has a permanent code (PREFIX-MAIL-001), an owner and an
   encrypted password. Offboarding cannot be approved by IT while an employee still has an active account. */
(function (App) {
  'use strict';
  const { esc, fmtDT } = App;
  const KIND = { personal: 'شخصي', shared: 'مشترك', service: 'خدمة/نظام' };
  const ST = { active: ['نشط', 'good'], suspended: ['موقوف', 'warn'], deleted: ['محذوف', 'muted'] };
  const EV = { created: 'إنشاء', assigned: 'تسليم لموظف', unassigned: 'فك الربط', suspended: 'إيقاف', active: 'تفعيل', deleted: 'حذف', renamed: 'تغيير العنوان' };
  const cid = () => App.scopeCompany() || (App.companies[0] || {}).id;
  const stB = m => App.badge((ST[m.status] || [m.status])[0], (ST[m.status] || [, 'muted'])[1]);
  const clean = d => { ['display_name', 'provider', 'plan', 'aliases', 'forwarding', 'recovery', 'notes', 'code', 'password'].forEach(k => { if (d[k] == null) d[k] = ''; }); if (!d.employee_id) delete d.employee_id; return d; };

  const fields = (c, opts) => [
    { name: 'company_id', label: 'الشركة', type: 'company', value: c, required: true },
    { name: 'address', label: 'الإيميل', required: true, ltr: true },
    { name: 'code', label: 'الكود (فاضي = تلقائي)', ltr: true },
    { name: 'employee_id', label: 'الموظف (اختياري)', options: opts, pick: 'employee', company: c },
    { name: 'display_name', label: 'الاسم الظاهر' },
    { name: 'kind', label: 'النوع', options: Object.entries(KIND) },
    { name: 'provider', label: 'المزوّد', lookup: 'mail_provider' },
    { name: 'plan', label: 'الباقة', lookup: 'mail_plan' },
    { name: 'forwarding', label: 'تحويل إلى', ltr: true },
    { name: 'recovery', label: 'إيميل/رقم الاسترداد', ltr: true },
    { name: 'aliases', label: 'Aliases (مفصولة بفاصلة)', ltr: true },
    { name: 'mfa_enabled', label: 'التحقق بخطوتين MFA مفعّل', type: 'checkbox' },
    { name: 'is_primary', label: 'الإيميل الأساسي للموظف', type: 'checkbox' },
    ...(App.can('technical:view_secrets') ? [{ name: 'password', label: 'كلمة المرور (اختياري — تتحفظ مشفّرة)', type: 'password' }] : []),
    { name: 'notes', label: 'ملاحظات', type: 'textarea', full: true }];

  /* also used from the employee page */
  App.newMailbox = async (preset, after) => {
    const c = (preset && preset.company_id) || cid();
    const opts = [['', '— بدون —']].concat(await App.empOptions(x => x.company_id === c && !['offboarding', 'exited'].includes(x.status)));
    return App.formModal('إيميل جديد', fields(c, opts), Object.assign({ kind: 'personal' }, preset || {}), async d => {
      d.mfa_enabled = !!d.mfa_enabled; d.is_primary = !!d.is_primary;
      const m = await App.post('/api/mailboxes', clean(d));
      App.toast('اتسجل بالكود ' + m.code); (after || (() => App.go('#/mailbox/' + m.id)))(m);
    }, { wide: true, cols: 3, intro: 'الكود بيتعمل تلقائي من بادئة الشركة (مثال ZW-MAIL-001) ومش بيتغير. كلمة المرور مش بتتخزن في قاعدة البيانات.' });
  };

  App.pages.mailboxes = async (el, arg, q) => {
    q = q || {};
    const rows = await App.get('/api/mailboxes' + App.qs({ company_id: App.scopeCompany(), status: q.status || '', q: q.q || '' }));
    const n = s => rows.filter(r => r.status === s).length;
    el.innerHTML = App.head('الإيميلات والحسابات', 'كل إيميل له كود ثابت وصاحب — والباسورد مشفّر وكل عرض له متسجل', App.can('technical:create') ? '<button class="btn primary" id="newM">+ إيميل</button>' : '', 'technical', { io: 'mailboxes' }) +
      `<div class="kpi-strip">${[['إجمالي', rows.length, ''], ['نشط', n('active'), 'good'], ['نشط بدون موظف', rows.filter(r => r.status === 'active' && !r.employee_id).length, 'warn'], ['موقوف', n('suspended'), ''], ['نشط بدون MFA', rows.filter(r => r.status === 'active' && !r.mfa_enabled).length, 'bad']].map(([l, v, k]) => `<div class="kpi ${k}"><span class="l">${l}</span><span class="v">${v}</span></div>`).join('')}</div>
      <div class="panel"><div class="row" style="gap:8px;margin-bottom:10px"><input id="mq" placeholder="بحث بالإيميل أو الكود" value="${esc(q.q || '')}" style="max-width:260px" dir="ltr"><select id="mst" style="width:auto"><option value="">كل الحالات</option>${Object.entries(ST).map(([k, v]) => `<option value="${k}" ${q.status === k ? 'selected' : ''}>${v[0]}</option>`).join('')}</select></div>
      ${App.table(rows, [{ label: 'الكود', render: m => `<b class="mono">${esc(m.code)}</b>` }, { label: 'الإيميل', render: m => `<span dir="ltr">${esc(m.address)}</span>${m.is_primary ? ' ' + App.badge('أساسي', 'info') : ''}${m.has_password ? ' 🔑' : ''}` },
        { label: 'الموظف', render: m => m.employee_name ? `${esc(m.employee_name)} <span class="small mono muted">${esc(m.employee_code || '')}</span>` : '<span class="muted">—</span>' },
        { label: 'الشركة', key: 'company_name' }, { label: 'النوع', key: 'kind_label' }, { label: 'المزوّد', key: 'provider' }, { label: 'MFA', render: m => m.mfa_enabled ? '✓' : App.badge('لا', 'warn') }, { label: 'الحالة', render: stB }],
        { href: m => '#/mailbox/' + m.id, empty: 'لا توجد إيميلات — ضيف واحد أو استورد من إكسل' })}</div>`;
    const go = () => App.go('#/mailboxes' + App.qs({ q: el.querySelector('#mq').value.trim(), status: el.querySelector('#mst').value }));
    el.querySelector('#mq').onkeydown = e => { if (e.key === 'Enter') go(); };
    el.querySelector('#mst').onchange = go;
    const b = el.querySelector('#newM'); if (b) b.onclick = () => App.newMailbox();
  };

  App.pages.mailbox = async (el, id) => {
    const m = await App.get('/api/mailboxes/' + id), ed = App.can('technical:edit'), sec = App.can('technical:view_secrets');
    const reload = () => App.pages.mailbox(el, id);
    el.innerHTML = App.head(`${m.code} — ${m.address}`, `${stB(m)} · ${esc(m.kind_label)} · ${esc(m.company_name)}${m.is_primary ? ' · ' + App.badge('الإيميل الأساسي', 'info') : ''}`,
      [ed && m.status === 'active' ? `<button class="btn primary" data-do="assign">👤 ${m.employee_id ? 'نقل لموظف آخر' : 'تسليم لموظف'}</button>` : '',
        ed && m.employee_id ? '<button class="btn" data-do="unassign">فك الربط</button>' : '',
        ed && m.employee_id && !m.is_primary && m.status === 'active' ? '<button class="btn" data-do="primary">⭐ اجعله الأساسي</button>' : '',
        ed && m.status === 'active' ? '<button class="btn" data-do="suspend">⏸ إيقاف</button>' : '',
        ed && m.status !== 'active' ? '<button class="btn" data-do="activate">▶ تفعيل</button>' : '',
        sec ? '<button class="btn" data-do="pw">🔑 كلمة المرور</button>' : '',
        ed ? '<button class="btn" data-do="edit">✏️ تعديل</button>' : ''].join(''), 'technical') +
      `<div class="blocks"><div class="panel"><h2>البيانات</h2><dl class="kv">${[['الكود', m.code], ['الإيميل', m.address], ['الموظف', m.employee_name ? `${m.employee_name} (${m.employee_code || '—'})` : ''], ['الاسم الظاهر', m.display_name], ['المزوّد', m.provider], ['الباقة', m.plan], ['MFA', m.mfa_enabled ? 'مفعّل' : 'غير مفعّل'], ['تحويل إلى', m.forwarding], ['الاسترداد', m.recovery], ['Aliases', m.aliases], ['كلمة المرور', m.has_password ? '🔒 محفوظة مشفّرة' : 'غير محفوظة'], ['ملاحظات', m.notes]].map(([k, v]) => `<dt>${k}</dt><dd>${esc(v || '—')}</dd>`).join('')}</dl>
      ${m.employee_id ? `<p><a href="#/employee/${m.employee_id}">← صفحة الموظف</a></p>` : ''}</div>
      <div class="panel"><h2>السجل</h2>${App.table(m.events, [{ label: 'الوقت', render: e => fmtDT(e.at) }, { label: 'الحركة', render: e => esc(EV[e.kind] || e.kind) }, { label: 'الموظف', key: 'employee_name' }, { label: 'تفاصيل', key: 'detail' }], { compact: true })}</div></div>`;
    el.onclick = async ev => {
      const b = ev.target.closest('[data-do]'); if (!b) return;
      try {
        const k = b.dataset.do;
        if (k === 'assign') return App.formModal(m.employee_id ? 'نقل الإيميل لموظف آخر' : 'تسليم الإيميل لموظف', [{ name: 'employee_id', label: 'الموظف', options: await App.empOptions(x => x.company_id === m.company_id && x.id !== m.employee_id && !['offboarding', 'exited'].includes(x.status)), required: true, pick: 'employee', company: m.company_id }, { name: 'is_primary', label: 'اجعله الإيميل الأساسي للموظف', type: 'checkbox' }, { name: 'notes', label: 'ملاحظات', keepEmpty: true }], {}, async d => { d.is_primary = !!d.is_primary; d.notes = d.notes || ''; await App.post(`/api/mailboxes/${id}/assign`, d); reload(); });
        if (k === 'unassign') return App.formModal('فك ربط الإيميل عن ' + m.employee_name, [{ name: 'notes', label: 'السبب', keepEmpty: true }], {}, async d => { await App.post(`/api/mailboxes/${id}/unassign`, { notes: d.notes || '' }); reload(); });
        if (k === 'primary') { await App.patch('/api/mailboxes/' + id, { version: m.version, is_primary: true }); App.toast('بقى الإيميل الأساسي للموظف'); return reload(); }
        if (k === 'suspend' || k === 'activate') return App.formModal(k === 'suspend' ? 'إيقاف الإيميل' : 'تفعيل الإيميل', [{ name: 'notes', label: 'السبب', keepEmpty: true }], {}, async d => { await App.post(`/api/mailboxes/${id}/status/${k === 'suspend' ? 'suspended' : 'active'}`, { notes: d.notes || '' }); reload(); });
        if (k === 'pw') {
          const showPw = async () => {
            const r = await App.post(`/api/mailboxes/${id}/password/reveal`);
            App.modal({ title: 'كلمة مرور ' + m.address, body: `<p class="muted small">العرض ده اتسجل في سجل التدقيق. هتختفي بعد 30 ثانية.</p><div class="pick-wrap"><input id="pwv" readonly dir="ltr" class="mono" value="${esc(r.password)}"><button type="button" class="btn sm" id="pwc">📋 نسخ</button></div>`,
              onOpen: (mm, close) => { mm.querySelector('#pwc').onclick = async () => { try { await navigator.clipboard.writeText(r.password); App.toast('اتنسخت'); } catch (e) { mm.querySelector('#pwv').select(); } }; setTimeout(close, 30000); } });
          };
          return App.modal({ title: '🔑 كلمة مرور ' + m.address, body: `<p class="small">${m.has_password ? 'محفوظة مشفّرة في مخزن الأسرار (مش في قاعدة البيانات ولا في الشيت).' : 'مفيش كلمة مرور محفوظة للإيميل ده.'}</p>`,
            actions: [...(m.has_password ? [{ label: '👁 عرض', primary: true, onClick: showPw }] : []),
              ...(ed ? [{ label: m.has_password ? 'تغيير' : 'حفظ كلمة مرور', onClick: () => App.formModal('كلمة مرور الإيميل', [{ name: 'password', label: 'كلمة المرور', type: 'password', required: true }], {}, async d => { await App.put(`/api/mailboxes/${id}/password`, d); App.toast('اتحفظت مشفّرة'); reload(); }) }] : []),
              ...(ed && m.has_password ? [{ label: 'مسح', danger: true, onClick: async () => { if (await App.confirm('مسح كلمة المرور', 'هتتمسح من المخزن نهائيًا.')) { await App.api('DELETE', `/api/mailboxes/${id}/password`); reload(); } } }] : [])] });
        }
        if (k === 'edit') {
          const f = fields(m.company_id, []).filter(x => !['company_id', 'code', 'employee_id', 'is_primary', 'password'].includes(x.name));
          return App.formModal('تعديل ' + m.code, f, m, async d => { d.version = m.version; d.mfa_enabled = !!d.mfa_enabled; clean(d); delete d.code; delete d.password; await App.patch('/api/mailboxes/' + id, d); App.toast('اتحفظ'); reload(); }, { wide: true, cols: 3 });
        }
      } catch (err) { App.fail(err); }
    };
  };

  /* panel for the employee page (the employee page calls this if present) */
  App.mailPanel = (e, reload) => {
    if (!App.can('technical:view')) return '';
    const list = e.mailboxes || [];
    const canAdd = App.can('technical:create') && !['offboarding', 'exited'].includes(e.status);
    return `<div class="panel"><div class="row" style="justify-content:space-between"><h2>✉️ الإيميلات (${list.length})</h2>${canAdd ? '<button class="btn sm" id="empAddMail">+ إيميل</button>' : ''}</div>
      ${App.table(list, [{ label: 'الكود', render: m => `<span class="mono">${esc(m.code)}</span>` }, { label: 'الإيميل', render: m => `<span dir="ltr">${esc(m.address)}</span>${m.is_primary ? ' ' + App.badge('أساسي', 'info') : ''}` }, { label: 'النوع', key: 'kind_label' }, { label: 'الحالة', render: stB }], { href: m => '#/mailbox/' + m.id, compact: true, empty: 'مفيش إيميلات مسجلة للموظف' })}</div>`;
  };
  App.bindMailPanel = (el, e, reload) => {
    const b = el.querySelector('#empAddMail');
    if (b) b.onclick = () => App.newMailbox({ company_id: e.company_id, employee_id: e.id, display_name: e.full_name, is_primary: !e.email }, () => reload());
  };
})(window.App);
