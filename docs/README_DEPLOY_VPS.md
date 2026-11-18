# النقل لـ VPS

النظام بيتنقل **بالإعدادات بس**، من غير ما الواجهات أو المعرّفات تتغير.

## الخطوات (Docker)
1. انسخ المشروع على السيرفر. **متنسخش** `backend\data` إلا لو هتنقل البيانات (شوف تحت).
2. اعمل `backend/.env` على السيرفر:
   ```
   ITADM_SECRET_KEY=<نفس المفتاح اللي على الجهاز لو هتنقل الأسرار، أو مفتاح جديد>
   ITADM_COOKIE_SECURE=true
   ITADM_SESSION_HOURS=12
   ```
3. شغّل `docker compose up -d --build`. ده بيشغّل السيرفر والـ Worker كخدمتين منفصلتين.
4. **لازم HTTPS قدامه.** اعمل reverse proxy (Caddy أو Nginx) على `127.0.0.1:8000`. مثال Caddy:
   ```
   it.example.com {
     reverse_proxy 127.0.0.1:8000
   }
   ```
5. أول مستخدم: `docker compose exec app python -m app.cli create-admin`.

## نقل البيانات من الجهاز
1. على الجهاز: **نسخة احتياطية** من الواجهة.
2. على السيرفر: انسخ ملف النسخة لـ `/data/backups` جوه volume `itadm-data`.
3. `docker compose stop`، وبعدين `docker compose run --rm app python -m app.cli restore <الملف>`، وبعدين `docker compose start`.
4. الأسرار المشفّرة: انسخ `secrets.enc.json` لـ volume `itadm-secrets`، **ومعاه نفس `ITADM_SECRET_KEY`**.

## ملاحظات
- **PostgreSQL:** لسه مش متنفذ (CR-11). SQLite كفاية لجهاز واحد أو VPS بعدد مستخدمين محدود. الكود مكتوب بـ SQLAlchemy، وتغيير `ITADM_DATABASE_URL` + `psycopg` + ترحيل البيانات هو الخطوة الجاية لو احتجت.
- **النسخ الاحتياطي المدمج** بيدعم SQLite بس. على PostgreSQL استخدم `pg_dump`.
- الـ Worker على VPS شغال 24/7، فالمتابعة مش هتقف لما جهازك يتقفل.
- **متنشرش النظام على النت من غير HTTPS** و`ITADM_COOKIE_SECURE=true`. وأي حد معاه الرابط هيوصل لشاشة الدخول، فاستخدم كلمات مرور قوية وفعّل MFA.
