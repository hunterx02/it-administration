/* M01 — Dashboard: critical alerts first, then visual blocks. Every number comes from /api/dashboard
   (derived from module tables). Missing data -> empty/incomplete state, never fake "healthy". */
(function (App) {
  'use strict';
  const { esc, num, fmtDate, fmtDT } = App;
  const SEV = { critical: ['حرج', '⛔'], high: ['عالي', '⚠️'], medium: ['متوسط', 'ℹ️'] };
  const USAGE = { normal: ['طبيعي', 'good'], prepare: ['جهز رصيد', 'warn'], urgent: ['تجديد عاجل', 'bad'], incomplete: ['لا توجد قراءة', 'muted'], thresholds_missing: ['حدد العتبات', 'muted'] };
  const DUE = { ok: ['منتظم', 'good'], due_soon: ['قرب التجديد', 'warn'], overdue: ['متأخر', 'bad'], incomplete: ['بيانات ناقصة', 'muted'] };
  App.USAGE = USAGE; App.DUE = DUE;
  const due = i => i ? `${App.badge(DUE[i.due_state][0], DUE[i.due_state][1])} <span class="small">${i.next_due ? fmtDate(i.next_due) + ' (' + i.days_left + ' يوم)' : ''}</span>` : '';

  function alertsBlock(al) {
    if (!al.total) return '<div class="notice">لا توجد تنبيهات حالية بناءً على البيانات المسجلة.</div>';
    return `<div class="alerts-strip" role="list">${al.top.map(a => `<a role="listitem" class="alert-card ${a.severity}" href="#/${esc(a.link)}"><span class="ic" aria-hidden="true">${SEV[a.severity][1]}</span><span><small>${SEV[a.severity][0]}</small>${esc(a.title)}${a.ref ? `<span class="muted small mono" dir="ltr"> ${esc(a.ref)}</span>` : ''}</span></a>`).join('')}
      ${al.total > al.top.length ? `<a class="alert-card" href="#/alerts"><b>+${al.total - al.top.length}</b>&nbsp;عرض كل التنبيهات</a>` : ''}</div>`;
  }

  function renewalsBlock(r) {
    const lines = r.primary_lines.length ? r.primary_lines.map(l => {
      const u = USAGE[l.usage_state];
      return `<a class="line-card" href="#/line/${l.id}" style="color:inherit"><div class="top"><b>${esc(l.name || l.code)}</b>${App.badge(u[0], u[1])}</div>
        ${App.gauge({ remaining: l.remaining_gb, quota: l.quota_gb, warn: l.warn_gb, critical: l.critical_gb, state: l.usage_state, label: l.name || l.code })}
        ${App.spark((l.spark || []).map(s => s.remaining))}
        <span class="small muted">آخر قراءة: ${fmtDT(l.last_reading_at)} ${l.source ? '· ' + esc(l.source) : ''}</span>
        <span class="small">باقة الإنترنت: ${due(l.internet)}</span><span class="small">الأرضي: ${due(l.landline)}</span></a>`;
    }).join('') : `<div class="muted small">لا توجد خطوط أساسية محددة (علّم «خط أساسي» على الخطوط الثلاثة من صفحة الإنترنت والأرضي).</div>`;
    const map = { sims_SA: 'شرائح سعودية', sims_EG: 'شرائح مصرية', internet_landline: 'إنترنت / أرضي' };
    const d7 = Object.entries(r.due_within_7_days || {}).map(([k, v]) => [map[k] || k, v]);
    return `<div class="panel block-wide"><div class="panel-head"><h2>مركز التجديدات</h2><a class="small" href="#/renewals?days=7"><b>${r.due_total_7}</b> عنصر يحتاج تجديد خلال 7 أيام</a></div>
      <div class="cards4">${lines}</div>${d7.length ? `<h3 style="margin-top:12px">قرب التجديد (7 أيام)</h3>${App.bars(Object.fromEntries(d7), { href: () => '#/renewals?days=7', color: 'var(--c-repair)' })}` : ''}</div>`;
  }

  function assetsBlock(a) {
    const AR = a.allocation_ar, card = (key, c, cats) => {
      const parts = Object.entries(c.allocation).map(([k, v]) => ({ label: AR[k] || k, value: v, color: App.color(k), href: `#/assets?allocation=${k}${cats ? '&group=' + key : ''}` }));
      return `<div class="acard2"><h3><span>${esc(c.label)}</span><b>${c.total}</b></h3>${App.donut(parts, String(c.total), 'إجمالي')}
        ${c.damaged ? `<a class="small t-bad" href="#/assets?condition=damaged&group=${key}">${c.damaged} تالف (حالة فنية)</a>` : ''}${c.sims_assigned != null ? `<span class="small">شرائح مسلّمة: <b>${c.sims_assigned}</b></span>` : ''}</div>`;
    };
    return `<div class="panel block-wide"><div class="panel-head"><h2>الأصول والعهد</h2><span class="muted small">${a.condition_unknown ? `${a.condition_unknown} أصل حالته الفنية غير محددة · ` : ''}${a.in_maintenance} في الصيانة</span></div>
      <div class="cards4">${['desktops', 'laptops', 'mobiles', 'small'].map(k => card(k, a.cards[k], a.cards[k].categories)).join('')}</div>
      <h3 style="margin-top:14px">التوزيع على الأقسام</h3>${App.bars(a.by_department, { href: () => '#/assets' })}</div>`;
  }

  function tasksBlock(t) {
    return `<div class="panel"><div class="panel-head"><h2>المهام والمتابعة</h2><a class="small" href="#/onboarding">التعيينات</a></div>
      <div class="stat-row"><div><span class="small">مفتوحة</span><b>${t.open}</b></div><div><span class="small">متأخرة</span><b class="${t.late ? 't-bad' : ''}">${t.late}</b></div><div><span class="small">تنتظر اعتماد</span><b>${t.awaiting_approval}</b></div><div><span class="small">اكتملت اليوم</span><b>${t.done_today}</b></div></div>
      ${t.top.length ? `<ul class="small" style="padding-inline-start:18px">${t.top.map(x => `<li><a href="#/onboard/${x.onboarding_id}">${esc(x.description)}</a> · ${x.status === 'done' ? 'تنتظر اعتماد' : 'مفتوحة'}${x.due_date ? ' · ' + fmtDate(x.due_date) : ''}</li>`).join('')}</ul>` : ''}
      <p class="muted small">${esc(t.source_note)}</p></div>`;
  }

  function journeyBlock(j) {
    return `<div class="panel"><div class="panel-head"><h2>التعيينات والإخلاءات</h2></div>
      <div class="stat-row"><div><span class="small">تعيينات جارية</span><b><a href="#/onboarding">${j.onboarding_open}</a></b></div><div><span class="small">تعيينات متأخرة</span><b class="${j.onboarding_late ? 't-bad' : ''}">${j.onboarding_late}</b></div>
      <div><span class="small">إخلاءات جارية</span><b><a href="#/offboarding">${j.offboarding_open}</a></b></div><div><span class="small">متوقفة بسبب عهدة</span><b class="${j.offboarding_blocked ? 't-bad' : ''}">${j.offboarding_blocked}</b></div></div>
      <h3 style="margin-top:12px">طلبات التوظيف حسب المرحلة</h3>${App.bars(j.applications, { href: () => '#/recruitment' })}</div>`;
  }

  function financeBlock(f) {
    const row = (label, o) => `<div><span class="small">${label}</span><b>${Object.entries(o).map(([c, v]) => num(v) + ' ' + c).join(' · ') || '0'}</b></div>`;
    return `<div class="panel"><div class="panel-head"><h2>التكاليف — الشهر الحالي</h2><a class="small" href="#/budgets">التقارير المالية</a></div>
      <div class="stat-row">${row('متوقع/ملتزم', f.committed)}${row('مفوتر', f.invoiced)}${row('مدفوع', f.paid)}</div>
      <p class="small">طلبات شراء مفتوحة: <a href="#/purchases"><b>${f.open_requests}</b></a></p><p class="muted small">العملات معروضة منفصلة — لا تحويل تلقائي.</p></div>`;
  }

  function securityBlock(s) {
    const items = [];
    if (s.backup_overdue != null) items.push(`<li>نسخ WhatsApp متأخر: <b class="${s.backup_overdue ? 't-bad' : ''}">${s.backup_overdue}</b> · بدون قراءة: ${s.backup_unknown}</li>`);
    if (s.integrations) items.push(`<li>الربط: ${Object.entries(s.integrations).map(([k, v]) => esc(k) + ' ' + v).join(' · ')}</li>`);
    if (s.worker) items.push(`<li>الـ Worker: ${App.badge(s.worker.state_ar, s.worker.state === 'running' ? 'good' : 'warn')} ${s.worker.last_beat ? '· آخر نبضة ' + fmtDT(s.worker.last_beat) : ''}</li>`);
    return `<div class="panel"><div class="panel-head"><h2>الأمان والنسخ الاحتياطي</h2><a class="small" href="#/security">التفاصيل</a></div>${items.length ? `<ul class="small" style="padding-inline-start:18px">${items.join('')}</ul>` : '<p class="muted small">لا توجد قراءة</p>'}</div>`;
  }

  App.pages.dashboard = async el => {
    const d = await App.get('/api/dashboard' + App.qs({ company_id: App.scopeCompany() }));
    el.innerHTML = App.head('نظرة عامة', `آخر تحديث ${fmtDT(d.generated_at)}`, '', 'assets') +
      alertsBlock(d.alerts) + (d.renewals ? renewalsBlock(d.renewals) : '') + (d.assets ? assetsBlock(d.assets) : '') +
      `<div class="blocks">${d.tasks ? tasksBlock(d.tasks) : ''}${d.journey ? journeyBlock(d.journey) : ''}${d.finance ? financeBlock(d.finance) : ''}${Object.keys(d.security || {}).length ? securityBlock(d.security) : ''}</div>`;
  };

  App.pages.alerts = async el => {
    const al = await App.get('/api/alerts');
    el.innerHTML = App.head('كل التنبيهات', 'محسوبة لحظيًا من بيانات الوحدات حسب صلاحيتك') + App.table(al, [
      { label: 'الأولوية', render: a => App.badge(SEV[a.severity][0], a.severity === 'critical' ? 'bad' : a.severity === 'high' ? 'warn' : 'info') },
      { label: 'التنبيه', render: a => `<a href="#/${esc(a.link)}">${esc(a.title)}</a>` }, { label: 'المرجع', render: a => `<span class="mono" dir="ltr">${esc(a.ref)}</span>` }], { empty: 'لا توجد تنبيهات' });
  };
})(window.App);
