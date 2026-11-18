# مصفوفة التتبع (المتطلب ← الكود ← الـ API ← الاختبار)

| المتطلب | الكود | الـ API | الاختبار |
|---|---|---|---|
| M01: 6 أقسام، Dashboard وحدها في الرئيسية | `frontend/app/core.js` (NAV) | — | `run_e2e.py` (6 groups) |
| M01: الحرج أولًا + رسوم من بيانات حقيقية | `pages-dashboard.js`, `modules/ops.py::dashboard` | `GET /api/dashboard`, `/api/alerts` | `test_dashboard_real_data_only_and_role_blocks` |
| M02: كود Prefix-Category-Seq | `modules/assets.py::create_asset` | `POST /api/assets` | `test_m02_codes_prefix_category_sequence` |
| M02: السيريال فريد | `assets.py::assert_serial_unique` | `POST/PATCH /api/assets` | `test_serial_must_be_unique` |
| M02: حجز ≠ تسليم، نتائج الاسترجاع | `assets.py::reserve/do_handover/do_return` | `/api/assets/{id}/reserve|handover|return` | `test_m02_reserve_is_not_handover_and_return_results` |
| M02: شاشات متعددة | `AssetLink` | `/api/assets/{id}/links` | `test_m02_monitor_links_and_version_conflict` |
| M02: باسورد الجهاز مشفّر | `assets.py` (password), `secrets_store.py` | `/api/assets/{id}/password[/reveal]` | `test_device_password_*`, `test_passwords_imported_from_excel_*` |
| M02: تغيير الفئة | `assets.py::change_category` | `POST /api/assets/{id}/category` | `test_change_category_new_code_old_kept` |
| M03: مصفوفة القرار (HR فقط) | `journey.py::decide/final_decision` | `/api/journey/applications/{id}/decision` | `test_m03_decision_matrix` ×4 |
| M03: بيانات HR مخفية عن IT | `journey.py::app_out/interview_out` | `/api/journey/applications/{id}` | `test_m03_it_cannot_see_hr_private_data` |
| M03: 5 مستندات، التجنيد للذكور | `ensure_joining_rows` | `/api/employees/{id}/joining-docs/*` | `test_m03_onboarding_docs_versions_acks`, `test_m03_male_military_required` |
| M03: إصدارات الملفات والإقرارات | `DocumentTemplate/DocumentAck` | `/api/journey/templates`, `/api/employees/{id}/acks` | `test_m03_onboarding_docs_versions_acks` |
| M03: تنفيذ ← دليل ← اعتماد | `journey.py::update_task` | `/api/journey/tasks/{id}` | `test_m03_onboarding_docs_versions_acks` |
| M03: تجميد العهدة عند فتح الإخلاء | `people.py::assert_can_receive_custody` | كل نقاط التسليم | `test_m03_offboarding_freeze_it_paper_close` |
| M03: 6 جهات بالترتيب + النسخة الموقعة | `journey.py::set_authority/close_offboarding` | `/api/journey/offboarding/{id}/*` | `test_m03_offboarding_*`, `test_clearance_signatures_in_order` |
| M04: لا مدد افتراضية، شهور تقويمية | `common.py::add_period`, `technical.py::due_info` | `/api/technical/*` | `test_m04_no_default_periods_and_calendar_months` |
| M04: عتبات لكل خط | `technical.py::usage_state` | `/api/technical/readings` | `test_m04_thresholds_500gb_example` |
| M04: القراءة القديمة مش بتكتب فوق اليدوية | `technical.py::apply_reading` | `/api/technical/readings` | `test_m04_stale_api_reading_never_overwrites_manual` |
| M04: الدفع ≠ التجديد | `finance.py::direct_expense`, `technical.py::record_renewal` | `/api/finance/expenses`, `/api/technical/renewals` | `test_m04_m05_payment_does_not_renew` |
| M05: اعتماد ورقي + طباعة | `finance.py::print_pr/record_approval` | `/api/finance/purchase-requests/{id}/print|approval` | `test_m05_paper_approval_partial_receipt` |
| M05: التكلفة مرة واحدة | `finance.py::spend_summary`, `FinancialEvent` unique | `/api/finance/summary` | `test_m05_allocation_counted_once`, `test_m05_maintenance_single_economic_event` |
| M06: RBAC على السيرفر | `rbac.py`, `deps.py::require` | كل الـ API | `test_rbac_enforced_server_side` |
| M06: MFA | `security.py::totp_*`, `routers/auth.py` | `/api/auth/me/mfa/*` | `test_mfa_full_flow`, `test_admin_can_reset_lost_mfa` |
| M06: أسرار مشفّرة خارج المشروع | `secrets_store.py` | `/api/system/secrets` | `test_secrets_store_*`, `test_tampered_secret_file_fails_closed` |
| M06: سجل غير قابل للعبث | `audit.py` (hash chain) | `/api/audit/verify` | `test_audit_hash_chain_detects_tampering` |
| M06: CSP/CSRF | `main.py` | كل الطلبات | `test_csp_and_security_headers`, `test_csrf_header_required` |
| M06: سكريبتات معتمدة ومعزولة | `automation.py::execute_script` | `/api/system/scripts/*` | `test_script_library_upload_is_not_execute` |
| M06: نسخ واستعادة حقيقية | `automation.py::create_backup/verify_backup/restore_backup` | `/api/system/backups/*` | `test_backup_and_verified_restore` |
| Sheets: مطابقة بالكود، أعمدة محمية، تعارضات | `integrations/google.py::sync_entity` | `/api/system/integrations/{key}/sync` | `test_sheet_sync_rules`, `test_sync_without_credentials_is_pending` |
| Excel محلي كمصدر بيانات | `google.py::LocalXlsxTransport` | `/api/system/integrations/{key}/workbook` | `test_local_excel_source_edits_in_place_and_keeps_formulas` |
| استيراد/تصدير | `modules/imports.py` | `/api/import/*`, `/api/export/*` | `test_import_*`, `test_import_export_permissions` |
| التقارير المشتركة | `ops.py::REPORTS` | `/api/reports/*` | `test_reports_rbac_no_pii_and_safe_export`, `test_new_reports_registered` |
