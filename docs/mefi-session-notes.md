# mefi: разведка сессионного доступа (источник B)

Дата: 24.09.2026. Метод: вход в интерфейс mefi в браузере, наблюдение сетевых запросов и вызовы `fetch` из консоли. Данные клиентов не сохранялись, здесь только структура. Дополняет `docs/mefi-api-notes.md` (источник A).

## Платформа

mefi это форк Perfex CRM (CodeIgniter). Вход: `GET /admin` → форма `POST /authentication/admin` с полями `email`, `password`, `remember`, `csrf_token_name`. **На форме входа reCAPTCHA v2** (`data-sitekey` присутствует). mefi объявила **обязательную 2FA** (Google Authenticator или код на e-mail), дедлайн не указан.

CSRF: cookie `csrf_cookie_name`, значение передаётся полем `csrf_token_name` в каждом POST. Сессионная cookie HttpOnly, имя и срок жизни из браузера не видны. Клиент периодически дёргает `GET /admin/session_check?csrf_token_name=…`.

Роли: в аккаунте одна роль `Agenti vanzari` (7 пользователей), остальные администраторы. Perfex поддерживает права view/create/edit/delete по модулям, значит роль «только чтение» создать можно.

## Как отдаются списки

Каждый список грузится через `POST /admin/<модуль>/table` в формате DataTables legacy: `{draw, iTotalRecords, iTotalDisplayRecords, aaData[, footer, sums]}`. Ячейки `aaData` это HTML-строки, значения нужно вытаскивать из разметки. Минимальный body: `csrf_token_name`, `draw=1`, `start=0`, `length=N`. **`length=5000` отдаёт всю таблицу одним ответом** (проверено на 1226 офертах). Заголовок `X-Requested-With: XMLHttpRequest`.

| Модуль | Endpoint | Записей 24.09.2026 | Колонки (в порядке ячеек) |
|---|---|---|---|
| Oferte | `POST /admin/proposals/table` | 1226 | -, Oferta (ссылка `/admin/proposals/list_proposals/{id}`), Titlu, Nume client (ссылка `/admin/clients/client/{id}`), Persoana de contact, Acțiuni, Tracking, Ofertat de (фирма-эмитент, не агент), Total, Data creare, Data expirare, Status, Legătură oferta (ссылка на контракт `/admin/contracts/contract/{id}`, если есть) |
| Contracte | `POST /admin/contracts/table` | 296 | Nr. contract, Titlu (ссылка `/admin/contracts/contract/{id}`), Nume client (ссылка на клиента), Acțiuni, Tracking, Valoare fără TVA, Valoare cu TVA, Data emitere, Data finalizare, Status semnătură, Tip anexă/contract, Responsabil |
| Facturi | `POST /admin/invoices/table` | 720 | -, Serie și nr. (ссылка `/admin/invoices/list_invoices/{id}`), Nume client, Nume contact, Acțiuni, Tracking, Total RON, TVA RON, Data emitere, Status (`Achitată` …), Data scadentă, Legatura proiect, Firma emitenta |
| Încasări | `POST /admin/payments/table` | 703 | ID (ссылка `/admin/payments/payment/{id}`), Acțiuni, Data, Factura # (ссылка на счёт), Client, Modalitatea de încasare, Total Valută, Total RON, Referința, Înregistrat de |
| Clienți finali | `POST /admin/clients/table` | 802 | -, Companie (ссылка `/admin/clients/client/{id}`), Acțiuni, Nume, E-mail, Telefon, Showroom, Grupuri, Status, Data creare, Responsabili, Facturi achitate RON, Sursa, UTM_Source |

Детальный отчёт оферт: `POST /admin/reports/proposals_report` (страница `/admin/rapoarte/oferte?type=rdo`), body как у таблиц плюс `months-report=all`; 12 колонок: Oferta #, Titlu, Persoana de contact, **Agent de vânzări**, Companie, Data creare, Data expirare, Total, Total cu TVA, TVA, Reducere, Status. Есть фильтры `report-from`, `report-to`, `proposal_status`, `proposals_sale_agents`. Это единственное место, где у оферты виден агент продаж; в `/proposals/table` его нет.

