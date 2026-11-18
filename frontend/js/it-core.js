/* =========================================================
   it-core.js — محرك نظام تقنية المعلومات (ITX)
   مبني على 4 وثائق: هيكل النظام وسير الداتا · الداشبورد النهائي ·
   سيكل التعيين والإخلاء (الإخلاء بالعكس) · Recruitment & Onboarding Master Workflow

   • الأكواد الثابتة: DEV / MOB / ACC / SIM-SA / SIM-EG / NET / TOOL / LIC / DOM / BKP / CON
                      EMP / CAN / REQ / ONB / OFF / DOC — كل صف في الشيت بكود ثابت
   • التعيين 0–8 والإخلاء X1–X9: كل مرحلة إخلاء بتلغي مرحلة التعيين اللي قصادها
   • المستندات: الإقرار الموحد + ملاحق العهدة ← كشف خلو الطرف ← إقرار تسليم العهدة
   • التنبيهات: جوه النظام + Telegram — مرة واحدة لكل حدث (alertsSent)
   • المزامنة: Google Sheets = الداتا الأساسية للأصول؛ الحركات من النظام بس (Audit Log)
   • مفيش باسوردات في النظام: رابط الـ Vault بس
   ========================================================= */
(function (global) {
  'use strict';
  const U = global.U, C = global.Core, UI = global.UI, Fin = global.Fin = global.Fin || {};
  const ITX = global.ITX = {};
  const today = () => U.today();
  const nowISO = () => new Date().toISOString();
  const esc = s => U.esc(s);

  // ================= الإعداد =================
  const COLLS = ['requisitions', 'candidates', 'cycles', 'itDocs', 'itLog', 'consumables', 'netPackages', 'tools', 'domains', 'backups', 'usageSnapshots', 'stocktakes', 'syncErrors', 'alertsSent', 'assets', 'assetLog', 'lines', 'mailboxes', 'moves', 'subscriptions', 'team'];
  const DEF_SETTINGS = {
    companies: [{ name: (global.BRAND || {}).company || 'SEG', branches: (global.BRAND || {}).branches || ['الفرع الرئيسي — الرياض', 'مكتب القاهرة', 'المخزن'] }],
    sheetId: '', syncEvery: 5, lastSync: '', lastSyncOk: true,
    sheetTabs: { assets: 'الأجهزة', lines: 'الشرائح', tools: 'التولات', netPackages: 'الباقات', subscriptions: 'التراخيص', domains: 'الدومينات', consumables: 'الإكسسوارات', team: 'الموظفين', usage: 'الاستهلاك' },
    telegramToken: '', telegramChat: '', vaultUrl: '', replaceYears: 4, retentionDays: 90, mailRetentionDays: 90,
    onboardBase: '', // رابط صفحة apply.html على السيرفر (للـ QR والرابط الشخصي)
  };
  ITX.ensure = function () {
    if (!global.DB) return;
    COLLS.forEach(k => { if (!Array.isArray(DB[k])) DB[k] = []; });
    DB.itSettings = Object.assign({}, DEF_SETTINGS, DB.itSettings || {});
    DB.itSettings.sheetTabs = Object.assign({}, DEF_SETTINGS.sheetTabs, DB.itSettings.sheetTabs || {});
  };
  const S = () => DB.itSettings || DEF_SETTINGS;
  ITX.settings = S;

  // ================= الأكواد الثابتة =================
  const PAD = { DEV: 4, MOB: 3, ACC: 3, 'SIM-SA': 3, 'SIM-EG': 3, NET: 3, TOOL: 3, LIC: 3, DOM: 3, BKP: 2, CON: 3, EMP: 3, CAN: 4, REQ: 4, ONB: 4, OFF: 4, DOC: 5, STK: 3 };
  const ASSET_PFX = { mobile: 'MOB', accessory: 'ACC' }; // الباقي DEV
  ITX.nextCode = function (pfx) {
    const k = 'c_' + pfx; DB.seq[k] = (DB.seq[k] || 0) + 1;
    return pfx + '-' + String(DB.seq[k]).padStart(PAD[pfx] || 3, '0');
  };
  ITX.assetCode = type => ITX.nextCode(ASSET_PFX[type] || 'DEV');
  // تسجيل كود موجود (من الشيت) بحيث العداد ميتكررش
  ITX.bumpSeq = function (code) {
    const m = String(code || '').match(/^([A-Z]+(?:-[A-Z]{2})?)-(\d+)$/); if (!m) return;
    const k = 'c_' + m[1]; DB.seq[k] = Math.max(DB.seq[k] || 0, +m[2]);
  };

  // ================= أدوات مشتركة =================
  ITX.staff = id => (DB.team || []).find(m => m.id === id) || {};
  ITX.nm = id => ITX.staff(id).name || '';
  ITX.me = () => ((global.Auth && global.Auth.current()) || {});
  ITX.role = () => ITX.me().role || 'admin';
  ITX.sar = (amount, cur) => U.round(U.num(amount) * (global.Ledger ? global.Ledger.defaultRate(cur || 'SAR') : 1));
  ITX.egpRate = () => { const c = (DB.settings.currencies || []).find(x => x.code === 'EGP'); return c && c.rate ? c.rate : 0.078; };
  ITX.toCur = (sarAmount, cur) => cur === 'EGP' ? U.round(sarAmount / ITX.egpRate()) : U.round(sarAmount);
  ITX.days = date => date ? U.daysBetween(today(), date) : null;
  ITX.inDays = n => n == null ? '—' : n < -1 ? 'فات من ' + -n + ' يوم' : n === -1 ? 'امبارح' : n === 0 ? 'النهارده' : n === 1 ? 'بكرة' : n === 2 ? 'بعد يومين' : 'بعد ' + n + (n <= 10 ? ' أيام' : ' يوم');
  ITX.ago = iso => { if (!iso) return '—'; const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000); return m < 1 ? 'دلوقتي' : m < 60 ? 'من ' + m + ' دقيقة' : m < 1440 ? 'من ' + Math.round(m / 60) + ' ساعة' : 'من ' + Math.round(m / 1440) + ' يوم'; };
  ITX.time = iso => { if (!iso) return ''; const d = new Date(iso), t = today(), day = U.isoDate(d); const hm = d.toLocaleTimeString('ar-EG', { hour: 'numeric', minute: '2-digit' }); return (day === t ? 'النهارده' : day === U.addDays(t, -1) ? 'امبارح' : day) + ' ' + hm; };
  ITX.chip = (txt, cls) => `<span class="badge b-${cls || 'muted'}">${txt}</span>`;
  ITX.typeName = a => ((Fin.TYPES || {})[a.type] || ['جهاز'])[0];
  ITX.deptOf = a => a.holderId ? (ITX.staff(a.holderId).dept || a.dept || 'المخزن العام') : (a.dept || 'المخزن العام');
  ITX.companyOf = a => a.company || (a.holderId ? ITX.staff(a.holderId).company : '') || ((S().companies[0] || {}).name || '');
  ITX.branchOf = a => a.branch || (a.holderId ? ITX.staff(a.holderId).branch : '') || '';
  ITX.allDepts = () => Array.from(new Set((Fin.DEPTS || []).concat((DB.team || []).map(m => m.dept).filter(Boolean))));

  // ---------- فلتر الشركة / الفرع / القسم (أعلى الداشبورد) ----------
  ITX.scope = (() => { try { return JSON.parse(sessionStorage.getItem('itx_scope') || '{}'); } catch (e) { return {}; } })();
  ITX.setScope = function (k, v) { ITX.scope[k] = v; try { sessionStorage.setItem('itx_scope', JSON.stringify(ITX.scope)); } catch (e) {} };
  ITX.inScope = function (o, kind) {
    const sc = ITX.scope; if (!sc.company && !sc.branch && !sc.dept) return true;
    let co, br, dp;
    if (kind === 'emp') { co = o.company; br = o.branch; dp = o.dept; }
    else if (kind === 'asset' || kind === 'line') { co = ITX.companyOf(o); br = ITX.branchOf(o); dp = ITX.deptOf(o); }
    else { co = o.company; br = o.branch; dp = o.dept; }
    return (!sc.company || !co || co === sc.company) && (!sc.branch || !br || br === sc.branch) && (!sc.dept || dp === sc.dept);
  };
  ITX.scopeFields = function (a) {
    const cos = S().companies, co = a.company || (cos[0] || {}).name || '';
    const brs = ((cos.find(c => c.name === co) || cos[0] || {}).branches || []);
    return UI.field({ label: 'الشركة', name: 'company', type: 'select', options: cos.map(c => c.name), value: co })
      + UI.field({ label: 'الفرع', name: 'branch', type: 'select', options: [{ value: '', label: '—' }].concat(brs), value: a.branch || '' })
      + UI.field({ label: 'القسم (لو مش مع موظف)', name: 'dept', type: 'select', options: [{ value: '', label: 'المخزن العام' }].concat(ITX.allDepts()), value: a.dept || '' });
  };

  // ================= سجل الحركات (Audit Log) =================
  ITX.event = function (text, o) {
    o = o || {};
    DB.itLog.unshift({ id: U.uid(), at: nowISO(), by: o.by || ITX.me().name || 'النظام', text, ref: o.ref || '', empId: o.empId || '', cycleId: o.cycleId || '', kind: o.kind || '' });
    if (DB.itLog.length > 3000) DB.itLog.length = 3000;
    C.audit('حركة IT', o.kind || 'حدث', o.ref || '', text);
    global.Store.save();
  };
  const alog = (assetId, kind, extra) => C.upsert('assetLog', Object.assign({ id: U.uid(), assetId, kind, date: today() }, extra || {}), 'سجل عهدة');
  ITX.alog = alog;

  // ================= المراحل =================
  ITX.ON_STAGES = [
    { n: 0, t: 'طلب الاحتياج', en: 'Requisition' }, { n: 1, t: 'التقديم والتسجيل', en: 'Application & Registration' },
    { n: 2, t: 'المقابلات والتقييم', en: 'Interviews & Evaluations' }, { n: 3, t: 'قرار التوظيف والعرض', en: 'Hiring Decision' },
    { n: 4, t: 'المستندات قبل العمل', en: 'Digital Pre-Onboarding' }, { n: 5, t: 'تجهيز IT قبل المباشرة', en: 'IT Preparation' },
    { n: 6, t: 'يوم المباشرة وتسليم العهد', en: 'Joining Day & IT Handover' }, { n: 7, t: 'الإقرار الموحد والأرشفة', en: 'Unified Acknowledgment' },
    { n: 8, t: 'موظف نشط', en: 'Active Employee' },
  ];
  ITX.OFF_STAGES = [
    { n: 1, t: 'طلب الإخلاء', undo: 8 }, { n: 2, t: 'كشف خلو الطرف', undo: 7 }, { n: 3, t: 'آخر يوم واستلام العهد', undo: 6 },
    { n: 4, t: 'فك التجهيز والمسح', undo: 5 }, { n: 5, t: 'مستندات الخروج', undo: 4 }, { n: 6, t: 'التسوية النهائية', undo: 3 },
    { n: 7, t: 'مقابلة الخروج', undo: 2 }, { n: 8, t: 'الأرشفة والاحتفاظ', undo: 1 }, { n: 9, t: 'قرار الوظيفة', undo: 0 },
  ];
  ITX.undoneBy = n => ITX.OFF_STAGES.find(s => s.undo === n); // مرحلة الإخلاء اللي بتلغي مرحلة التعيين n
  // اللي اتعمل في التعيين ← بيتلغي في الإخلاء
  ITX.REVERSE = [
    ['حجز الجهاز (خالي ← محجوز)', 'رجوعه المخزن خالي بعد المسح', 'X4'], ['إنشاء الإيميل والحسابات', 'قفل ثم أرشفة ثم حذف', 'X3'],
    ['تسجيل MDM و MFA', 'حذف من MDM وإلغاء MFA', 'X3 / X4'], ['تسليم الشريحة', 'استلامها أو إيقافها', 'X3'],
    ['تخصيص التولات والتراخيص', 'نقلها أو إرجاعها', 'X4'], ['الإقرار الموحد', 'إقرار تسليم العهدة', 'X3'],
    ['CAN ← EMP', 'EMP يتقفل Exited', 'X8'], ['فتح REQ', 'إلغاء الوظيفة أو REQ لبديل', 'X9'],
  ];
  // حالات المرشح / الموظف (Dashboard States — صفحة 9 من وثيقة التوظيف)
  ITX.CSTATUS = {
    applied: ['تم التقديم', 'Applied', 'info'], interview: ['تحديد المقابلة', 'Interview Scheduled', 'info'], hrEval: ['تقييم HR', 'HR Evaluated', 'info'],
    techEval: ['التقييم الثاني', 'Technical Evaluated', 'info'], accepted: ['مقبول', 'Accepted', 'good'], rejected: ['مرفوض', 'Not Accepted', 'bad'], pending: ['معلّق', 'Pending', 'warn'],
    docs: ['مراجعة المستندات', 'Documents Pending', 'warn'], joining: ['تاريخ المباشرة', 'Joining Scheduled', 'info'], handover: ['تسليم العهد', 'IT Handover', 'warn'],
    signature: ['توقيع الإقرار', 'Signature Pending', 'warn'], active: ['اكتمال التعيين', 'Active Employee', 'good'],
  };
  ITX.cBadge = s => { const x = ITX.CSTATUS[s] || ITX.CSTATUS.applied; return `<span class="badge b-${x[2]}" title="${x[1]}">${x[0]}</span>`; };
  // مستندات Pre-Onboarding (كل مستند بإصدار؛ والتأكيد بيتسجل بالوقت وهوية المرشح والإصدار)
  ITX.PRE_DOCS = [
    { key: 'personal', t: 'البيانات الشخصية', en: 'Personal Information', ver: 'v1', need: ['read'] },
    { key: 'contract', t: 'العقود والمستندات الوظيفية', en: 'Employment Documents', ver: 'v3', need: ['read'] },
    { key: 'nda', t: 'اتفاقية عدم الإفصاح والسرية (NDA)', en: 'NDA', ver: 'v2', need: ['read', 'agree'] },
    { key: 'policy', t: 'اللائحة الداخلية للشركة', en: 'Internal Regulations', ver: 'v5', need: ['read', 'agree'] },
  ];
  ITX.MARK = { read: 'اطلعت على', agree: 'وافقت على', received: 'استلمت فعليًا' };

  // ================= العهدة (asset_assignments) =================
  // كل اللي مع الموظف: أصول (أجهزة + إكسسوارات + شرائح) وحسابات (تولات + تراخيص + إيميل)
  ITX.custody = function (empId) {
    const out = [];
    DB.assets.filter(a => a.holderId === empId && a.status === 'assigned').forEach(a => out.push({ t: 'asset', id: a.id, code: a.tag, type: ITX.typeName(a), serial: a.serial || '—', label: ITX.typeName(a) + ' ' + (Fin.assetName ? Fin.assetName(a) : ''), group: 'asset' }));
    DB.lines.filter(l => l.holderId === empId && l.status === 'assigned').forEach(l => out.push({ t: 'line', id: l.id, code: l.code || 'SIM', type: l.country === 'SA' ? 'شريحة سعودية' : 'شريحة مصرية', serial: Fin.fmtPhone ? Fin.fmtPhone(l.number) : l.number, label: 'شريحة ' + (l.carrier || ''), group: 'asset' }));
    DB.tools.filter(t => (t.users || []).includes(empId) && t.status !== 'cancelled').forEach(t => out.push({ t: 'tool', id: t.id, code: t.code, type: 'حساب تول', serial: t.account || '', label: t.name, group: 'account' }));
    DB.subscriptions.filter(s => (s.users || []).includes(empId) && s.status !== 'cancelled').forEach(s => out.push({ t: 'sub', id: s.id, code: s.code || 'LIC', type: 'ترخيص / اشتراك', serial: s.loginEmail || '', label: s.name, group: 'account' }));
    DB.mailboxes.filter(b => b.holderId === empId && b.status === 'active').forEach(b => out.push({ t: 'mail', id: b.id, code: 'MAIL', type: 'إيميل', serial: b.address, label: b.address, group: 'account' }));
    return out;
  };
  ITX.custodyCount = empId => { const c = ITX.custody(empId); return { assets: c.filter(x => x.group === 'asset').length, accounts: c.filter(x => x.group === 'account').length, all: c.length }; };
  // حسابات لسه شغالة لموظف مشي (إيميل نشط / مستخدم نظام / مقعد ترخيص / تول)
  ITX.liveAccounts = function (empId) {
    const s = ITX.staff(empId), n = [];
    DB.mailboxes.filter(b => b.holderId === empId && b.status === 'active').forEach(b => n.push(b.address));
    DB.mailboxes.filter(b => (b.members || []).includes(empId) && b.status === 'active').forEach(b => n.push(b.address + ' (مشترك)'));
    DB.subscriptions.filter(x => (x.users || []).includes(empId) && x.status !== 'cancelled').forEach(x => n.push(x.name));
    DB.tools.filter(x => (x.users || []).includes(empId) && x.status !== 'cancelled').forEach(x => n.push(x.code + ' ' + x.name));
    const u = (DB.users || []).find(x => x.email && s.email && x.email.toLowerCase() === s.email.toLowerCase() && !x.disabled); if (u) n.push('حساب النظام ' + u.email);
    return n;
  };
  ITX.isExited = m => m.active === false || m.status === 'exited';
  ITX.openOff = empId => DB.cycles.find(c => c.type === 'offboard' && c.empId === empId && c.status === 'open');
  // X1: تجميد أي عهد جديدة على الموظف
  ITX.assertNotFrozen = function (empId) {
    const cy = ITX.openOff(empId);
    if (cy) throw new Error('العهد متجمدة على ' + ITX.nm(empId) + ' — عنده إخلاء مفتوح ' + cy.no);
    if (ITX.isExited(ITX.staff(empId))) throw new Error(ITX.nm(empId) + ' ترك العمل');
  };

  // ================= المستندات =================
  ITX.DOCK = {
    unified: ['الإقرار الموحد', 'Unified Employee Acknowledgment'], annex: ['ملحق عهدة', 'Custody Annex'], clearance: ['كشف خلو الطرف', 'Clearance Checklist'],
    returnAck: ['إقرار تسليم العهدة', 'Custody Return Acknowledgment'], wipeCert: ['شهادة مسح داتا', 'Data Wipe Certificate'],
  };
  ITX.DOCST = { draft: ['مسودة', 'muted'], printed: ['اتطبع · مستني التوقيع', 'warn'], signed: ['اتوقّع · مستني المسح الضوئي', 'info'], archived: ['متأرشف', 'good'], waiting: ['مستني آخر يوم', 'warn'] };
  ITX.newDoc = function (kind, empId, o) {
    o = o || {};
    const d = Object.assign({ id: U.uid(), no: ITX.nextCode('DOC'), kind, empId, date: today(), status: kind === 'clearance' ? 'printed' : 'draft', items: [], parents: [], version: 1, by: ITX.me().name || 'النظام', at: nowISO() }, o);
    C.upsert('itDocs', d, ITX.DOCK[kind][0]);
    return d;
  };
  ITX.docsOf = (empId, kind) => DB.itDocs.filter(d => d.empId === empId && (!kind || d.kind === kind));
  ITX.snap = x => ({ t: x.t, id: x.id, code: x.code, type: x.type, serial: x.serial, label: x.label, group: x.group });
  // ملحق عهدة: أي عهدة تتسلم بعد الإقرار الموحد بيتعملها ملحق مربوط بيه
  ITX.annex = function (empId, refs) {
    const uni = ITX.docsOf(empId, 'unified').filter(d => ['signed', 'archived'].includes(d.status)).pop(); if (!uni) return null;
    const all = ITX.custody(empId), items = refs.map(r => all.find(x => x.t === r.t && x.id === r.id)).filter(Boolean).map(x => Object.assign(ITX.snap(x), { mark: 'received' }));
    if (!items.length) return null;
    const d = ITX.newDoc('annex', empId, { parents: [uni.no], items, status: 'printed' });
    ITX.event('اتعمل ملحق عهدة ' + d.no + ' على الإقرار ' + uni.no + ' (' + items.map(i => i.code).join('، ') + ')', { ref: d.no, empId });
    return d;
  };
  ITX.setDocStatus = function (d, st, extra) { Object.assign(d, { status: st }, extra || {}); if (st === 'signed') d.signedAt = today(); if (st === 'archived') d.archivedAt = today(); C.upsert('itDocs', d, ITX.DOCK[d.kind][0]); ITX.event(ITX.DOCK[d.kind][0] + ' ' + d.no + ': ' + ITX.DOCST[st][0], { ref: d.no, empId: d.empId }); };
  // رفع النسخة الموقّعة (سكان) — الملفات الكبيرة محتاجة تخزين سحابي (Firebase Storage) عند الربط
  ITX.uploadScan = function (d, after) {
    const inp = document.createElement('input'); inp.type = 'file'; inp.accept = 'image/*,application/pdf';
    inp.onchange = () => {
      const f = inp.files[0]; if (!f) return;
      const done = data => { ITX.setDocStatus(d, 'archived', { scanName: f.name, scanSize: f.size, scanData: data || '' }); UI.toast('اترفعت النسخة الموقّعة ' + d.no); if (after) after(d); else global.Router.render(); };
      if (f.size > 350 * 1024) { UI.toast('الملف كبير — اتسجّل اسمه بس؛ عند الربط بالسيرفر بيترفع على التخزين السحابي', 'warn'); done(''); return; }
      const r = new FileReader(); r.onload = () => done(r.result); r.readAsDataURL(f);
    };
    inp.click();
  };

  // ---------- طباعة المستندات ----------
  const itemsTable = (items, cols) => `<table class="tbl p-lines"><thead><tr><th>#</th><th>الكود</th><th>النوع</th><th>البيان</th><th>السيريال / الرقم</th>${cols ? cols.map(c => `<th>${c[0]}</th>`).join('') : ''}</tr></thead><tbody>
    ${items.map((x, i) => `<tr><td>${i + 1}</td><td dir="ltr">${esc(x.code)}</td><td>${esc(x.type)}</td><td>${esc(x.label || '')}</td><td dir="ltr">${esc(x.serial || '—')}</td>${cols ? cols.map(c => `<td>${c[1](x)}</td>`).join('') : ''}</tr>`).join('') || '<tr><td colspan="9">لا يوجد</td></tr>'}</tbody></table>`;
  const box = on => `<span class="p-box">${on ? '☑' : '☐'}</span>`;
  ITX.printDoc = function (d) {
    const m = ITX.staff(d.empId), K = ITX.DOCK[d.kind], cand = (DB.candidates || []).find(c => c.empId === d.empId || c.id === d.candidateId) || {};
    let body = '';
    const who = `<div class="p-notes">الموظف: <b>${esc(m.name || cand.name || '')}</b> · كود الموظف: <b dir="ltr">${esc(m.empNo || '—')}</b>${cand.no ? ' · Candidate ID: <b dir="ltr">' + esc(cand.no) + '</b>' : ''} · ${esc([m.role, m.dept, m.company, m.branch].filter(Boolean).join(' — '))}</div>`;
    if (d.kind === 'unified') {
      const cps = cand.checkpoints || [];
      const cp = (k, a) => cps.find(x => x.doc === k && x.action === a);
      const sec = (n, en, ar, html) => `<div class="p-sec"><h4><span>${String(n).padStart(2, '0')}</span> ${en} — ${ar}</h4>${html}</div>`;
      const ck = (k, a) => { const x = cp(k, a); return `${box(!!x)} ${ITX.MARK[a]}${x ? ` <small>(${esc(x.ver)} · ${ITX.time(x.at)})</small>` : ''}`; };
      body = who
        + sec(1, 'Personal Information', 'البيانات الشخصية وصحتها', `<p>${esc(cand.name || m.name || '')} · ${esc(cand.phone || '')} · ${esc(cand.email || m.email || '')} · ${esc(cand.nationalId ? 'رقم الهوية ' + cand.nationalId : '')}</p><p>${ck('personal', 'read')} &nbsp; ${box(false)} أقر بصحتها</p>`)
        + sec(2, 'Employment Documents', 'العقود والمستندات الوظيفية التي تمت مراجعتها', `<p>${ck('contract', 'read')}</p>`)
        + sec(3, 'NDA', 'الاطلاع على اتفاقية عدم الإفصاح والسرية', `<p>${ck('nda', 'read')} &nbsp; ${ck('nda', 'agree')}</p>`)
        + sec(4, 'Internal Regulations', 'قراءة اللائحة الداخلية للشركة', `<p>${ck('policy', 'read')} &nbsp; ${ck('policy', 'agree')}</p>`)
        + sec(5, 'IT Assets & Accounts', 'الأجهزة والعهد والحسابات المسلّمة فعليًا وأرقامها', itemsTable(d.items, [['', x => box(true) + ' ' + ITX.MARK.received]]))
        + sec(6, 'Joining Details', 'القسم والوظيفة وتاريخ مباشرة العمل', `<p>القسم: <b>${esc(m.dept || '')}</b> · الوظيفة: <b>${esc(m.role || '')}</b> · تاريخ المباشرة: <b>${esc(m.startDate || '')}</b> · الفرع: ${esc(m.branch || '')}</p>`)
        + sec(7, 'Final Declaration', 'الإقرار بصحة البيانات والاطلاع والاستلام بحسب كل بند', `<p>أقر أنا الموقّع أدناه بصحة البيانات الواردة، وبأنني اطلعت ووافقت على ما هو مُعلّم أعلاه، واستلمت فعليًا العهد والحسابات الموضحة في البند 05 بحالة سليمة، وأتعهد بالمحافظة عليها وإعادتها عند الطلب أو عند انتهاء العمل.</p>`)
        + sec(8, 'Employee Signature', 'اسم الموظف والتاريخ والتوقيع الورقي', `<div class="p-sign"><div>اسم الموظف: ${esc(m.name || '')}</div><div>التاريخ: ........</div><div>التوقيع</div></div><div class="p-sign"><div>الموارد البشرية</div><div>مسؤول تقنية المعلومات</div></div>`)
        + `<p class="p-small">قاعدة الإقرار: الخانات بتميّز بين «اطلعت على» و«وافقت على» و«استلمت فعليًا». يتولّد من سجل HR وIT، ويُطبع للتوقيع اليدوي مرة واحدة ويُمسح ضوئيًا للأرشفة. ذلك لا يلغي أي متطلبات توقيع منفصلة يقررها المختص القانوني. تأكيد القراءة وحده لا يعني توقيع العقد أو تسليم عهدة.</p>`;
    } else if (d.kind === 'annex') {
      body = who + `<div class="p-notes">ملحق للإقرار الموحد رقم <b dir="ltr">${esc((d.parents || [])[0] || '')}</b>: أقر باستلامي فعليًا العهد التالية بالإضافة لما ورد في الإقرار.</div>` + itemsTable(d.items, [['', () => box(true) + ' ' + ITX.MARK.received]]) + `<div class="p-sign"><div>توقيع الموظف المستلم</div><div>مسؤول تقنية المعلومات</div></div>`;
    } else if (d.kind === 'clearance') {
      const cur = ITX.custody(d.empId), held = x => cur.some(c => c.t === x.t && c.id === x.id);
      body = who + `<div class="p-notes">اتولّد من ${esc((d.parents || []).join(' و ') || 'العهدة الحالية')} · ${d.items.filter(x => x.group === 'asset').length} أصول + ${d.items.filter(x => x.group === 'account').length} حسابات</div>`
        + itemsTable(d.items, [['الحالة وقت الطباعة', x => held(x) ? (x.t === 'tool' ? 'هيتنقل' : 'لسه معاه') : 'اتسلم / اتقفل'], ['توقيع المستلم', () => '']])
        + `<div class="p-sign"><div>مسؤول تقنية المعلومات</div><div>الموارد البشرية</div><div>المدير المباشر</div><div>الحسابات</div></div>`;
    } else if (d.kind === 'returnAck') {
      body = who + `<div class="p-notes">أقر أنا <b>${esc(m.name || '')}</b> بأنني سلّمت العهد التالية لقسم تقنية المعلومات، وتم قفل حساباتي على أنظمة الشركة. <b>الحسابات ماتصرفش التسوية النهائية قبل توقيع هذا الإقرار.</b></div>`
        + itemsTable(d.items, [['الحالة عند الاستلام', x => esc(x.condition || '')], ['تاريخ الاستلام', x => esc(x.at || '')]]) + `<div class="p-sign"><div>توقيع الموظف</div><div>مسؤول تقنية المعلومات</div></div>`;
    } else if (d.kind === 'wipeCert') {
      body = `<div class="p-notes">نشهد بأنه تم مسح كل البيانات من الجهاز التالي وحذفه من الـ MDM قبل ${d.reason === 'retire' ? 'تكهينه' : 'رجوعه للمخزن خالي'}، بطريقة: <b>${esc(d.method || '')}</b>.</div>` + itemsTable(d.items) + `<div class="p-sign"><div>القائم بالمسح: ${esc(d.by || '')}</div><div>اعتماد مسؤول تقنية المعلومات</div></div>`;
    }
    UI.print(UI.printHeader(K[0], d.no, d.date, K[1]) + body);
    if (d.status === 'draft') ITX.setDocStatus(d, 'printed');
  };

  // ================= الأجهزة: المسح والتكهين =================
  ITX.wipeAsset = function (a, after) {
    UI.modal({
      title: 'مسح ' + a.tag + ' وحذفه من MDM', saveLabel: 'تم المسح',
      body: `<div class="grid2">${UI.field({ label: 'طريقة المسح', name: 'method', type: 'select', options: ['Factory Reset + حذف من MDM', 'إعادة تثبيت الويندوز (Clean Install)', 'Secure Erase للهارد', 'مسح عن بُعد من MDM'] })}
        ${UI.field({ label: 'الحالة بعد المسح', name: 'condition', type: 'select', options: Fin.CONDS || ['جيد'], value: a.condition })}</div>
        <p class="muted small">الجهاز هيرجع "خالي / جاهز للتسليم"، وهتتعمل شهادة مسح داتا.</p>`,
      onSave: m => {
        const d = UI.form(m.el);
        Object.assign(a, { status: 'ready', mdm: false, condition: d.condition, wipedAt: today() }); C.upsert('assets', a, 'مسح جهاز');
        const cert = ITX.newDoc('wipeCert', a.lastHolder || '', { items: [{ t: 'asset', id: a.id, code: a.tag, type: ITX.typeName(a), serial: a.serial, label: Fin.assetName ? Fin.assetName(a) : '' }], method: d.method, status: 'archived' });
        alog(a.id, 'wipe', { note: d.method + ' · ' + cert.no });
        ITX.event('اتمسح ' + a.tag + ' واتحذف من MDM ورجع المخزن خالي (' + cert.no + ')', { ref: a.tag });
        UI.toast('رجع ' + a.tag + ' خالي'); if (after) after(cert); else global.Router.render();
      },
    });
  };
  ITX.retireAsset = function (a) {
    UI.modal({
      title: 'تكهين ' + a.tag, saveLabel: 'تكهين',
      body: `<div class="grid2">${UI.field({ label: 'السبب', name: 'reason', type: 'select', options: ['وصل لعمر الإحلال', 'عطل غير اقتصادي في الإصلاح', 'تالف', 'بيع / تبرع'] })}${UI.field({ label: 'طريقة مسح الداتا', name: 'method', type: 'select', options: ['Secure Erase للهارد', 'تكسير الهارد / الفلاشة', 'Factory Reset + حذف من MDM', 'لا يحتوي على بيانات'] })}</div>
        <p class="muted small">مفيش جهاز يتكهن من غير شهادة مسح داتا.</p>`,
      onSave: m => {
        const d = UI.form(m.el), cert = ITX.newDoc('wipeCert', '', { items: [{ t: 'asset', id: a.id, code: a.tag, type: ITX.typeName(a), serial: a.serial, label: Fin.assetName ? Fin.assetName(a) : '' }], method: d.method, reason: 'retire', status: 'archived' });
        Object.assign(a, { status: 'retired', retiredAt: today(), retireReason: d.reason, wipeCert: cert.no, mdm: false }); C.upsert('assets', a, 'تكهين');
        alog(a.id, 'retire', { note: d.reason + ' · ' + cert.no }); ITX.event('اتكهّن ' + a.tag + ' بشهادة مسح ' + cert.no, { ref: a.tag });
        global.Router.render();
      },
    });
  };

  // ================= التوقع على متوسط استهلاك آخر 7 أيام =================
  ITX.snapsOf = (refType, refId) => DB.usageSnapshots.filter(s => s.refType === refType && s.refId === refId).sort((a, b) => a.date.localeCompare(b.date));
  ITX.forecast = function (refType, x) {
    const quota = U.num(x.quota), snaps = ITX.snapsOf(refType, x.id);
    const last = snaps[snaps.length - 1], used = last ? U.num(last.used) : U.num(x.used), remaining = Math.max(0, quota - used);
    const win = snaps.filter(s => s.date >= U.addDays(today(), -7));
    let perDay = 0;
    if (win.length >= 2) { const a = win[0], b = win[win.length - 1], dd = Math.max(1, U.daysBetween(a.date, b.date)); perDay = Math.max(0, (U.num(b.used) - U.num(a.used)) / dd); }
    const renewIn = ITX.days(x.renewalDate), daysLeft = perDay > 0 ? remaining / perDay : Infinity, pct = quota ? Math.round(remaining / quota * 100) : 0;
    let verdict, cls;
    if (remaining <= 0) { verdict = 'خلص'; cls = 'bad'; }
    else if (daysLeft <= 1.5) { verdict = 'هيخلص بكرة'; cls = 'bad'; }
    else if (renewIn != null && daysLeft < renewIn) { verdict = 'هيخلص قبل التجديد بـ ' + Math.max(1, Math.round(renewIn - daysLeft)) + ' يوم'; cls = 'warn'; }
    else { verdict = 'يكفي لحد التجديد'; cls = 'good'; }
    return { quota, used, remaining, pct, perDay, daysLeft, renewIn, verdict, cls, last };
  };
  ITX.fmtQty = (n, unit) => { n = U.num(n); if (unit === 'GB') return Math.round(n) + ' جيجا'; return n >= 1e6 ? (n / 1e6).toFixed(n % 1e6 ? 1 : 0).replace(/\.0$/, '') + 'M' : n >= 1e3 ? Math.round(n / 1e3) + 'K' : String(Math.round(n)); };
  ITX.addSnapshot = function (refType, x, used, date, source) {
    DB.usageSnapshots.push({ id: U.uid(), refType, refId: x.id, code: x.code, date: date || today(), used: U.num(used), source: source || 'يدوي' });
    x.used = U.num(used); C.upsert(refType === 'tool' ? 'tools' : 'netPackages', x, 'لقطة استهلاك');
  };

  // ================= مركز التجديدات (كل الأنواع في مكان واحد) =================
  ITX.renewals = function (win) {
    const t = today(), lim = U.addDays(t, win == null ? 30 : win), out = [];
    const push = (date, title, kind, sub, code, link, extra) => { if (date && date <= lim) out.push(Object.assign({ date, days: U.daysBetween(t, date), title, kind, sub, code, link }, extra || {})); };
    DB.tools.filter(x => x.status !== 'cancelled').forEach(x => push(x.renewalDate, 'تجديد اشتراك التول', 'تول', x.dept || 'عام', x.code, '#/tool/' + x.id, { cost: ITX.sar(x.cost, x.currency) }));
    DB.lines.filter(l => !['cancelled'].includes(l.status) && l.renewDate).forEach(l => push(l.renewDate, l.country === 'SA' ? 'تجديد شريحة سعودية' : 'شحن شريحة مصرية', 'شريحة', l.holderId ? (ITX.staff(l.holderId).dept || '') : (l.purpose || 'عام'), l.code, '#/lines' + l.country, { cost: ITX.sar(l.cost, l.currency) }));
    DB.netPackages.filter(x => x.status !== 'cancelled').forEach(x => push(x.renewalDate, 'تجديد باقة الإنترنت', 'باقة', x.branch || '', x.code, '#/net', { cost: ITX.sar(x.cost, x.currency) }));
    DB.assets.filter(a => !['retired', 'lost'].includes(a.status) && a.warrantyEnd && a.warrantyEnd >= t).forEach(a => push(a.warrantyEnd, 'انتهاء ضمان ' + ITX.typeName(a), 'ضمان', ITX.deptOf(a), a.tag, '#/asset/' + a.id));
    DB.domains.filter(x => x.status !== 'cancelled').forEach(x => push(x.expiry, x.kind === 'ssl' ? 'شهادة SSL — ' + x.name : x.kind === 'hosting' ? 'تجديد الهوستنج — ' + x.name : 'تجديد الدومين — ' + x.name, x.kind === 'ssl' ? 'SSL' : x.kind === 'hosting' ? 'هوستنج' : 'دومين', 'عام', x.code, '#/domains', { cost: ITX.sar(x.cost, x.currency) }));
    DB.subscriptions.filter(s => ['active', 'trial'].includes(s.status)).forEach(s => push(s.renewalDate, (s.kind === 'license' ? 'تراخيص ' : 'تجديد ') + s.name, 'ترخيص', 'عام', s.code || 'LIC', '#/sub/' + s.id, { cost: ITX.sar(s.cost, s.currency) }));
    return out.sort((a, b) => a.date.localeCompare(b.date));
  };

  // ================= الجرد =================
  ITX.lastStocktake = () => DB.stocktakes.slice().sort((a, b) => (b.startedAt || '').localeCompare(a.startedAt || ''))[0];
  ITX.stocktakeStats = function (st) {
    st = st || ITX.lastStocktake(); if (!st) return { total: 0, reviewed: 0, pct: 0, mismatch: 0, misplaced: 0 };
    const it = st.items || [], rev = it.filter(x => x.seen);
    return { total: it.length, reviewed: rev.length, pct: it.length ? Math.round(rev.length / it.length * 100) : 0, mismatch: rev.filter(x => x.serialOk === false).length, misplaced: rev.filter(x => x.placeOk === false).length, missing: st.status === 'closed' ? it.filter(x => !x.seen).length : 0 };
  };
  ITX.lastFullStocktake = () => DB.stocktakes.filter(s => s.status === 'closed' && s.full).sort((a, b) => (b.closedAt || '').localeCompare(a.closedAt || ''))[0];

  // ================= الباك أب =================
  // ساعات من آخر نجاح، والتأخير = اللي عدّى بعد المدة المحددة (كل 24 ساعة ← آخر نجاح من 50 = متأخر 26)
  ITX.backupSince = b => b.lastSuccess ? (Date.now() - new Date(b.lastSuccess).getTime()) / 3600000 : 9999;
  ITX.backupOk = b => ITX.backupSince(b) <= U.num(b.everyHours || 24);
  ITX.backupLate = b => Math.max(0, Math.round(ITX.backupSince(b) - U.num(b.everyHours || 24)));

  // ================= التنبيهات (جوه النظام + Telegram · مرة واحدة لكل حدث) =================
  ITX.alerts = function () {
    const t = today(), out = [], A = (sev, title, code, link, key, sub) => out.push({ sev, title, code, link, key: key || (title + ':' + code), sub: sub || '' });
    DB.tools.filter(x => x.status !== 'cancelled').forEach(x => { const f = ITX.forecast('tool', x); if (f.cls === 'bad') A('urgent', f.remaining <= 0 ? 'التوكن خلص' : 'التوكن هيخلص بكرة تقريبًا', x.code, '#/tool/' + x.id, 'tok:' + x.id + ':' + (x.renewalDate || '')); else if (f.cls === 'warn') A('soon', 'التوكن ' + f.verdict, x.code, '#/tool/' + x.id, 'tokw:' + x.id + ':' + (x.renewalDate || '')); });
    DB.netPackages.filter(x => x.status !== 'cancelled').forEach(x => { const f = ITX.forecast('net', x); if (f.remaining <= Math.max(10, f.quota * 0.1)) A('urgent', 'الباقة وصلت ' + Math.round(f.remaining) + ' جيجا', x.code, '#/net', 'net:' + x.id + ':' + (x.renewalDate || '')); });
    DB.lines.filter(l => l.status !== 'cancelled' && l.renewDate).forEach(l => { const n = ITX.days(l.renewDate); if (n != null && n <= (l.country === 'SA' ? 3 : 2)) A(n < 0 ? 'urgent' : 'soon', (l.country === 'SA' ? 'شريحة سعودية تتجدد ' : 'شريحة مصرية الشحن ') + ITX.inDays(n), l.code, '#/lines' + l.country, 'sim:' + l.id + ':' + l.renewDate); });
    DB.assets.filter(a => !['retired', 'lost'].includes(a.status) && a.warrantyEnd).forEach(a => { const n = ITX.days(a.warrantyEnd); if (n >= 0 && n <= 7) A('soon', 'ضمان ' + ITX.typeName(a) + ' ينتهي خلال ' + n + ' أيام', a.tag, '#/asset/' + a.id, 'war:' + a.id + ':' + a.warrantyEnd); });
    DB.backups.filter(b => b.status !== 'off').forEach(b => { if (!ITX.backupOk(b)) A('urgent', 'باك أب ' + b.name + ' متأخر ' + ITX.backupLate(b) + ' ساعة', b.code, '#/backup', 'bkp:' + b.id + ':' + (b.lastSuccess || '')); });
    DB.domains.filter(x => x.status !== 'cancelled' && x.expiry).forEach(x => { const n = ITX.days(x.expiry); if (n <= 14) A(n < 3 ? 'urgent' : 'soon', (x.kind === 'ssl' ? 'شهادة SSL ' : x.kind === 'hosting' ? 'الهوستنج ' : 'الدومين ') + x.name + ' ' + (n < 0 ? 'انتهت' : 'تنتهي ' + ITX.inDays(n)), x.code, '#/domains', 'dom:' + x.id + ':' + x.expiry); });
    DB.subscriptions.filter(s => ['active', 'trial'].includes(s.status)).forEach(s => { const n = ITX.days(s.renewalDate); if (n != null && n < 0) A('urgent', 'اشتراك ' + s.name + ' فات موعد تجديده', s.code || 'LIC', '#/sub/' + s.id, 'sub:' + s.id + ':' + s.renewalDate); const used = (s.users || []).length || U.num(s.usedSeats); if (s.seats && used >= s.seats && s.kind === 'license') A('soon', 'تراخيص ' + s.name + ' كلها مستخدمة (' + used + ' / ' + s.seats + ')', s.code || 'LIC', '#/sub/' + s.id, 'seat:' + s.id + ':' + used); });
    DB.consumables.filter(x => U.num(x.qty) < U.num(x.min)).forEach(x => A('soon', x.name + ' تحت الحد الأدنى (' + x.qty + ' / ' + x.min + ')', x.code, '#/consumables', 'con:' + x.id + ':' + x.qty));
    (DB.team || []).filter(ITX.isExited).forEach(m => { const c = ITX.custodyCount(m.id).assets, acc = ITX.liveAccounts(m.id).length; if (c) A('urgent', m.name + ' مشي ولسه معاه ' + c + ' عهدة', m.empNo || '', '#/holder/' + m.id, 'left:' + m.id + ':' + c); if (acc) A('urgent', 'حسابات شغالة لموظف Exited: ' + m.name + ' (' + acc + ')', m.empNo || '', '#/holder/' + m.id, 'acc:' + m.id + ':' + acc); });
    DB.cycles.filter(c => c.status === 'open').forEach(c => {
      const n = ITX.days(c.date), st = ITX.cycleStage(c);
      if (c.type === 'onboard' && n != null && n <= 1 && st <= 5) A('urgent', 'مباشرة ' + ITX.nm(c.empId) + ' ' + ITX.inDays(n) + ' وتجهيز IT متأخر', c.no, '#/cycle/' + c.id, 'onb:' + c.id + ':' + c.date);
      if (c.type === 'offboard' && n != null && n <= 1 && ITX.custodyCount(c.empId).assets) A('soon', 'آخر يوم ' + ITX.nm(c.empId) + ' ' + ITX.inDays(n) + ' ولسه معاه عهد', c.no, '#/cycle/' + c.id, 'offd:' + c.id + ':' + c.date);
    });
    const ss = ITX.stocktakeStats(); if (ss.mismatch) A('soon', ss.mismatch + ' سيريال مش مطابق للشيت في الجرد', 'STK', '#/stocktake', 'stk:' + ss.mismatch);
    const se = DB.syncErrors.filter(e => e.status !== 'fixed').length; if (se) A('soon', se + ' أخطاء مزامنة مع الشيت', 'SYNC', '#/syncerr', 'sync:' + se + ':' + t);
    return out.sort((a, b) => (a.sev === 'urgent' ? 0 : 1) - (b.sev === 'urgent' ? 0 : 1));
  };
  ITX.sendTelegram = function (text) {
    const s = S(); if (!s.telegramToken || !s.telegramChat) return Promise.resolve(false);
    return fetch('https://api.telegram.org/bot' + encodeURIComponent(s.telegramToken) + '/sendMessage', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ chat_id: s.telegramChat, text }) })
      .then(r => r.ok).catch(() => false);
  };
  ITX.dispatchAlerts = function () {
    const sent = new Set(DB.alertsSent.map(x => x.key)), fresh = ITX.alerts().filter(a => !sent.has(a.key));
    fresh.forEach(a => {
      const ch = ['crm'];
      if (global.Platform && global.Platform.notify) global.Platform.notify({ to: ['it', 'admin'], title: (a.sev === 'urgent' ? '🔴 ' : '🟠 ') + a.title + (a.code ? ' · ' + a.code : ''), icon: a.sev === 'urgent' ? '🔴' : '🟠', link: 'it.html' + a.link });
      if (S().telegramToken && S().telegramChat) { ch.push('telegram'); ITX.sendTelegram((a.sev === 'urgent' ? '🔴 عاجل: ' : '🟠 قريب: ') + a.title + (a.code ? ' — ' + a.code : '')); }
      DB.alertsSent.unshift({ key: a.key, at: nowISO(), title: a.title, code: a.code, sev: a.sev, channels: ch });
    });
    if (DB.alertsSent.length > 1000) DB.alertsSent.length = 1000;
    if (fresh.length) global.Store.save();
    return fresh.length;
  };

  // ================= المزامنة مع Google Sheets =================
  // الشيت = الداتا الأساسية للأجهزة والشرائح والتولات… (البيانات الثابتة)
  // النظام = الحركات (الحالة، مع مين، الحجز، المسح) — المزامنة متكتبش أبدًا على حقول الحركات
  const COL = { // اسم العمود في الشيت (عربي أو إنجليزي) ← الحقل
    code: ['الكود', 'code', 'asset id', 'id'], type: ['النوع', 'type'], brand: ['الماركة', 'brand'], model: ['الموديل', 'model'], serial: ['السيريال', 'serial', 'imei', 'serial number'],
    specs: ['المواصفات', 'specs'], company: ['الشركة', 'company'], branch: ['الفرع', 'branch'], dept: ['القسم', 'department', 'dept'], purchaseDate: ['تاريخ الشراء', 'purchase date'],
    warrantyEnd: ['نهاية الضمان', 'warranty end', 'warranty'], cost: ['السعر', 'التكلفة', 'cost', 'price'], currency: ['العملة', 'currency'], supplier: ['المورد', 'supplier', 'vendor'],
    location: ['المكان', 'location'], name: ['الاسم', 'name'], number: ['الرقم', 'number', 'رقم الشريحة'], carrier: ['شركة الاتصالات', 'carrier'], plan: ['الباقة', 'plan'],
    renewalDate: ['التجديد', 'تاريخ التجديد', 'renewal', 'renewal date', 'الشحن'], account: ['الحساب', 'account'], quota: ['الحصة', 'quota', 'الرصيد'], unit: ['الوحدة', 'unit'],
    seats: ['المقاعد', 'seats'], expiry: ['الانتهاء', 'expiry'], kind: ['الصنف', 'kind'], qty: ['الكمية', 'qty'], min: ['الحد الأدنى', 'min'],
    used: ['المستخدم', 'used', 'الاستهلاك'], remaining: ['المتبقي', 'remaining', 'remain'], date: ['التاريخ', 'date'],
    url: ['الرابط', 'الموقع', 'url', 'website', 'link'], cycle: ['الدورة', 'دورة الدفع', 'cycle', 'billing'], category: ['الفئة', 'category'], loginEmail: ['حساب الدخول', 'login', 'login email'], source: ['المصدر', 'source'], role: ['الوظيفة', 'role', 'title'], email: ['الإيميل', 'email'], empNo: ['كود الموظف', 'employee id', 'emp'],
  };
  const TYPE_AR = { 'لابتوب': 'laptop', 'موبايل': 'mobile', 'كمبيوتر مكتبي': 'desktop', 'شاشة': 'monitor', 'تابلت': 'tablet', 'طابعة': 'printer', 'راوتر': 'network', 'إكسسوار': 'accessory', 'شاحن': 'accessory', 'كاميرا': 'camera' };
  // الحقول اللي الشيت بيملكها لكل نوع
  const OWN = {
    assets: ['type', 'brand', 'model', 'serial', 'specs', 'company', 'branch', 'dept', 'purchaseDate', 'warrantyEnd', 'cost', 'currency', 'supplier', 'location'],
    lines: ['number', 'carrier', 'plan', 'renewDate', 'cost', 'currency', 'company', 'branch'], tools: ['name', 'account', 'quota', 'unit', 'renewalDate', 'cost', 'currency', 'dept'],
    netPackages: ['name', 'quota', 'renewalDate', 'cost', 'currency', 'branch', 'provider'], domains: ['name', 'kind', 'expiry', 'cost', 'currency', 'registrar'],
    subscriptions: ['name', 'seats', 'renewalDate', 'cost', 'currency', 'vendor', 'plan', 'url', 'category', 'cycle', 'loginEmail'], consumables: ['name', 'qty', 'min', 'unit', 'category'], team: ['name', 'role', 'dept', 'email', 'company', 'branch', 'empNo'],
  };
  const PREFIX = { assets: /^(DEV|MOB|ACC)-\d+$/, lines: /^SIM-(SA|EG)-\d+$/, tools: /^TOOL-\d+$/, netPackages: /^NET-\d+$/, domains: /^DOM-\d+$/, subscriptions: /^LIC-\d+$/, consumables: /^CON-\d+$/, team: /^EMP-\d+$/ };
  ITX.parseCSV = function (text) {
    const rows = []; let row = [], cell = '', q = false;
    text = String(text || '').replace(/^﻿/, '');
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (q) { if (ch === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; } else cell += ch; }
      else if (ch === '"') q = true; else if (ch === ',') { row.push(cell); cell = ''; }
      else if (ch === '\n' || ch === '\r') { if (ch === '\r' && text[i + 1] === '\n') i++; row.push(cell); rows.push(row); row = []; cell = ''; }
      else cell += ch;
    }
    if (cell !== '' || row.length) { row.push(cell); rows.push(row); }
    const nonEmpty = rows.filter(r => r.some(c => String(c).trim() !== ''));
    if (!nonEmpty.length) return [];
    const head = nonEmpty[0].map(h => String(h).trim().toLowerCase());
    const map = head.map(h => Object.keys(COL).find(k => COL[k].some(n => n.toLowerCase() === h)) || '');
    return nonEmpty.slice(1).map((r, i) => { const o = { __row: i + 2 }; map.forEach((k, j) => { if (k) o[k] = String(r[j] == null ? '' : r[j]).trim(); }); return o; });
  };
  const normDate = v => {
    if (!v) return ''; let y, mo, da, m;
    if ((m = String(v).match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/))) [y, mo, da] = [+m[1], +m[2], +m[3]];
    else if ((m = String(v).match(/^(\d{1,2})[\/.-](\d{1,2})[\/.-](\d{4})$/))) [da, mo, y] = [+m[1], +m[2], +m[3]]; // يوم/شهر/سنة
    else return null;
    const dt = new Date(y, mo - 1, da);
    if (dt.getFullYear() !== y || dt.getMonth() !== mo - 1 || dt.getDate() !== da) return null;
    return y + '-' + String(mo).padStart(2, '0') + '-' + String(da).padStart(2, '0');
  };
  // استيراد صفوف شيت واحد: يراجع الكود والقسم والتاريخ، ويكتب الحقول اللي الشيت بيملكها بس
  ITX.importRows = function (coll, rows, tab) {
    const errs = [], seen = {}, depts = ITX.allDepts();
    let added = 0, updated = 0;
    rows.forEach(r => {
      const code = String(r.code || '').toUpperCase().trim(), E = p => errs.push({ id: U.uid(), at: nowISO(), sheet: tab || coll, row: r.__row, code, problem: p, status: 'open' });
      if (!code) return E('صف ناقص الكود');
      if (PREFIX[coll] && !PREFIX[coll].test(code)) return E('الكود مش على النمط المتفق عليه');
      if (seen[code]) return E('كود مكرر في الشيت (صف ' + seen[code] + ')');
      seen[code] = r.__row;
      if (r.dept && !depts.includes(r.dept)) return E('القسم "' + r.dept + '" مش موجود في قائمة الأقسام');
      for (const k of ['purchaseDate', 'warrantyEnd', 'renewalDate', 'expiry', 'date']) if (r[k]) { const d = normDate(r[k]); if (d === null) return E('تاريخ مش صحيح في عمود ' + k + ': ' + r[k]); r[k] = d; }
      if (coll === 'usage') {
        // لقطة يومية (من سكريبت WE أو الـ Usage API): الكود · التاريخ · المستخدم أو المتبقي · الحصة · التجديد
        const ref = DB.tools.find(x => x.code === code) || DB.netPackages.find(x => x.code === code); if (!ref) return E('الكود مش موجود في التولات أو الباقات');
        const rt = DB.tools.includes(ref) ? 'tool' : 'net', dt = r.date || today();
        if (r.quota && U.num(r.quota) > 0) ref.quota = U.num(r.quota);
        if (r.renewalDate) ref.renewalDate = r.renewalDate;
        if ((r.used === undefined || r.used === '') && r.remaining !== undefined && r.remaining !== '') r.used = U.num(ref.quota) - U.num(r.remaining);
        if (r.used === undefined || r.used === '') return E('مفيش قيمة في عمود المستخدم أو المتبقي');
        const ex = DB.usageSnapshots.find(s => s.refId === ref.id && s.date === dt);
        if (ex) { if (U.num(ex.used) !== U.num(r.used)) { ex.used = U.num(r.used); ref.used = ex.used; updated++; } }
        else { ITX.addSnapshot(rt, ref, r.used, dt, r.source || 'Google Sheets'); added++; }
        ref.lastFetch = nowISO();
        return;
      }
      if (coll === 'domains') {
        if (!r.registrar && r.supplier) r.registrar = r.supplier;
        if (r.kind) r.kind = { 'دومين': 'domain', 'domain': 'domain', 'ssl': 'ssl', 'شهادة ssl': 'ssl', 'هوستنج': 'hosting', 'استضافة': 'hosting', 'hosting': 'hosting' }[String(r.kind).toLowerCase()] || 'domain';
        if (!r.expiry && r.renewalDate) r.expiry = r.renewalDate;
      }
      if (coll === 'netPackages' && !r.provider && r.supplier) r.provider = r.supplier;
      if (coll === 'subscriptions') {
        if (!r.vendor && r.supplier) r.vendor = r.supplier;
        if (r.cycle) r.cycle = { 'شهري': 'monthly', 'ربع سنوي': 'quarterly', 'سنوي': 'yearly' }[r.cycle] || (['monthly', 'quarterly', 'yearly'].includes(r.cycle) ? r.cycle : 'monthly');
      }
      if (coll === 'assets' && r.type) r.type = TYPE_AR[r.type] || r.type;
      if (coll === 'lines') { r.country = code.startsWith('SIM-SA') ? 'SA' : 'EG'; if (r.renewalDate) r.renewDate = r.renewalDate; }
      const keyField = coll === 'assets' ? 'tag' : coll === 'team' ? 'empNo' : 'code';
      let x = DB[coll].find(o => String(o[keyField] || '').toUpperCase() === code);
      if (coll === 'assets' && r.serial && DB.assets.some(o => o !== x && o.serial && o.serial.toLowerCase() === r.serial.toLowerCase())) return E('السيريال ' + r.serial + ' مسجل على كود تاني');
      const patch = {}; (OWN[coll] || []).forEach(k => { if (r[k] !== undefined && r[k] !== '') patch[k] = ['cost', 'quota', 'seats', 'qty', 'min'].includes(k) ? U.num(r[k]) : r[k]; });
      if (coll === 'lines' && r.country) patch.country = r.country;
      if (x) { const before = JSON.stringify(OWN[coll].map(k => x[k])); Object.assign(x, patch); if (JSON.stringify(OWN[coll].map(k => x[k])) !== before) updated++; }
      else {
        x = Object.assign({ id: U.uid(), createdAt: nowISO(), source: 'sheet' }, patch);
        x[keyField] = code; ITX.bumpSeq(code);
        if (coll === 'assets') Object.assign(x, { status: 'stock', condition: 'جديد', holderId: '' });
        if (coll === 'lines') Object.assign(x, { status: 'stock', kind: 'voice', holderId: '' });
        if (coll === 'tools' || coll === 'netPackages' || coll === 'domains' || coll === 'subscriptions') Object.assign(x, { status: 'active', users: [], history: [] });
        if (coll === 'team') Object.assign(x, { active: true });
        if (coll === 'netPackages' && !x.currency) x.currency = 'EGP'; // الباقات بالجنيه
        if (coll === 'subscriptions' && !x.category) x.category = 'مواقع ومنصات مدفوعة';
        DB[coll].push(x); added++;
        if (coll === 'assets') DB.assetLog.push({ id: U.uid(), assetId: x.id, kind: 'create', date: today(), note: 'من Google Sheets' });
      }
    });
    // أخطاء الشيت ده تتحدث كل مزامنة (القديمة اللي اتصلحت تتقفل)
    DB.syncErrors.filter(e => e.sheet === (tab || coll) && e.status !== 'fixed').forEach(e => { if (!errs.some(n => n.row === e.row && n.problem === e.problem)) { e.status = 'fixed'; e.fixedAt = nowISO(); } });
    errs.forEach(n => { if (!DB.syncErrors.some(e => e.sheet === n.sheet && e.row === n.row && e.problem === n.problem && e.status !== 'fixed')) DB.syncErrors.unshift(n); });
    global.Store.save();
    return { added, updated, errors: errs.length };
  };
  ITX.sheetUrl = tab => 'https://docs.google.com/spreadsheets/d/' + encodeURIComponent(S().sheetId) + '/gviz/tq?tqx=out:csv&sheet=' + encodeURIComponent(tab);
  ITX.sync = async function (manual) {
    const s = S(); if (!s.sheetId) { if (manual) UI.toast('اكتب ID الشيت الأول من "المزامنة والإعدادات"', 'warn'); return null; }
    const res = {}; let ok = true;
    for (const [coll, tab] of Object.entries(s.sheetTabs)) {
      if (!tab) continue;
      try { const r = await fetch(ITX.sheetUrl(tab)); if (!r.ok) throw new Error('HTTP ' + r.status); res[coll] = ITX.importRows(coll, ITX.parseCSV(await r.text()), tab); }
      catch (e) { ok = false; res[coll] = { error: e.message }; }
    }
    s.lastSync = nowISO(); s.lastSyncOk = ok; s.lastSyncResult = res; global.Store.save();
    ITX.event('مزامنة Google Sheets: ' + Object.entries(res).map(([k, v]) => v.error ? k + ' ✕' : k + ' +' + v.added + ' ~' + v.updated + (v.errors ? ' !' + v.errors : '')).join(' · '), { kind: 'sync', by: 'Sync Worker' });
    ITX.dispatchAlerts();
    if (manual) UI.toast(ok ? 'تمت المزامنة' : 'المزامنة خلصت مع أخطاء', ok ? 'ok' : 'warn');
    return res;
  };

  // ================= الدورة (تعيين / إخلاء) =================
  ITX.cycleStage = function (c) {
    const open = (c.items || []).filter(i => !i.done && !i.skipped);
    if (!open.length) return c.type === 'onboard' ? 8 : 9;
    return Math.min.apply(null, open.map(i => i.stage));
  };
  ITX.stageLabel = (c, n) => c.type === 'onboard' ? String(n) : 'X' + n;
  ITX.cycleDone = c => (c.items || []).filter(i => i.done || i.skipped).length;

  // ================= القايمة حسب الصلاحية =================
  // HR: رحلة الموظف بس · IT: كل حاجة (البيانات الشخصية للمرشح مخفية) · المدير: كل حاجة
  // تنبيه: ده تقييد واجهة فقط. الحماية الحقيقية على الـ Backend (/api + RBAC).
  ITX.EMP_GROUP = 'رحلة الموظف';
  ITX.filterNav = function (user) {
    const r = (user && user.role) || 'admin', nav = (global.APP || {}).nav; if (!nav) return;
    if (r === 'hr') {
      const keep = ['الرئيسية', ITX.EMP_GROUP, 'دورة الموظف']; for (let i = nav.length - 1; i >= 0; i--) if (!keep.includes(nav[i].group)) nav.splice(i, 1);
      nav[0].items = nav[0].items.filter(x => x[0] === 'itdash' || x[0] === 'alerts');
      const pages = global.APP.pages, ok = ['itdash', 'alerts', 'reqs', 'cands', 'cand', 'onboard', 'offboard', 'cycle', 'docs', 'staff', 'holder', 'asset'];
      if (pages) for (let i = pages.length - 1; i >= 0; i--) if (!ok.includes(pages[i])) pages.splice(i, 1);
    }
  };
  ITX.canSeePersonal = () => ['admin', 'hr'].includes(ITX.role());

  ITX.init = function () {
    ITX.ensure();
    try { ITX.dispatchAlerts(); } catch (e) { console.error(e); }
    clearInterval(ITX._t);
    // Sync Worker: كل 5 دقايق طول ما النظام مفتوح (على السيرفر: Apps Script trigger)
    ITX._t = setInterval(() => { if (S().sheetId) ITX.sync(false).then(() => global.Router && document.querySelector('[data-live]') && global.Router.render()); else ITX.dispatchAlerts(); }, Math.max(1, U.num(S().syncEvery) || 5) * 60000);
  };
  ITX.ensure();
})(window);
