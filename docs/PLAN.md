# План и состояние

Обновляется в конце каждой сессии: что сделано, что дальше, какие решения приняты. История в git, здесь только текущее.

## Где мы (24.09.2026)

- Сделано: стандарты (PRINCIPLES, STACK, TESTING), ADR-001 accepted, .env.example, settings.json, разведка mefi API (docs/mefi-api-notes.md), фикстура tests/fixtures/mefi/search_3_leads.json, конфиги исправлены по живым данным.
- Сейчас: сессия 3, фундамент. Shape подтверждён: docs/shapes/2026-09-24-foundation.md. Следующий шаг: /rigorous craft двумя коммитами, затем /rigorous critique.
- Дальше по docs/first-sessions.md: сессия 4 metrics/ (блокер: нет SB KPi.xlsx), сессия 5 планировщик и доставка, сессия 6 /settings, сессия 7 harden и деплой.

## Принятые решения (не обсуждать заново)

- Только ежедневный снапшот в 19:00, повтор 19:10. Частый опрос и speed-to-lead не в v1, w5 выключен, формула через ADR-002.
- Фильтры дат mefi не используются, окна считаются локально по снапшоту в Europe/Bucharest.
- Lifecycle mefi для категорий не используется, только status.name. status null → UNMAPPED. Алерт UNMAPPED только при первом появлении id.
- Исчезнувшие лиды: счётчик missing_since_previous в snapshot_runs, флага в схеме нет.
- raw jsonb без контактов клиента (список raw_strip в status-mapping.yaml), textarea-поля хранятся.
- Консультанты: config/managers.yaml, список от клиента ещё не получен.
- Отчёты читают только снапшот с snapshot_date = today и status = success, иначе пометка «данные mefi недоступны».
- Data revenire часто равна дню создания: правило для d3 решается в shape сессии 5.
- m19 в MVP только Excel, PDF на этапе 2.

## Ждём извне

- SB KPi.xlsx в docs/reference/ (блокер сессии 4).
- Список консультантов id → шоурум, active от директора Sofabelle.

## Правило контекста

Между сессиями /clear. Перед craft план должен быть в docs/shapes/. В конце сессии обновить этот файл и закоммитить вместе с работой.
