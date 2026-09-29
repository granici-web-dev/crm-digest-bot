# Индекс репозитория

В начале сессии читать этот файл и `docs/PLAN.md`. Остальное только по колонке «когда читать». Новый документ = новая строка.

| Файл | Что внутри | Когда читать |
|---|---|---|
| `CLAUDE.md` | Инварианты, ловушки mefi, запреты | Всегда, подгружается сам |
| `docs/PLAN.md` | Где мы, что дальше, принятые решения | Всегда |
| `docs/owner-questions.md` | Открытые вопросы владельцу, продавцам, маркетингу; ответы | Перед shape, который зависит от ответа клиента; при встрече с клиентом |
| `docs/success-criteria.md` | Этапы MVP → внедрение → v1.0 → сопровождение, ворота каждого этапа, метрики ценности | Перед переходом в Prod, перед разговором об оплате, когда решаем, брать ли доработку |
| `PRINCIPLES.md`, `STACK.md`, `TESTING.md` | Инженерные стандарты | Читает rigorous, вручную не открывать |
| `docs/brief.md` | Спецификация: источники, маппинг, расписания, реестр модулей, MVP | Shape нового модуля или источника |
| `docs/report-menu.md` | Названия, описания, mock-примеры RO/RU всех модулей | Shape и craft шаблонов |
| `docs/samples/weekly-manual-report-2026-07.md` | Ручной недельный отчёт: формат и правило рабочего окна | Shape w1 и любой недельной метрики |
| `docs/samples/daily-seller-report-sample.md` | Настоящий ежедневный отчёт продавцов за 12.08.2026 | Shape и craft d1, формат повторять один в один |
| `docs/kpi-definitions.md` | Формулы KPI и SPI, пороги, веса | `metrics/`, вопросы про формулы |
| `docs/mefi-api-notes.md` | Проверенные факты mefi API, лимиты, реальные id | Клиент mefi, снапшот |
| `docs/mefi-clients-notes.md` | clients API: поля, нет ссылки на лид, счёт по месяцам и state, связь с `converted_at`, вывод про Contract Cantitate | Источник клиентов, счёт контрактов |
| `docs/mefi-api-reference/` | Документация mefi из соседнего проекта | Только если в notes нет ответа, по одному файлу |
| `docs/mefi-api-reference/clients-read.md` | Документация mefi `clients:read` от вендора (28.09.2026): search, профиль, контакты, заметки, лимиты | Только если в `mefi-clients-notes.md` нет ответа |
| `docs/mefi-session-notes.md` | Источник B: endpoints таблиц mefi, разбор ячеек, связи сущностей, риск входа | Этап 2, синк Oferte/Contracte/Facturi |
| `config/status-mapping.yaml` | Статусы → категории, кастомные поля, источники | Категоризация |
| `config/modules.yaml` | Реестр модулей отчётов и источников | Модули, `/settings`, планировщик |
| `config/kpi.yaml` | Пороги KPI, уровни SPI, дни для ACR | `metrics/`, пороги |
| `config/managers.yaml` | Консультанты: id, имя, шоурум, active | Метрики по менеджерам |
| `docs/acceptance/` | Как мерить ворота M6–M9, чек-лист M7, результаты прогонов в зачёт | Приёмка MVP, прогон `eval chat`, `audit privacy`, `restore-drill.sh` |
| `docs/shapes/` | Подтверждённые планы фич | Craft и critique своей фичи |
| `docs/decisions/` | ADR | Решения, которые меняют формулы или стек |
| `docs/first-sessions.md` | Порядок сессий и промпты | Планирование следующей сессии |
| `docs/research.md` | Обзор рынка, почему пишем сами | При соблазне взять готовое |
| `tests/fixtures/mefi/` | Обезличенные ответы mefi | Тесты клиента |
| `tests/fixtures/etalon-2026-05.json` | Регрессионный эталон: лиды из SB KPi.xlsx без контактов, ожидаемые KPI по формулам брифа, значения Excel для сравнения | Тесты `metrics/` |
| `scripts/build_etalon.py` | Сборка эталона из `docs/reference/SB KPi.xlsx` с проверкой на контакты клиента | Пересборка эталона |
