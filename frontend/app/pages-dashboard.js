/* M01 — Dashboard (formal, chart-first). Critical first, then visual blocks. Every number comes from /api/dashboard
   (derived from module tables). Missing data -> explicit "no data", never a fake "healthy". Colors always carry text. */
(function (App) {
  'use strict';
  const { esc, num, fmtDate, fmtDT } = App;
  const SEV = { critical: ['حرج', '⛔'], high: ['مرتفع', '⚠️'], medium: ['متوسط', 'ℹ️'] };
  const USAGE = { normal: ['طبيعي', 'good'], prepare: ['جهّز رصيد', 'warn'], urgent: ['تجديد عاجل', 'bad'], incomplete: ['لا توجد قراءة', 'muted'], thresholds_missing: ['حدد العتبات', 'muted'] };
  const DUE = { ok: ['منتظم', 'good'], due_soon: ['قرب التجديد', 'warn'], overdue: ['متأخر', 'bad'], incomplete: ['بيانات ناقصة', 'muted'] };
  App.USAGE = USAGE; App.DUE = DUE;
  const SEVERITY = { urgent: 0, prepare: 1, thresholds_missing: 2, incomplete: 3, normal: 4 };
  /* lines needing action first: urgent, then prepare; ties by lowest remaining share */
  App.worstFirst = list => list.slice().sort((a, b) => (SEVERITY[a.usage_state] ?? 5) - (SEVERITY[b.usage_state] ?? 5) ||
    ((a.remaining_gb ?? 1e12) / (a.quota_gb || 1)) - ((b.remaining_gb ?? 1e12) / (b.quota_gb || 1)));
  const kpi = (label, value, kind, sub, href) => `<${href ? `a href="${href}"` : 'div'} class="kpi ${kind}"><span class="l">${esc(label)}</span><span class="v">${value == null ? '—' : esc(value)}</span>${sub ? `<span class="s">${esc(sub)}</span>` : ''}</${href ? 'a' : 'div'}>`;
  const due = i => i ? `${App.badge(DUE[i.due_state][0], DUE[i.due_state][1])} <span class="small">${i.next_due ? fmtDate(i.next_due) + ' · ' + i.days_left + ' يوم' : ''}</span>` : '';
  const mainCur = obj => Object.entries(obj || {}).sort((a, b) => Number(b[1]) - Number(a[1]))[0];

  function kpiStrip(d) {
    const s = d.alerts.by_severity || {}, out = [];
    out.push(kpi('تنبيهات حرجة', s.critical || 0, s.critical ? 'bad' : 'good', 'تحتاج تصرف فوري', '#/alerts'));
    out.push(kpi('تنبيهات مرتفعة', s.high || 0, s.high ? 'warn' : 'good', 'خلال أيام', '#/alerts'));
    if (d.renewals) {
      const urgent = d.renewals.primary_lines.filter(l => l.usage_state === 'urgent').length;
      out.push(kpi('تجديدات خلال 7 أيام', d.renewals.due_total_7, d.renewals.due_total_7 ? 'warn' : 'good', 'شرائح · باقات · اشتراكات', '#/renewals?days=7'));
      out.push(kpi('خطوط رصيدها عاجل', urgent, urgent ? 'bad' : 'good', `من ${d.renewals.primary_lines.length} خطوط أساسية`, '#/lines'));
    }
    if (d.journey) out.push(kpi('إخلاءات متوقفة على عهدة', d.journey.offboarding_blocked, d.journey.offboarding_blocked ? 'bad' : 'good', `${d.journey.offboarding_open} إخلاء مفتوح`, '#/offboarding'));
    if (d.assets) { const tot = Object.values(d.assets.cards).reduce((a, c) => a + c.total, 0); out.push(kpi('إجمالي الأصول', tot, 'info', `${d.assets.in_maintenance} في الصيانة`, '#/assets')); }
    if (d.security && d.security.backup_overdue != null) out.push(kpi('نسخ WhatsApp متأخرة', d.security.backup_overdue, d.security.backup_overdue ? 'warn' : 'good', 'الشرائح السعودية', '#/security'));
    if (d.finance) { const p = mainCur(d.finance.paid); out.push(kpi('مدفوع هذا الشهر', p ? num(p[1]) : 0, 'info', p ? p[0] : 'لا مدفوعات', '#/budgets')); }
    return `<div class="kpi-strip">${out.join('')}</div>`;
  }

  function alertsPanel(al) {
    const body = al.total ? `<div class="alert-list">${al.top.map(a => `<a class="alert-row ${a.severity}" href="#/${esc(a.link)}"><span class="bar"></span><span>${App.badge(SEV[a.severity][0], a.severity === 'critical' ? 'bad' : a.severity === 'high' ? 'warn' : 'info')}</span><span>${esc(a.title)}</span><span class="mono small muted" dir="ltr">${esc(a.ref || '')}</span></a>`).join('')}</div>${al.total > al.top.length ? `<p class="small"><a href="#/alerts">عرض كل التنبيهات (${al.total})</a></p>` : ''}`
      : '<div class="notice">لا توجد تنبيهات حالية بناءً على البيانات المسجلة.</div>';
    const sev = al.by_severity || {};
    const donut = App.donut([{ label: 'حرج', value: sev.critical || 0, color: 'var(--bad)', href: '#/alerts' }, { label: 'مرتفع', value: sev.high || 0, color: 'var(--c-repair)', href: '#/alerts' }, { label: 'متوسط', value: sev.medium || 0, color: 'var(--s1)', href: '#/alerts' }], String(al.total), 'تنبيه');
    return `<div class="dash-row"><div class="panel span-8"><div class="panel-head"><h2 class="t">التنبيهات الحرجة أولًا</h2></div>${body}</div><div class="panel span-4"><div class="panel-head"><h2 class="t">توزيع التنبيهات</h2></div>${donut}</div></div>`;
  }

  function renewalsRow(r) {
    const lines = r.primary_lines.length ? App.worstFirst(r.primary_lines).map(l => `<a class="gauge-card" href="#/line/${l.id}"><div class="top"><b>${esc(l.name || l.code)}</b>${App.badge(USAGE[l.usage_state][0], USAGE[l.usage_state][1])}</div>
        ${App.gauge({ remaining: l.remaining_gb, quota: l.quota_gb, warn: l.warn_gb, critical: l.critical_gb, state: l.usage_state, label: l.name || l.code })}
        <span class="small">الباقة: ${due(l.internet)}</span><span class="small muted">آخر قراءة ${fmtDT(l.last_reading_at)}</span></a>`).join('')
      : '<div class="muted small">لم تُحدد خطوط أساسية — علّم «خط أساسي» على الخطوط من صفحة الإنترنت والأرضي.</div>';
    const map = { sims_SA: 'شرائح سعودية', sims_EG: 'شرائح مصرية', internet_landline: 'إنترنت / أرضي' };
    const d7 = Object.fromEntries(Object.entries(r.due_within_7_days || {}).map(([k, v]) => [map[k] || k, v]));
    return `<div class="dash-row"><div class="panel span-8"><div class="panel-head"><h2 class="t">الإنترنت — الرصيد المتبقي</h2><a class="small" href="#/lines">كل الخطوط (${r.lines_total})</a></div><div class="gauge-grid">${lines}</div></div>
      <div class="panel span-4"><div class="panel-head"><h2 class="t">تجديدات خلال 7 أيام</h2><a class="small" href="#/renewals?days=7">مركز التجديدات</a></div>${Object.keys(d7).length ? App.bars(d7, { href: () => '#/renewals?days=7', color: 'var(--c-repair)' }) : '<div class="notice">لا توجد تجديدات قريبة.</div>'}</div></div>`;
  }

  function assetsRow(a) {
    const AR = a.allocation_ar, all = {};
    Object.values(a.cards).forEach(c => Object.entries(c.allocation).forEach(([k, v]) => { all[k] = (all[k] || 0) + v; }));
    const tot = Object.values(all).reduce((x, y) => x + y, 0);
    const parts = Object.entries(all).map(([k, v]) => ({ label: AR[k] || k, value: v, color: App.color(k), href: '#/assets?allocation=' + k }));
    const minis = ['desktops', 'laptops', 'mobiles', 'small'].map(k => { const c = a.cards[k]; return `<a href="#/assets?group=${k}">${App.donut(Object.entries(c.allocation).map(([s, v]) => ({ label: AR[s] || s, value: v, color: App.color(s) })), String(c.total), '', { noLegend: true })}<b>${esc(c.label)}</b><span class="small muted">${c.damaged ? `<span class="t-bad">${c.damaged} تالف</span>` : 'لا يوجد تالف'}</span></a>`; }).join('');
    return `<div class="dash-row"><div class="panel span-4"><div class="panel-head"><h2 class="t">الأصول حسب حالة التخصيص</h2></div>${App.donut(parts, String(tot), 'أصل')}${a.condition_unknown ? `<p class="small muted">${a.condition_unknown} أصل حالته الفنية غير محددة</p>` : ''}</div>
      <div class="panel span-8"><div class="panel-head"><h2 class="t">الأصول حسب الفئة</h2><a class="small" href="#/assets">سجل الأصول</a></div><div class="mini-donuts">${minis}</div><h3 style="margin:14px 0 6px;font-size:13px">التوزيع على الأقسام</h3>${App.bars(a.by_department, { href: () => '#/assets' })}</div></div>`;
  }

  function financeAndPeopleRow(d) {
    const cells = [];
    if (d.finance) {
      const cur = (mainCur(Object.assign({}, ...d.finance.monthly.map(m => m.committed), ...d.finance.monthly.map(m => m.paid))) || ['EGP'])[0];
      const groups = d.finance.monthly.map(m => ({ label: m.month.slice(5) + '/' + m.month.slice(2, 4), values: { 'متوقع/ملتزم': Number((m.committed || {})[cur] || 0), 'مدفوع': Number((m.paid || {})[cur] || 0) } }));
      cells.push(`<div class="panel span-7"><div class="panel-head"><h2 class="t">مصروف IT — آخر 6 شهور (${esc(cur)})</h2><a class="small" href="#/budgets">التقارير المالية</a></div>${App.columns(groups, [{ name: 'متوقع/ملتزم', color: 'var(--s1)' }, { name: 'مدفوع', color: 'var(--c-used)' }], { unit: cur, label: 'مصروف IT الشهري' })}
        <div class="stat-col" style="margin-top:8px"><div><span class="small">طلبات شراء مفتوحة</span><b><a href="#/purchases">${d.finance.open_requests}</a></b></div>${['committed', 'invoiced', 'paid'].map(b => `<div><span class="small">${{ committed: 'متوقع الشهر', invoiced: 'مفوتر الشهر', paid: 'مدفوع الشهر' }[b]}</span><b>${Object.entries(d.finance[b]).map(([c, v]) => num(v) + ' ' + c).join(' · ') || '0'}</b></div>`).join('')}</div></div>`);
    }
    if (d.journey || d.tasks) {
      const j = d.journey || {}, t = d.tasks || {};
      const funnel = j.applications ? App.bars(j.applications, { href: () => '#/recruitment', color: 'var(--s1)' }) : '';
      cells.push(`<div class="panel span-${d.finance ? 5 : 12}"><div class="panel-head"><h2 class="t">رحلة الموظف والمهام</h2></div>
        <div class="stat-col"><div><span class="small">تعيينات جارية</span><b><a href="#/onboarding">${j.onboarding_open ?? '—'}</a></b></div><div><span class="small">تعيينات متأخرة</span><b class="${j.onboarding_late ? 't-bad' : ''}">${j.onboarding_late ?? '—'}</b></div>
        <div><span class="small">مهام IT مفتوحة</span><b>${t.open ?? '—'}</b></div><div><span class="small">تنتظر اعتماد</span><b>${t.awaiting_approval ?? '—'}</b></div></div>
        ${funnel ? `<h3 style="margin:14px 0 6px;font-size:13px">طلبات التوظيف حسب المرحلة</h3>${funnel}` : ''}</div>`);
    }
    return cells.length ? `<div class="dash-row">${cells.join('')}</div>` : '';
  }

  function securityRow(s) {
    if (!Object.keys(s || {}).length) return '';
    const tiles = [];
    if (s.backup_overdue != null) tiles.push(kpi('نسخ احتياطي متأخر', s.backup_overdue, s.backup_overdue ? 'bad' : 'good', `${s.backup_unknown} بدون بيانات`, '#/security'));
    if (s.integrations) { const h = s.integrations.healthy || 0, tot = Object.values(s.integrations).reduce((a, b) => a + b, 0); tiles.push(kpi('موصلات متصلة', `${h} / ${tot}`, h ? 'good' : 'warn', 'Google Sheets / Drive / Forms', '#/integrations')); }
    if (s.worker) tiles.push(kpi('الـ Worker', s.worker.state === 'running' ? 'يعمل' : s.worker.state === 'never' ? 'لم يعمل' : 'متوقف', s.worker.state === 'running' ? 'good' : 'warn', s.worker.last_beat ? 'آخر نبضة ' + fmtDT(s.worker.last_beat) : '', '#/integrations?tab=worker'));
    return `<div class="panel"><div class="panel-head"><h2 class="t">الأمان والتكامل</h2></div><div class="kpi-strip" style="margin:0">${tiles.join('')}</div></div>`;
  }

  App.pages.dashboard = async el => {
    const d = await App.get('/api/dashboard' + App.qs({ company_id: App.scopeCompany() }));
    el.innerHTML = App.head('Dashboard', `لوحة متابعة قسم تقنية المعلومات · آخر تحديث ${fmtDT(d.generated_at)}`, '<a class="btn" href="#/excel">📥 استيراد / تصدير Excel</a><button class="btn ghost" onclick="App.render()">↻ تحديث</button>', 'assets') +
      kpiStrip(d) + alertsPanel(d.alerts) + (d.renewals ? renewalsRow(d.renewals) : '') + (d.assets ? assetsRow(d.assets) : '') + financeAndPeopleRow(d) + securityRow(d.security);
  };

  App.pages.alerts = async el => {
    const al = await App.get('/api/alerts');
    el.innerHTML = App.head('كل التنبيهات', 'محسوبة لحظيًا من بيانات الوحدات حسب صلاحيتك') + `<div class="panel">${al.length ? `<div class="alert-list">${al.map(a => `<a class="alert-row ${a.severity}" href="#/${esc(a.link)}"><span class="bar"></span><span>${App.badge(SEV[a.severity][0], a.severity === 'critical' ? 'bad' : a.severity === 'high' ? 'warn' : 'info')}</span><span>${esc(a.title)}</span><span class="mono small muted" dir="ltr">${esc(a.ref || '')}</span></a>`).join('')}</div>` : App.emptyBox('لا توجد تنبيهات')}</div>`;
  };
})(window.App);
