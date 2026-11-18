/* =========================================================
   brand.js — هوية الشركة (الملف الوحيد اللي تغيّره عشان تغيّر الاسم واللوجو)
   ---------------------------------------------------------
   1) غيّر القيم اللي تحت.
   2) لو هتغيّر اللوجو: حط الصورة الجديدة في فولدر img بنفس الاسم
      (logo.png للخلفية الفاتحة، logo-white.png للوضع الليلي) — أو اكتب اسم ملفك هنا.
   3) افتح النظام تاني (Ctrl+F5) — الاسم الجديد بيتطبق على الشاشات والمطبوعات
      والبيانات المسجلة قبل كده بالاسم القديم.
   ========================================================= */
window.BRAND = {
  company: 'IT Administration',         // اسم الشركة / الإدارة (بيظهر في الإقرارات والمطبوعات وفلتر الشركات)
  companyEn: 'IT Administration',       // الاسم بالإنجليزي (المطبوعات ثنائية اللغة)
  systemName: 'IT Administration',      // اسم النظام في الشريط العلوي
  title: 'IT Administration',           // عنوان تاب المتصفح وأسفل القايمة
  tagline: '«كل جهاز معروف مكانه — وكل عهدة مربوطة بكود الموظف»', // جملة شاشة الدخول
  logo: 'img/logo.svg',                 // اللوجو على الخلفية الفاتحة (PNG أو SVG)
  logoWhite: 'img/logo-white.svg',      // اللوجو على الخلفية الغامقة (الوضع الليلي / شاشة الدخول)
  emailDomain: 'company.com',           // دومين إيميلات الشركة (name@الدومين)
  branches: ['الفرع الرئيسي — الرياض', 'مكتب القاهرة', 'المخزن'], // الفروع الافتراضية (تتعدل بعد كده من المزامنة والإعدادات)
  accent: '#0f766e',                    // اللون الأساسي للأزرار والروابط وشاشة الدخول (فاضي = اللون الافتراضي)
  nameNextToLogo: false,                // true لو اللوجو أيقونة بس ومحتاج الاسم جنبه في الشريط العلوي
};

// ---- تطبيق الهوية على البيانات المحفوظة (متغيّرش الجزء ده) ----
window.applyBrand = function (DB) {
  const B = window.BRAND, s = DB.settings, old = s.companyName;
  if (B.accent) { const r = document.documentElement.style; r.setProperty('--accent', B.accent); r.setProperty('--lg-accent', B.accent); }
  if (s.brandApplied === JSON.stringify([B.company, B.companyEn, B.logo, B.emailDomain])) return false;
  s.companyName = B.company; s.companyNameEn = B.companyEn || B.company; s.logo = B.logo; s.logoInit = true;
  // تغيير الدومين: الإيميلات والدومينات المسجلة بالدومين القديم تتغير للجديد
  const oldDom = s.emailDomain;
  if (B.emailDomain && oldDom && oldDom !== B.emailDomain) {
    const fix = v => typeof v === 'string' ? v.split(oldDom).join(B.emailDomain) : v;
    (DB.mailboxes || []).forEach(b => { b.address = fix(b.address); b.aliases = fix(b.aliases); b.forwardTo = fix(b.forwardTo); });
    (DB.team || []).forEach(m => { m.email = fix(m.email); });
    (DB.users || []).forEach(u => { if (!/@example\.com$/.test(u.email || '')) u.email = fix(u.email); });
    (DB.subscriptions || []).forEach(x => { x.name = fix(x.name); x.loginEmail = fix(x.loginEmail); });
    (DB.domains || []).forEach(x => { x.name = fix(x.name); });
  }
  if (B.emailDomain) s.emailDomain = B.emailDomain;
  // الاسم القديم في سجلات الأجهزة والموظفين والشرائح وطلبات الاحتياج يتغير للجديد
  if (old && old !== B.company) {
    ['team', 'assets', 'lines', 'requisitions', 'tools', 'netPackages'].forEach(k => (DB[k] || []).forEach(x => { if (x.company === old) x.company = B.company; }));
    ((DB.itSettings || {}).companies || []).forEach(c => { if (c.name === old) c.name = B.company; });
  }
  s.brandApplied = JSON.stringify([B.company, B.companyEn, B.logo, B.emailDomain]);
  return true;
};