Другие готовые отчёты: `/admin/rapoarte/contracte?type=rc|cdp|vcdt|gdmda`, `/admin/rapoarte/incasari?type=rdi|gdmda`, `/admin/rapoarte/clienti_potentiali`, `/admin/rapoarte/clienti?type=motive`.

## Разбор ячеек

- Текущий статус: в ячейке dropdown со всеми вариантами; текущий в `<a class="dropdown-toggle …"><div title="…">` и в `<span class="ellipsis-text">`. Для оферт ещё `proposal-status-{id}` в классе.
- Showroom клиента: dropdown, текущее значение это текст `<a class="… dropdown-toggle">` до иконки.
- Sursa клиента: `<select name="client_source_table">`, выбранный `<option selected>`; id источников те же, что в лидах (7 = Arhirtect, 15 = BIFE 2026).
- Суммы: `19505,20 RON` (запятая как десятичный разделитель, пробелов тысяч нет). Даты: `dd-mm-yyyy`, у клиентов `dd-mm-yyyy hh:mm:ss`.

## Статусы оферт (24.09.2026)

| Статус | Кол-во |
|---|---|
| Schiță | 826 |
| Trimisă | 168 |
| Acceptat | 204 |
| Ofertă revizuită | 7 |
| Negocieri | 2 |
| Refuzată | 4 |
| Anulat | 15 |

Две трети оферт остаются в `Schiță`: продавцы не переводят статус после отправки. Для метрик по офертам это надо учитывать (оферта «сделана» по факту создания, а не по статусу `Trimisă`).

Оферты по агентам (из детального отчёта): Raileanu Leon 312, Roibu Valeria 303, Godja Adina Maria 248, Dragoi Mihaela 179, Marc Andra 118, Moaca Andreea 50, остальные единицы. Имя агента здесь без двойного пробела, в отличие от `assigned_to` в API лидов.

## Связи между сущностями

- Оферта → клиент: ссылка на `/admin/clients/client/{id}` есть у 282 из 1226 оферт. **Оферта → лид: ни одной ссылки `/admin/leads/`** в таблице оферт. Оферты в mefi выписываются на клиента, не на лид.
- Клиент → лид: на странице клиента `/admin/clients/client/{id}` есть ссылка `/admin/leads/index/{lead_id}` (клиент, сконвертированный из лида). В мастере экспорта клиентов есть колонка `leadid` («ID Lead asociat»).
- Счёт → клиент, платёж → счёт: ссылки в ячейках.

Значит, цепочка лид → оферта строится только через клиента: лид → клиент (`leadid`) → оферты клиента. Для лидов без клиента (оферта до контракта) связи нет; для `L2O` остаётся кастомное поле `Ofertat` из источника A.

## Экспорт

`/admin/clients/export_viewer`: мастер в 4 шага, `POST` на тот же URL с полями `export_type` (profile | contacts | notes), `cols[]` (в profile есть `leadid`, `status_client`, `source`, `datecreated`, `last_status_change`), фильтры (`clients_created_from/to`, `statuses[]`, `sources[]`, `responsible_admin[]`, …), `format` (xlsx | csv | xml). Простой POST без полей мастера вернул HTML; точный набор обязательных полей не выяснен. Кнопка `Export ▼` над таблицами это клиентский DataTables Buttons, экспортирует только видимую страницу, для синка бесполезна.

## Выводы для этапа 2

1. Источник B = пять `POST …/table` с `length=5000` плюс `POST /admin/reports/proposals_report` для агентов. Никакого обхода HTML-страниц, Playwright нужен только для входа.
2. **Вход это главный риск**: reCAPTCHA на форме плюс обязательная 2FA. Скриптовый логин по паролю невозможен. Варианты: (а) ручной вход раз в N дней с `remember` и переиспользование cookie ботом, срок жизни сессии надо измерить; (б) 2FA по e-mail на служебный ящик, который читает бот, и решение reCAPTCHA остаётся проблемой; (в) официальный API для Oferte/Contracte/Facturi от mefi, письмо директора (бриф §11.2). Рекомендация: (в) как цель, (а) как временная мера.
3. Сервисный пользователь: создать роль «только просмотр» (Perfex это умеет), без прав на редактирование.
4. Все таблицы содержат имена, телефоны и e-mail клиентов: при записи в Postgres вырезать так же, как в снапшоте лидов (`raw_strip`).
