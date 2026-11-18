/* =========================================================
   chatbot.js — 🤖 مساعد SEG (شات بوت)
   ثلاث جماهير:
   • staff  : موظفو الشركة داخل الأنظمة — "إزاي أعمل كذا؟" + أسئلة عن البيانات + تحويل للدعم
   • client : العميل في منصة العملاء — رصيده، فواتيره، المنشورات المنتظرة + تحويل لموظف
   • site   : زوار موقع الشركة — الخدمات والأسعار وإزاي نبدأ (داخل شات الموقع)
   الإجابة من قاعدة معرفة عربية + بيانات النظام. لو اتحدد APP_CONFIG.aiEndpoint (سيرفر عليه نموذج
   ذكاء اصطناعي مثل Claude) يتبعت له السؤال اللي القاعدة مش لاقية له إجابة.
   ========================================================= */
(function (global) {
  'use strict';
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  // تطبيع العربي: إزالة التشكيل وتوحيد الألف والياء والتاء المربوطة
  const norm = s => String(s || '').toLowerCase().replace(/[ً-ْـ]/g, '').replace(/[أإآ]/g, 'ا').replace(/ى/g, 'ي').replace(/ة/g, 'ه').replace(/ؤ/g, 'و').replace(/ئ/g, 'ي').replace(/[^؀-ۿa-z0-9\s@]/g, ' ').replace(/\s+/g, ' ').trim();
  const HOST = () => location.pathname.split('/').pop() || 'index.html';
  const link = (page, hash, label) => `<a href="${page === HOST() ? '' : page}#/${hash}">${label}</a>`;

  // ================= قاعدة المعرفة =================
  const KB = {
    staff: [
      { k: ['فاتوره', 'فاتوره جديده', 'اعمل فاتوره', 'اصدار فاتوره', 'invoice'], a: () => `من ${link('index.html', 'invoices', 'النظام المالي ← فواتير المبيعات')} اضغط <b>+ فاتورة</b>، اختار العميل والخدمات، والضريبة 15% ورمز QR بيتحسبوا لوحدهم. ولو العميل عنده رقم ضريبي هتطلع "فاتورة ضريبية"، غير كده "مبسطة".` },
      { k: ['سند قبض', 'تحصيل', 'العميل دفع', 'استلام فلوس', 'دفعه من عميل'], a: () => `من ${link('index.html', 'receipts', 'سندات القبض')} ← <b>+ سند قبض</b>، اختار العميل والخزينة، ووزّع المبلغ على الفواتير المفتوحة.` },
      { k: ['مصروف', 'مصاريف', 'صرف', 'expense'], a: () => `من ${link('index.html', 'expenses', 'المصروفات')} ← <b>+ مصروف</b>. لو الخزينة بالجنيه اكتب المبلغ بالجنيه وسعر الصرف، والريال بيتحسب لوحده.` },
      { k: ['تحويل', 'جنيه', 'فريق مصر', 'تحويل فلوس مصر', 'سعر الصرف'], a: () => `التحويل بين الخزائن (مثلاً من الراجحي لبنك مصر) من ${link('index.html', 'transfers', 'التحويلات')}، بتكتب المبلغ بالريال والمستلم بالجنيه، وفروق العملة بتتسجل من صفحة الخزينة.` },
      { k: ['ضريبه', 'اقرار', 'vat', 'زكاه', 'هيئه'], a: () => `إقرار ضريبة القيمة المضافة من ${link('index.html', 'reports/vat', 'التقارير ← إقرار الضريبة')}، وفيه زرار سداد/مقاصة بقيد تلقائي. الاحتساب العكسي للإعلانات الأجنبية (Meta، تيك توك...) محسوب فيه.` },
      { k: ['تقرير', 'ارباح', 'قائمه الدخل', 'ميزانيه', 'ميزان المراجعه'], a: () => `كل التقارير المالية في ${link('index.html', 'reports', 'التقارير المالية')}: قائمة الدخل، الميزانية، ميزان المراجعة، التدفق النقدي، أعمار الديون، وربحية العملاء والمشاريع.` },
      { k: ['حمله', 'حمله جديده', 'نتايج الحمله', 'campaign', 'اعلان'], a: () => `من ${link('marketing.html', 'campaigns', 'التسويق ← الحملات الإعلانية')} ← <b>+ حملة</b>، وبعدها <b>+ نتائج</b> كل أسبوع من مدير الإعلانات (الإنفاق، الظهور، الـ Leads...).` },
      { k: ['منشور', 'خطه المحتوي', 'محتوي', 'بوست', 'تقويم'], a: () => `خطة المحتوى في ${link('marketing.html', 'content', 'التسويق ← خطة المحتوى')}. حطه في مرحلة "مراجعة العميل" عشان يظهر للعميل في منصة العملاء ويوافق عليه.` },
      { k: ['عميل محتمل', 'ليد', 'lead', 'leads', 'فرصه'], a: () => `العملاء المحتملون في ${link('marketing.html', 'leads', 'التسويق ← العملاء المحتملون')}. انقل الكارت بين المراحل بالسحب أو الأسهم، وافتحه تلاقي فرصة التعاقد ✨ وأزرار الماكرو 🧩.` },
      { k: ['مهمه', 'مهام', 'task', 'تاسك'], a: () => `المهام في ${link('marketing.html', 'tasks', 'مهام الفريق')}: <b>+ مهمة</b> واختار المسؤول وموعد التسليم.` },
      { k: ['مؤثر', 'مؤثرين', 'انفلونسر', 'influencer'], a: () => `مكتبة المؤثرين وسجل التعاون في ${link('marketing.html', 'influencers', 'المؤثرون')}، وفيها CPM وتكلفة التفاعل لكل مؤثر.` },
      { k: ['اتمته', 'قاعده', 'workflow', 'تلقائي', 'automation'], a: () => `من ${link('marketing.html', 'automation', 'الأتمتة')} ← <b>+ قاعدة</b>: اختار "لما يحصل" (مثلاً عميل محتمل جديد) وبعدها الإجراءات (مهمة، إشعار، رسالة واتساب...).` },
      { k: ['رساله', 'واتساب', 'رد علي عميل', 'صندوق التواصل', 'inbox'], a: () => `كل رسائل العملاء في ${link('marketing.html', 'inbox', 'صندوق التواصل')}، مرتبة حسب الأولوية والنبرة. زرار "↩️ رد" بيفتح واتساب أو الإيميل ويسجّل الرد.` },
      { k: ['زوار', 'الموقع', 'تعقب', 'visitors'], a: () => `زوار موقع الشركة في ${link('marketing.html', 'visitors', 'زوار الموقع')}: مين أونلاين، بيتفرج على إيه، وتقييم اهتمامه، وتحويله لعميل محتمل.` },
      { k: ['جهاز', 'لابتوب', 'عهده', 'تسليم جهاز', 'موبايل جديد'], a: () => `من ${link('it.html', 'assets', 'تقنية المعلومات ← الأجهزة والعُهد')}: سجّل الجهاز (+ جهاز) وبعدها <b>📤 تسليم</b> لموظف، وهيطلع إقرار استلام عهدة للتوقيع.` },
      { k: ['خط', 'رقم', 'شريحه', 'sim', 'خط سعودي', 'خط مصري'], a: () => `الخطوط في تقنية المعلومات: ${link('it.html', 'linesSA', 'الأرقام السعودية')} و${link('it.html', 'linesEG', 'الأرقام المصرية')}. ضيف الرقم وسلّمه للموظف.` },
      { k: ['ايميل', 'بريد', 'email', 'ايميل جديد', 'باسورد الايميل'], a: () => `إيميلات الشركة في ${link('it.html', 'mail', 'إيميلات الشركة')}. الإيميل نفسه بيتعمل من لوحة Google Workspace، والنظام بيسجّله ويتابعه. ولو نسيت باسورد إيميلك كلّم مسؤول IT.` },
      { k: ['موظف جديد', 'تعيين', 'استلام شغل', 'هيبدا', 'انضم', 'onboarding'], a: () => `من ${link('it.html', 'onboard', 'التعيينات')} ← <b>🟢 تسجيل تعيين جديد</b>: هتطلع قائمة (إيميل، جهاز، خط، اشتراكات، إقرار) وكل بند ليه زرار ينفّذه.` },
      { k: ['اخلاء طرف', 'استقال', 'هيسيب', 'هيمشي', 'ساب الشغل', 'ترك العمل', 'انهاء خدمه', 'offboarding'], a: () => `من ${link('it.html', 'offboard', 'الإخلاءات')} ← <b>🚪 تسجيل إخلاء طرف</b>: النظام بيجمع كل اللي مع الموظف (أجهزة، خطوط، إيميل، اشتراكات) وبعد التنفيذ تطبع شهادة إخلاء الطرف.` },
      { k: ['اشتراك', 'adobe', 'canva', 'تجديد', 'subscription'], a: () => `الاشتراكات في ${link('it.html', 'subs', 'الاشتراكات')}. زرار <b>🔁 تجديد</b> بيقدّم التاريخ ويسجّل المصروف في النظام المالي لوحده.` },
      { k: ['باسورد', 'كلمه السر', 'كلمه المرور', 'نسيت', 'مش عارف ادخل', 'password'], a: () => `تغيير كلمة المرور بيكون من المدير: النظام المالي ← الإعدادات ← <b>المستخدمين</b> ← "تغيير كلمة المرور". لو مش قادر تدخل خالص، اضغط "تحويل للدعم" تحت.` },
      { k: ['صلاحيه', 'مش شايف', 'مش ظاهر', 'مستخدم جديد', 'حساب'], a: () => `كل مستخدم بيشوف النظام اللي يخصه بس (محاسب ← المالي، تسويق ← التسويق، IT ← تقنية المعلومات). إضافة مستخدم أو تغيير صلاحيته من الإعدادات ← المستخدمين (للمدير).` },
      { k: ['منصه العملاء', 'حساب للعميل', 'العميل يدخل', 'بوابه'], a: () => `من ${link('marketing.html', 'clients', 'التسويق ← العملاء')} افتح العميل واضغط <b>🔑 منصة العملاء</b> واعمله حساب. العميل يدخل على clients.html ويشوف فواتيره ويوافق على المحتوى.` },
      { k: ['انجليزي', 'english', 'اللغه', 'عربي'], a: () => `زرار <b>English / العربية</b> في الشريط العلوي بيغيّر لغة الواجهة.` },
      { k: ['بحث', 'الاقي', 'دور', 'search'], a: () => `اضغط <b>Ctrl + K</b> في أي نظام واكتب اسم عميل أو رقم فاتورة أو جهاز أو رقم تليفون.` },
      { k: ['نسخه احتياطيه', 'باك اب', 'backup', 'البيانات راحت'], a: () => `النسخة الاحتياطية من النظام المالي ← الإعدادات ← "تنزيل نسخة احتياطية" (ملف JSON)، ومنها كمان الاسترجاع.` },
      { k: ['مشكله', 'عطل', 'مش شغال', 'error', 'خطا', 'بايظ', 'معلق'], a: () => `آسف إن فيه مشكلة 🙏 جرّب الأول تحديث الصفحة (<b>Ctrl + F5</b>). لو المشكلة لسه موجودة، اضغط <b>تحويل للدعم</b> تحت واكتب حصل إيه، وفريق IT هيوصله إشعار على طول.` },
    ],
    client: [
      { k: ['رصيد', 'عليا كام', 'المستحق', 'كم المبلغ', 'مديونيه'], a: c => c.balance },
      { k: ['فاتوره', 'فواتير', 'invoice', 'نسخه من الفاتوره'], a: () => `كل فواتيرك في ${link('clients.html', 'pinvoices', 'الفواتير')}، وتقدر تفتح أي فاتورة وتطبعها PDF بالـ QR الضريبي.` },
      { k: ['ادفع', 'سداد', 'تحويل', 'حساب بنكي', 'ايبان', 'iban'], a: () => `بيانات التحويل: ${esc((global.DB && DB.settings.bankDetails) || 'تواصل مع فريق الحسابات')}. اكتب رقم الفاتورة في التحويل لو سمحت 🙏` },
      { k: ['متاخر', 'متاخره', 'فاتت'], a: c => c.overdue },
      { k: ['منشور', 'موافقه', 'محتوي', 'بوست', 'اوافق'], a: c => c.content },
      { k: ['تعديل', 'غير', 'عايز اغير'], a: () => `من ${link('clients.html', 'papprove', 'موافقة المحتوى')} اضغط <b>✎ طلب تعديل</b> على المنشور واكتب التعديل، وهيوصل للمصمم على طول.` },
      { k: ['تقرير', 'نتايج', 'الحمله', 'اداء', 'leads'], a: () => `تقرير أداء حملاتك الشهري في ${link('clients.html', 'preport', 'تقرير الأداء')}، وتقدر تطبعه PDF.` },
      { k: ['كشف حساب', 'statement'], a: () => `كشف حسابك في ${link('clients.html', 'pstatement', 'كشف الحساب')}.` },
      { k: ['تواصل', 'اكلم', 'رقم', 'موظف', 'حد يكلمني'], a: () => `اضغط <b>تحويل لموظف</b> تحت واكتب طلبك، وفريقنا هيتواصل معاك 👌` },
      { k: ['شكرا', 'تمام', 'ممتاز'], a: () => `العفو 🌹 لو احتجت أي حاجة أنا هنا.` },
    ],
    site: [
      { k: ['خدمات', 'بتعملوا ايه', 'service', 'شغلكم'], a: () => `بنقدم: إدارة السوشيال ميديا، الحملات الإعلانية (Meta وسناب وتيك توك وجوجل)، إنتاج المحتوى والتصوير، التسويق عبر المؤثرين، الهوية البصرية، وSEO.` },
      { k: ['سعر', 'اسعار', 'باقه', 'باقات', 'كام', 'تكلفه', 'price'], a: () => `الباقات: <b>الأساسية 4,500 ر.س</b> شهرياً، و<b>النمو 8,500 ر.س</b> (الأكثر طلباً)، و<b>الشاملة</b> حسب الطلب. تحب نرشّحلك الأنسب لنشاطك؟` },
      { k: ['اعلان', 'حمله', 'اعلانات', 'سناب', 'تيك توك', 'ميتا'], a: () => `بندير حملاتك على Meta وسناب وتيك توك وجوجل باستهداف دقيق وتقرير أسبوعي. متوسط العائد على الإعلانات عند عملائنا 3.4x 📈` },
      { k: ['مؤثر', 'مؤثرين', 'مشاهير'], a: () => `بنختار مؤثرين مرخصين من "موثوق" ومناسبين لجمهورك، ونقيس النتائج بأكواد خصم ومشاهدات.` },
      { k: ['ابدا', 'نبدا', 'اشترك', 'استشاره', 'اتعاقد'], a: () => `نبدأ باستشارة مجانية 👌 اكتب في الشات نوع نشاطك وهدفك (مبيعات، متابعين، وعي بالعلامة)، أو املأ فورم "تواصل معنا".` },
      { k: ['مكان', 'فين', 'عنوان', 'مقر', 'الرياض', 'مصر'], a: () => `مقرنا في الرياض، وفريق الإنتاج والتصميم في القاهرة، وبنخدم عملاء في كل المملكة.` },
      { k: ['اعمالكم', 'شغل سابق', 'بورتفوليو', 'portfolio', 'عملاء'], a: () => `من شغلنا: مشروع عقاري في جدة (1,067 عميل محتمل)، عيادات تجميل في الرياض (عائد 6.7x)، ومتجر إلكتروني (+380 عملية شراء بالمؤثرين).` },
      { k: ['مده', 'امتي', 'وقت', 'كم يوم'], a: () => `بنبدأ التنفيذ خلال 3–5 أيام عمل من الاتفاق، وأول تقرير بعد أسبوع.` },
    ],
  };
  const SUGGEST = {
    staff: ['إزاي أعمل فاتورة؟', 'إزاي أسلّم لابتوب لموظف؟', 'موظف جديد هعمل إيه؟', 'إخلاء طرف', 'الفواتير المتأخرة', 'عندي مشكلة'],
    client: ['عليّا كام؟', 'المنشورات اللي مستنية موافقتي', 'إزاي أدفع؟', 'تقرير الحملات'],
    site: ['الأسعار', 'خدماتكم إيه؟', 'إزاي نبدأ؟'],
  };

  // ================= بيانات العميل (منصة العملاء) =================
  function clientCtx() {
    const L = global.Ledger, cid = global.Portal && global.Portal.cid; if (!cid || !L) return {};
    const bal = L.partyBalance(L.buildLedger(), 'customer', cid);
    const od = (DB.invoices || []).filter(i => i.customerId === cid && L.isPosted(i)).map(i => Object.assign({ i }, L.invoiceInfo(i))).filter(x => x.status === 'overdue');
    const rv = (DB.content || []).filter(x => x.customerId === cid && x.status === 'review');
    return {
      balance: bal > 0 ? `رصيدك المستحق حالياً <b>${U.money(bal)}</b>. التفاصيل في ${link('clients.html', 'pinvoices', 'الفواتير')}.` : `مفيش عليك أي مبالغ مستحقة ✅ شكراً لك.`,
      overdue: od.length ? `عندك ${od.length} فاتورة متأخرة: ${od.map(x => `<b>${esc(x.i.no)}</b> (${U.money(x.outstanding)})`).join('، ')}.` : `مفيش فواتير متأخرة ✅`,
      content: rv.length ? `فيه <b>${rv.length}</b> منشور مستني موافقتك — ${link('clients.html', 'papprove', 'راجِعها من هنا')}.` : `مفيش منشورات مستنية موافقتك دلوقتي ✅`,
    };
  }

  // ================= الإجابة =================
  function match(q, aud) {
    const n = ' ' + norm(q) + ' '; let best = null, bs = 0;
    (KB[aud] || []).forEach(e => { let s = 0; e.k.forEach(k => { const kk = norm(k); if (kk && n.includes(kk)) s += 2 + kk.length / 4; else kk.split(' ').forEach(w => { if (w.length > 2 && n.includes(' ' + w)) s += 0.8; }); }); if (s > bs) { bs = s; best = e; } });
    return bs >= 2 ? best : null;
  }
  // أسئلة عن البيانات (للموظفين) — من المساعد الذكي في platform
  const DATA_Q = /متاخر|افضل حمله|فرص|مبيعات الشهر|ايراد|الرسايل اللي|محتاجه رد|المهام المتاخره|تكلفه الاشتراكات|كام جهاز|كم جهاز/;
  function reply(q, aud) {
    aud = aud || 'staff';
    if (aud === 'staff' && global.Platform && global.Platform.answer && DATA_Q.test(norm(q))) { const a = global.Platform.answer(q); if (a && !/^جرّب تسأل/.test(a)) return { html: a, ok: true }; }
    const e = match(q, aud);
    if (e) { const c = aud === 'client' ? clientCtx() : {}; return { html: typeof e.a === 'function' ? e.a(c) : e.a, ok: true }; }
    return { html: aud === 'site' ? 'سؤال حلو 👌 هحوّله لحد من الفريق يرد عليك هنا أو على واتساب خلال دقائق.' : `مش متأكد إني فهمت 🤔 جرّب تكتبها بطريقة تانية، أو اضغط <b>${aud === 'client' ? 'تحويل لموظف' : 'تحويل للدعم'}</b> وهيوصل سؤالك لحد من الفريق.`, ok: false };
  }
  // نموذج ذكاء اصطناعي على السيرفر (اختياري): APP_CONFIG.aiEndpoint
  async function aiFallback(q, aud) {
    const ep = (global.APP_CONFIG || {}).aiEndpoint; if (!ep) return null;
    try { const r = await fetch(ep, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: q, audience: aud }) }); const j = await r.json(); return j && j.answer ? esc(j.answer).replace(/\n/g, '<br>') : null; } catch (e) { return null; }
  }

  // ================= التحويل لإنسان =================
  function escalate(q, aud, user) {
    if (!global.DB || !global.Core) return;
    if (aud === 'client' && global.Portal && global.Portal.cid && global.Platform) {
      global.Platform.logActivity({ refType: 'customer', refId: global.Portal.cid, channel: 'chat', direction: 'in', subject: 'من مساعد منصة العملاء', body: q });
    } else if (global.Platform) {
      global.Platform.notify({ to: ['it', 'admin'], title: '🆘 طلب دعم من ' + ((user && user.name) || 'موظف') + ': ' + q.slice(0, 80), body: q, icon: '🆘', link: location.pathname.split('/').pop() + location.hash });
      global.Store.save();
    }
  }

  // ================= الواجهة =================
  let audience = 'staff', who = null;
  const KEY = 'seg_bot_hist2';
  function mount(user, aud) {
    if (document.getElementById('segBot')) return;
    who = user || null; audience = aud || (user && user.role === 'client' ? 'client' : 'staff');
    const w = document.createElement('div'); w.id = 'segBot'; w.className = 'bot';
    w.innerHTML = `<button class="bot-btn" aria-label="مساعد SEG" title="مساعد SEG">🤖</button>
      <div class="bot-box" hidden role="dialog" aria-label="مساعد SEG"><header><span class="bot-av">🤖</span><div><b>مساعد SEG</b><small>${audience === 'client' ? 'أسألني عن حسابك وفواتيرك ومحتواك' : 'أسألني إزاي تعمل أي حاجة في النظام'}</small></div><button class="bot-x" aria-label="إغلاق">×</button></header>
        <div class="bot-msgs"></div><div class="bot-sug"></div>
        <form class="bot-in"><input name="q" placeholder="اكتب سؤالك..." autocomplete="off"><button aria-label="إرسال">➤</button></form>
        <div class="bot-foot"><button type="button" class="bot-esc">${audience === 'client' ? '👤 تحويل لموظف' : '🆘 تحويل للدعم'}</button></div></div>`;
    document.body.appendChild(w);
    const box = w.querySelector('.bot-box'), msgs = w.querySelector('.bot-msgs'), f = w.querySelector('.bot-in');
    const add = (html, me) => { const d = document.createElement('div'); d.className = 'bm ' + (me ? 'me' : 'ai'); d.innerHTML = html; msgs.appendChild(d); msgs.scrollTop = 1e6; save(); };
    const save = () => { try { sessionStorage.setItem(KEY + audience, msgs.innerHTML.slice(-20000)); } catch (e) {} };
    let last = '';
    const ask = async q => {
      q = String(q || '').trim(); if (!q) return; last = q; add(esc(q), true);
      const r = reply(q, audience);
      if (!r.ok) { const ai = await aiFallback(q, audience); if (ai) return add(ai); }
      add(r.html);
    };
    let restored = ''; try { restored = sessionStorage.getItem(KEY + audience) || ''; } catch (e) {}
    if (restored) msgs.innerHTML = restored; else add(audience === 'client' ? `أهلاً 👋 أنا مساعد ${esc((global.DB && DB.settings.companyName) || 'SEG')}. أقدر أقولك رصيدك، فواتيرك، والمنشورات اللي مستنية موافقتك.` : `أهلاً ${esc((who && who.name) || '')} 👋 أنا مساعد ${esc((global.DB && DB.settings.companyName) || 'SEG')}. اسألني إزاي تعمل أي حاجة في الأنظمة، أو عن الأرقام (الفواتير المتأخرة، أفضل حملة...).`);
    w.querySelector('.bot-sug').innerHTML = (SUGGEST[audience] || []).map(s => `<button type="button" class="chip" data-q="${esc(s)}">${esc(s)}</button>`).join('');
    w.querySelector('.bot-btn').onclick = () => { box.hidden = !box.hidden; if (!box.hidden) f.q.focus(); };
    w.querySelector('.bot-x').onclick = () => { box.hidden = true; };
    f.onsubmit = e => { e.preventDefault(); const q = f.q.value; f.q.value = ''; ask(q); };
    w.querySelector('.bot-sug').onclick = e => { const c = e.target.closest('[data-q]'); if (c) ask(c.dataset.q); };
    w.querySelector('.bot-esc').onclick = () => {
      const q = (f.q.value || last || '').trim();
      if (!q) { add('اكتب مشكلتك أو سؤالك الأول، وبعدها اضغط التحويل 🙏'); f.q.focus(); return; }
      escalate(q, audience, who); f.q.value = '';
      add(audience === 'client' ? '✅ وصل طلبك لفريقنا، وهيتواصلوا معاك قريباً.' : '✅ اتبعت طلبك لفريق IT والمدير، وهيوصلهم إشعار على طول.');
    };
    msgs.addEventListener('click', e => { const a = e.target.closest('a[href]'); if (a) box.hidden = true; });
  }

  global.SEGBot = { mount, reply, match, norm, KB };
  if (global.I18N) Object.assign(global.I18N.dict, { "مساعد SEG": "SEG Assistant", "اكتب سؤالك...": "Type your question...", "🆘 تحويل للدعم": "🆘 Escalate to support", "👤 تحويل لموظف": "👤 Talk to a person", "أسألني إزاي تعمل أي حاجة في النظام": "Ask me how to do anything in the system", "أسألني عن حسابك وفواتيرك ومحتواك": "Ask about your account, invoices and content" });
})(window);
