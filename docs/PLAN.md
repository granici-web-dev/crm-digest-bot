# План и состояние

Обновляется в конце каждой сессии: что сделано, что дальше, какие решения приняты. История в git, здесь только текущее.

## Где мы (25.09.2026)

- Сделано: стандарты (PRINCIPLES, STACK, TESTING), ADR-001 accepted, .env.example, settings.json, разведка mefi API (docs/mefi-api-notes.md), фикстура tests/fixtures/mefi/search_3_leads.json, конфиги исправлены по живым данным.
- Фундамент закрыт (сессия 3): craft 267f27f, 44f81c1; critique; дополнение к shape d996160; harden тремя коммитами: 41f2a4c (персональные данные, pre-commit), f9b73ff (терпимая модель лида, nullable is_duplicate, проверка полноты снапшота), 91db89e (устойчивость прогона, Retry-After через пейсер, время по Бухаресту и DST). 55 тестов зелёные, pre-commit чистый.
- Сессия 4, часть 1: эталон из SB KPi.xlsx (`scripts/build_etalon.py` → `tests/fixtures/etalon-2026-05.json`, 314 лидов, 6 консультантов, проверка на контакты клиента), `docs/kpi-definitions.md` переписан по формулам workbook, `config/kpi.yaml`, ADR-002 (v1 повторяет Excel). Speed-to-lead теперь ADR-003.
- Сессия 4, часть 2 (25.09.2026): SB KPi.xlsx составлен через ИИ, ADR-002 переписан (`ADR-002-kpi-brief-primary.md`): формулы по брифу, SPI предварительный и скрыт. Эталон пересобран по формулам брифа, значения Excel в `excel_reference`, Moaca Andreea сверена вручную (`meta.manual_check`). m6 выключен.
- Сессия 4, часть 3 (25.09.2026): metrics/ по shape `docs/shapes/2026-09-25-metrics.md` через tdd, коммиты 6cbccb6..907b0c8. `frame.py` (load_lead_frame, prepare_lead_frame, unknown_manager_ids), `kpi.py` (счётчики и 9 KPI по компании, шоуруму, консультанту), `spi.py` (provisional), `cockpit.py` (m5), `extra.py` (revenire, когорта, дельта). `test_kpi_matches_etalon` зелёный, 151 тест.
- Сейчас: /rigorous critique metrics/, затем harden. Потом сессия 5: планировщик и доставка; вызывающий код шлёт алерт по unknown_manager_ids.
- Расхождение с дополнением к shape: там сказано, что 5 от API и 4 записанных при порогах по умолчанию это failed, но по записанным там же порогам (max(5, 0.5 %) недополучено, max(10, 1 %) пропущено) это success. Реализованы пороги; тест переименован в test_snapshot_below_thresholds_is_success_with_alert_data. Если 5/4 должно падать, пороги в status-mapping.yaml нужно ужесточить.
- Для сессии 5: планировщик ловит исключение run_daily_snapshot и логирует только describe_error(error), без traceback со str(error). Незнакомые ключи лида (unknown_raw_key) пишутся в raw, не вырезаются: алерт обязателен.
- Локально тесты идут с `TESTCONTAINERS_RYUK_DISABLED=true`: docker pull образа ryuk зависает.
- Дальше по docs/first-sessions.md: сессия 5 планировщик и доставка, сессия 6 /settings, сессия 7 harden и деплой.

## Принятые решения (не обсуждать заново)

- KPI по брифу §3 (ADR-002): PARTNERSHIP и тестовые аккаунты вне LEADS, SC = клиенты среди визитов, деление на ноль → null («—»), ACR по last_contact_at. SB KPi.xlsx только тестовые данные, его ошибка ACR (ссылка на пустую B2) не воспроизводится.
- Баллы, штраф IRR, SPI, уровни, рекомендации предварительные: в config/kpi.yaml (status: provisional) и в metrics/, но не в отчётах MVP. m5 = 9 KPI с целями без SPI, m6 выключен.
- Только ежедневный снапшот в 19:00, повтор 19:10. Частый опрос и speed-to-lead не в v1, w5 выключен, формула через ADR-003.
- Фильтры дат mefi не используются, окна считаются локально по снапшоту в Europe/Bucharest.
- Lifecycle mefi для категорий не используется, только status.name. status null → UNMAPPED. Алерт UNMAPPED только при первом появлении id.
- Исчезнувшие лиды: счётчик missing_since_previous в snapshot_runs (без пропущенных сегодня), флага в схеме нет.
- success = снапшот полный: пороги полноты в секции snapshot status-mapping.yaml. is_duplicate nullable, NULL = неизвестно; метрики с исключением дублей падают на NULL в окне.
- raw jsonb без контактов клиента (список raw_strip в status-mapping.yaml), textarea-поля хранятся.
- Консультанты: config/managers.yaml, сверен со списком пользователей mefi 24.09.2026.
- Метрики по консультантам считают только active: true; лид с assigned_to.id вне managers.yaml находит unknown_manager_ids (metrics/ не логирует), алерт шлёт раннер отчёта (сессия 5).
- Лиды с assigned_to.id консультанта с test_account: true исключаются из всех метрик в prepare_lead_frame.
- Отчёты читают только снапшот с snapshot_date = today и status = success, иначе пометка «данные mefi недоступны».
- Data revenire часто равна дню создания: правило для d3 решается в shape сессии 5.
- m19 в MVP только Excel, PDF на этапе 2.
- Лид на assigned_to.id = 7 (Marketing Sofa) считается не взятым в работу; использовать в d2 (сессия 5).
- Источник B на этапе 2 читается через POST /admin/<модуль>/table с length=5000, не через обход HTML. Вход в mefi это блокер: reCAPTCHA плюс обязательная 2FA; цель официальный API, временная мера ручной вход с cookie.

## Ждём извне

- Кто такой Doja Ovidiu (Cluj, 18 лидов в эталоне), нет в пользователях mefi.
- Письмо директора в mefi про API для Oferte/Contracte/Facturi, теперь условие этапа 2.
- Калибровка SPI с владельцем после 2–3 месяцев снапшотов (плюс Excel 2024–2025), отдельным ADR.

## Правило контекста

Между сессиями /clear. Перед craft план должен быть в docs/shapes/. В конце сессии обновить этот файл и закоммитить вместе с работой.
