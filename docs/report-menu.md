# Меню отчётов: названия, описания и примеры RO/RU

Источник: `sofabelle-digest-checklist.html` (12.09.2026), массив ITEMS. Это тексты для шаблонов `templates/*.ro.j2` и `*.ru.j2`. Примеры это mock, цифры вымышленные. Тег: now = MVP, access = нужен доступ, locked = заблокировано, phase2 = этап 2.

## Daily (19:30)

### d1 · Raport automat în formatul consilierilor / Автоматический отчёт в формате консультантов

Тег: now

**RO:** Pe fiecare showroom + total, direct din CRM, fără intervenția consilierilor.

**RU:** По каждому шоуруму + итого, из CRM, без участия продавцов.

Пример RO:

```
Sofabelle Brașov · 11.09
Leads Mail/FB/IG: 3 · Telefon: 1 · WhatsApp: 2
Designer/Colaboratori: 0 · Showroom (revenire): 1
Vizita in showroom: 2 · Oferta: 1 · Contract: 0
———
TOTAL · Leads 14 · Vizite 6 · Oferte 4 · Contracte 1
```


### d2 · Lead-uri fără niciun contact azi / Лиды без единого касания за день

Тег: now

**RO:** Nici status, nici notiță — pe consilier.

**RU:** Ни статуса, ни заметки — по консультанту.

Пример RO:

```
⚠ 3 lead-uri neatinse: Roibu 2 (cel mai vechi: 6h), Dragoi 1 (2h)
```

Пример RU:

```
⚠ 3 лида без касания: Roibu 2 (самый старый: 6 ч), Dragoi 1 (2 ч)
```

### d3 · Termene „Data revenire” depășite / Просроченные «Data revenire»

Тег: now

**RO:** Pe consilier, cu numărul de zile de întârziere.

**RU:** По консультанту, с числом дней просрочки.

Пример RO:

```
⏰ Reveniri restante: Godja 4, Moaca 2, Raileanu 1
```

Пример RU:

```
⏰ Просроченные возвраты: Godja 4, Moaca 2, Raileanu 1
```

### d4 · Oferte active fără mișcare > 14 zile / Активные оферты без движения > 14 дней

Тег: now

**RO:** Contor și cine.

**RU:** Счётчик и кто.

Пример RO:

```
📄 Oferte blocate >14 zile: 17 (+2 față de ieri) — Cluj 9, Brașov 5, București 3
```

Пример RU:

```
📄 Зависшие оферты >14 дней: 17 (+2 к вчера) — Cluj 9, Brașov 5, București 3
```

### d5 · Anomalii ale zilei / Аномалии дня

Тег: now

**RO:** Site-ul nu a adus niciun lead (posibil webhook căzut); val de IRELEVANT la un consilier.

**RU:** С сайта ни одного лида (возможно, упал webhook); всплеск IRELEVANT у консультанта.

Пример RO:

```
🔴 0 lead-uri de pe site azi (media ultimelor 7 zile: 5) — verificați formularul
```

Пример RU:

```
🔴 0 лидов с сайта сегодня (среднее за 7 дней: 5) — проверьте форму
```

### d6 · Comparație cu aceeași zi a săptămânii trecute / Сравнение с тем же днём прошлой недели

Тег: now

**RO:** Lead-uri și contracte.

**RU:** Лиды и контракты.

Пример RO:

```
Lead-uri azi: 11 (joia trecută: 8) · Contracte: 1 (0)
```

Пример RU:

```
Лидов сегодня: 11 (в прошлый четверг: 8) · Контрактов: 1 (0)
```

### d7 · O cifră de trend: conversia pe ultimele 30 de zile / Одна цифра тренда: конверсия за 30 дней

Тег: now

**RO:** Ca să nu așteptați sfârșitul lunii.

**RU:** Чтобы не ждать конца месяца.

Пример RO:

```
SCR 30 zile: 9,2% (cele 30 anterioare: 7,8%) ↑
```

Пример RU:

```
SCR за 30 дней: 9,2% (предыдущие 30: 7,8%) ↑
```

### d8 · Alertă imediată la o problemă critică / Мгновенный алерт при критичной проблеме

Тег: now

**RO:** Nu la 19:30, ci în momentul în care apare.

**RU:** Не в 19:30, а в момент возникновения.

Пример RO:

```
🔴 14:05 — de la 10:00 niciun lead de pe site, în mod normal 3–4 până acum
```

Пример RU:

```
🔴 14:05 — с 10:00 ни одного лида с сайта, обычно к этому времени 3–4
```

## Weekly (понедельник 09:00)

### w1 · Lead-uri pe zi × showroom și showroom × sursă / Лиды по дням × шоурум и шоурум × источник

Тег: now

**RO:** Site, WhatsApp, Telefon, Mail, Meta ADS.

**RU:** Site, WhatsApp, Telefon, Mail, Meta ADS.

Пример RO:

```
Săpt. 36 · 52 lead-uri: Brașov 19 · București 21 · Cluj 12
Site 24 · WhatsApp 13 · Telefon 11 · Mail 4
```

Пример RU:

```
Нед. 36 · 52 лида: Brașov 19 · București 21 · Cluj 12
Site 24 · WhatsApp 13 · Telefon 11 · Mail 4
```

### w2 · Vizite în showroom / Визиты в шоурум

Тег: now

**RO:** Separat de lead-urile digitale.

**RU:** Отдельно от цифровых лидов.

Пример RO:

```
Vizite showroom: 14 (Brașov 5, București 6, Cluj 3)
```

Пример RU:

```
Визиты в шоурум: 14 (Brașov 5, București 6, Cluj 3)
```

### w3 · Pâlnia săptămânii / Воронка недели

Тег: now

**RO:** Lead-uri → utile → oferte → contracte.

**RU:** Лиды → полезные → оферты → контракты.

Пример RO:

```
52 lead-uri → 41 utile (–11 irelevante) → 19 oferte → 4 contracte
Lead→Ofertă 46% · Ofertă→Contract 21%
```

Пример RU:

```
52 лида → 41 полезных (–11 нерелевантных) → 19 оферт → 4 контракта
Лид→Оферта 46% · Оферта→Контракт 21%
```

### w4 · Unde am pierdut și de ce / Где потеряли и почему

Тег: now

**RO:** Motive de pierdere, inclusiv termenul de producție (TIMP).

**RU:** Причины потерь, включая срок производства (TIMP).

Пример RO:

```
Pierdute 23: Nu a răspuns 9 · Buget 7 · Produs nepotrivit 3 · Termen producție 2 · Concurență 1 · A refuzat 1
```

Пример RU:

```
Потеряно 23: Не ответил 9 · Бюджет 7 · Продукт не подошёл 3 · Срок производства 2 · Конкурент 1 · Отказ 1
```

### w5 · Viteza primului contact / Скорость первого контакта

Тег: now

**RO:** De la sosirea lead-ului până la prima acțiune, pe showroom.

**RU:** От прихода лида до первого действия, по шоурумам.

Пример RO:

```
Prim contact: mediană 1h 40min · 23% din lead-uri > 4h · cel mai lent: Cluj 3h 10min
```

Пример RU:

```
Первый контакт: медиана 1 ч 40 мин · 23% лидов > 4 ч · медленнее всего: Cluj 3 ч 10 мин
```

### w6 · Ce campanii aduc lead-uri irelevante / Какие кампании приводят нерелевантные лиды

Тег: now

**RO:** Pe UTM_Campaign.

**RU:** По UTM_Campaign.

Пример RO:

```
Irelevante pe campanie: „BZA_Cluj_Website_Leads 03” 42% (11 din 26) · „Reducere30 Imperial” 12%
```

Пример RU:

```
Нерелевантные по кампаниям: «BZA_Cluj_Website_Leads 03» 42% (11 из 26) · «Reducere30 Imperial» 12%
```

### w7 · Apeluri pierdute fără lead creat / Пропущенные звонки без созданного лида

Тег: now

**RO:** Din centrala telefonică, după conectarea telefoniei IP.

**RU:** Из телефонной станции, после подключения IP-телефонии.

Пример RO:

```
Apeluri pierdute fără lead: 6 (București 4, Brașov 2)
```

Пример RU:

```
Пропущенные звонки без лида: 6 (București 4, Brașov 2)
```

### w8 · Comparație cu săptămâna trecută / Сравнение с прошлой неделей

Тег: now

**RO:** Pe fiecare cifră.

**RU:** По каждой цифре.

Пример RO:

```
Lead-uri 52 (–8%) · Oferte 19 (+12%) · Contracte 4 (=)
```

Пример RU:

```
Лиды 52 (–8%) · Оферты 19 (+12%) · Контракты 4 (=)
```

### w9 · Cost per lead / lead util / contract, pe canal / Стоимость лида / полезного лида / контракта по каналам

Тег: access

**RO:** Cheltuieli din conturile de reclamă ÷ lead-uri din CRM.

**RU:** Расходы из кабинетов ÷ лиды из CRM.

Пример RO:

```
Meta: 48 lei/lead · 61 lei/lead util · 780 lei/contract
Google: 72 / 85 / 1.140 lei
```

Пример RU:

```
Meta: 48 лей/лид · 61 лей/полезный лид · 780 лей/контракт
Google: 72 / 85 / 1 140 лей
```

### w10 · Campanii care cheltuie dar nu aduc lead-uri / Кампании, которые тратят, но не дают лидов

Тег: access

**RO:** În ultimele 7 zile.

**RU:** За последние 7 дней.

Пример RO:

```
Fără lead-uri 7 zile: „TikTok Belle Soft Sept” — 640 lei cheltuiți
```

Пример RU:

```
Без лидов 7 дней: «TikTok Belle Soft Sept» — потрачено 640 лей
```

### w11 · Trafic site → lead-uri, pe pagini / Трафик сайта → лиды, по страницам

Тег: access

**RO:** Din GA4.

**RU:** Из GA4.

Пример RO:

```
GA4: 3.120 sesiuni → 24 lead-uri (0,77%) · cea mai bună pagină: /canapea-belle 1,9%
```

Пример RU:

```
GA4: 3 120 сессий → 24 лида (0,77%) · лучшая страница: /canapea-belle 1,9%
```

### w12 · Fișier Excel atașat / Excel во вложении

Тег: now

**RO:** Tabelele zi × showroom, showroom × sursă, lista lead-urilor.

**RU:** Таблицы день × шоурум, шоурум × источник, список лидов.

Пример RO:

```
📎 sofabelle_sapt36.xlsx
```


## Monthly (1-е число 09:00)

### m1 · Grafic 1 — rezumatul lunii / Картинка 1 — итог месяца

Тег: locked

**RO:** Contracte și valoare vs luna trecută și vs aceeași lună anul trecut.

**RU:** Контракты и сумма vs прошлый месяц и vs тот же месяц прошлого года.

Пример RO:

```
AUGUST 2026 · 17 contracte (iulie: 14, +21%) · 296.400 lei (+23%) · vs aug 2025: +9%
```

Пример RU:

```
АВГУСТ 2026 · 17 контрактов (июль: 14, +21%) · 296 400 лей (+23%) · к авг 2025: +9%
```

### m2 · Grafic 2 — pâlnia lunii pe showroom / Картинка 2 — воронка месяца по шоурумам

Тег: now

**RO:** Lead-uri → utile → oferte → contracte.

**RU:** Лиды → полезные → оферты → контракты.

Пример RO:

```
Brașov 78 → 61 → 29 → 6 · București 94 → 70 → 33 → 7 · Cluj 55 → 44 → 20 → 4
```


### m3 · Grafic 3 — trend 6 luni / Картинка 3 — тренд за 6 месяцев

Тег: now

**RO:** Lead-uri, contracte, conversie.

**RU:** Лиды, контракты, конверсия.

Пример RO:

```
Mar 6,1% · Apr 7,4% · Mai 8,0% · Iun 7,2% · Iul 8,9% · Aug 9,7%
```

Пример RU:

```
Мар 6,1% · Апр 7,4% · Май 8,0% · Июн 7,2% · Июл 8,9% · Авг 9,7%
```

### m4 · Conversie (SCR) pe showroom și consilier, cu ținte / Конверсия (SCR) по шоурумам и консультантам, с целями

Тег: now

**RO:** Elită >10%, bine >7%, minim >5%.

**RU:** Элита >10%, хорошо >7%, минимум >5%.

Пример RO:

```
SCR: Brașov 9,8% ✓ · București 10,0% ✓ · Cluj 9,1% ✓
```


### m5 · Manager Cockpit — cei 9 KPI pe consilier / Manager Cockpit — 9 KPI по консультанту

Тег: now

**RO:** SCR, CDR, PLR, SC, PFR, ACR, L2O, O2C, IRR, scor SPI, nivel, recomandare.

**RU:** SCR, CDR, PLR, SC, PFR, ACR, L2O, O2C, IRR, балл SPI, уровень, рекомендация.

Пример RO:

```
Raileanu Leon · SPI 74 · Bine
SCR 11,2% · CDR 91% · PLR 22% · SC 24% · PFR 8% · ACR 18% · L2O 52% · O2C 21% · IRR 17%
→ Recomandare: reduceți ofertele active >14 zile
```

Пример RU:

```
Raileanu Leon · SPI 74 · Хорошо
SCR 11,2% · CDR 91% · PLR 22% · SC 24% · PFR 8% · ACR 18% · L2O 52% · O2C 21% · IRR 17%
→ Рекомендация: сократить активные оферты >14 дней
```

### m6 · Clasament consilieri după SPI / Рейтинг консультантов по SPI

Тег: now

**RO:** 

**RU:** 

Пример RO:

```
1. Godja 81 · 2. Raileanu 74 · 3. Dragoi 69 · 4. Roibu 66 · 5. Moaca 61 · 6. Doja 58
```


### m7 · Conversie pe sursă și pe campanie / Конверсия по источникам и кампаниям

Тег: now

**RO:** 

**RU:** 

Пример RO:

```
Showroom 24% · WhatsApp 11% · Telefon 9% · Site 6% · Mail 4%
```


### m8 · Motive de pierdere — evoluție față de luna trecută / Причины потерь — динамика к прошлому месяцу

Тег: now

**RO:** 

**RU:** 

Пример RO:

```
Buget 31% (iul 27%) ↑ · Nu a răspuns 24% (28%) ↓ · Termen producție 8% (5%) ↑ · Concurență 9% (6%) ↑
```

Пример RU:

```
Бюджет 31% (июль 27%) ↑ · Не ответил 24% (28%) ↓ · Срок производства 8% (5%) ↑ · Конкурент 9% (6%) ↑
```

### m9 · Conversie de cohortă / Когортная конверсия

Тег: now

**RO:** Câte din lead-urile lunii trecute au devenit contracte până azi — contractele se semnează în 30–60 zile.

**RU:** Сколько лидов прошлого месяца стали контрактами к сегодня — контракты подписываются через 30–60 дней.

Пример RO:

```
Lead-urile din iulie: 8,9% la 31.07 → 12,4% la 31.08
```

Пример RU:

```
Лиды июля: 8,9% на 31.07 → 12,4% на 31.08
```

### m10 · Număr contracte și valoare medie / Число контрактов и средняя сумма

Тег: locked

**RO:** Din modulul Contracte.

**RU:** Из модуля Contracte.

Пример RO:

```
17 contracte · valoare medie 17.435 lei
```

Пример RU:

```
17 контрактов · средняя сумма 17 435 лей
```

### m11 · Clienți repetitivi / Повторные клиенты

Тег: now

**RO:** Al doilea contract pe același client.

**RU:** Второй контракт на того же клиента.

Пример RO:

```
Contracte repetate: 2 din 17 (12%)
```

Пример RU:

```
Повторных контрактов: 2 из 17 (12%)
```

### m12 · Cost per contract (CPO) și CAC pe canal, buget total / Стоимость контракта (CPO) и CAC по каналам, общий бюджет

Тег: access

**RO:** 

**RU:** 

Пример RO:

```
Buget reclamă: 14.200 lei · CPO Meta 1.020 lei · Google 1.480 lei · TikTok — fără contracte
```

Пример RU:

```
Бюджет рекламы: 14 200 лей · CPO Meta 1 020 лей · Google 1 480 лей · TikTok — без контрактов
```

### m13 · Randamentul reclamei / Отдача рекламы

Тег: access

**RO:** Valoare contracte din digital ÷ cheltuieli.

**RU:** Сумма контрактов из digital ÷ расходы.

Пример RO:

```
Digital: 9 contracte, 164.000 lei, 14.200 lei cheltuiți → 11,5×
```

Пример RU:

```
Digital: 9 контрактов, 164 000 лей, 14 200 лей расходов → 11,5×
```

### m14 · Încasări, avansuri, marjă / Поступления, предоплаты, маржа

Тег: locked

**RO:** Din Facturi + calculațiile de cost.

**RU:** Из Facturi + калькуляции себестоимости.

Пример RO:

```
Încasări: 208.400 lei · Avansuri noi: 96.000 lei · Marjă brută medie: 41%
```

Пример RU:

```
Поступления: 208 400 лей · Новые предоплаты: 96 000 лей · Средняя валовая маржа: 41%
```

### m15 · Reoferte, contracte anulate / Повторные оферты, отменённые контракты

Тег: locked

**RO:** Din modulele Oferte / Contracte.

**RU:** Из модулей Oferte / Contracte.

Пример RO:

```
Reoferte: 6 · Anulate: 1
```

Пример RU:

```
Реоферт: 6 · Отменено: 1
```

### m16 · Top produse după valoare contract și după marjă / Топ продуктов по сумме контрактов и по марже

Тег: locked

**RO:** 

**RU:** 

Пример RO:

```
1. Belle Extensibil 6 · 2. Belle Soft 4 · 3. Life 3 · 4. Nocturne 2 · 5. Imperial 2
```


### m17 · Concurenți menționați de clienți / Конкуренты, которых называют клиенты

Тег: phase2

**RO:** Extras automat din notițele consilierilor.

**RU:** Автоматически из заметок консультантов.

Пример RO:

```
Concurenți: Mobexpert 4 · Dumonde 2 · Jysk 2 · Dalin 1 · eMag 1
```

Пример RU:

```
Конкуренты: Mobexpert 4 · Dumonde 2 · Jysk 2 · Dalin 1 · eMag 1
```

### m18 · Modele cerute de clienți / Модели, которые спрашивают клиенты

Тег: phase2

**RO:** Din notițe, inclusiv de cei care nu au cumpărat.

**RU:** Из заметок, включая тех, кто не купил.

Пример RO:

```
Cele mai cerute: Belle Extensibil 31 · Belle Soft 22 · Life 14 · Nocturne 9 · Free Comfort 7
```

Пример RU:

```
Самые востребованные: Belle Extensibil 31 · Belle Soft 22 · Life 14 · Nocturne 9 · Free Comfort 7
```

### m19 · Fișier Excel + PDF atașat / Excel + PDF во вложении

Тег: now

**RO:** 

**RU:** 

Пример RO:

```
📎 sofabelle_august_2026.xlsx · 📎 sofabelle_august_2026.pdf
```


## Yearly (5 января 09:00)

### y1 · Lead-uri și contracte pe luni — sezonalitate / Лиды и контракты по месяцам — сезонность

Тег: now

**RO:** 

**RU:** 

Пример RO:

```
Vârf: octombrie–noiembrie (18% din contracte) · minim: iulie–august
```

Пример RU:

```
Пик: октябрь–ноябрь (18% контрактов) · минимум: июль–август
```

### y2 · Comparație an vs an / Сравнение год к году

Тег: now

**RO:** Din 2027; istoricul 2024–2025 se încarcă din Excel.

**RU:** С 2027; история 2024–2025 загружается из Excel.

Пример RO:

```
2026: 2.140 lead-uri (+14%) · 187 contracte (+22%) · SCR 9,1% (2025: 8,4%)
```

Пример RU:

```
2026: 2 140 лидов (+14%) · 187 контрактов (+22%) · SCR 9,1% (2025: 8,4%)
```

### y3 · Showroom-ul anului și consilierul anului / Шоурум года и консультант года

Тег: now

**RO:** După SPI mediu.

**RU:** По среднему SPI.

Пример RO:

```
Showroom: București (SCR 10,3%) · Consilier: Godja Adina Maria (SPI mediu 78)
```

Пример RU:

```
Шоурум: București (SCR 10,3%) · Консультант: Godja Adina Maria (средний SPI 78)
```

### y4 · Evoluția motivelor de pierdere pe trimestre / Динамика причин потерь по кварталам

Тег: now

**RO:** 

**RU:** 

Пример RO:

```
Buget: T1 34% → T4 26% ↓ · Concurență: T1 6% → T4 11% ↑
```

Пример RU:

```
Бюджет: Q1 34% → Q4 26% ↓ · Конкурент: Q1 6% → Q4 11% ↑
```

### y5 · Sursele care au crescut / scăzut / Какие источники выросли / упали

Тег: now

**RO:** 

**RU:** 

Пример RO:

```
WhatsApp +41% · Site +9% · Telefon –12%
```


### y6 · Buget reclamă pe an și randament pe canal / Рекламный бюджет за год и отдача по каналам

Тег: access

**RO:** 

**RU:** 

Пример RO:

```
Total: 168.000 lei · CPO mediu 1.190 lei · cel mai eficient canal: Meta
```

Пример RU:

```
Всего: 168 000 лей · средний CPO 1 190 лей · самый эффективный канал: Meta
```

### y7 · Valoare contracte, încasări, marjă pe an / Сумма контрактов, поступления, маржа за год

Тег: locked

**RO:** 

**RU:** 

Пример RO:

```
Contracte: 3,4 mil. lei · Încasări: 3,1 mil. lei · Marjă brută: 40%
```

Пример RU:

```
Контракты: 3,4 млн лей · Поступления: 3,1 млн лей · Валовая маржа: 40%
```

### y8 · Top concurenți și top modele cerute pe an / Топ конкурентов и топ моделей за год

Тег: phase2

**RO:** 

**RU:** 

Пример RO:

```
Mobexpert 38 mențiuni · Belle Extensibil 340 cereri
```

Пример RU:

```
Mobexpert 38 упоминаний · Belle Extensibil 340 запросов
```

## Chat

### c1 · Cine e cel mai bun consilier luna asta? / Кто лучший консультант в этом месяце?

Тег: now

**RO:** 

**RU:** 

Пример RO:

```
După SPI, septembrie: Godja Adina Maria — 81 puncte (Elită). Conduce la SCR 12,4% și CDR 94%. Pe locul doi — Raileanu Leon, 74.
```

Пример RU:

```
По SPI за сентябрь: Godja Adina Maria — 81 балл (Элита). Лидирует по SCR 12,4% и CDR 94%. Второй — Raileanu Leon, 74.
```

### c2 · Ce conversie are Leon din București? / Какая конверсия у Леона из Бухареста?

Тег: now

**RO:** 

**RU:** 

Пример RO:

```
Raileanu Leon, București, septembrie (1–12.09): 38 lead-uri → 31 utile → 4 contracte. SCR 12,9%. În august: 11,2%.
```

Пример RU:

```
Raileanu Leon, București, сентябрь (1–12.09): 38 лидов → 31 полезный → 4 контракта. SCR 12,9%. За август: 11,2%.
```

### c3 · Câte lead-uri au venit de pe site săptămâna asta? / Сколько лидов пришло с сайта на этой неделе?

Тег: now

**RO:** 

**RU:** 

Пример RO:

```
Săpt. 37, de pe site: 24 lead-uri (Brașov 9, București 10, Cluj 5). Săptămâna trecută: 28.
```

Пример RU:

```
Нед. 37, с сайта: 24 лида (Brașov 9, București 10, Cluj 5). На прошлой неделе: 28.
```

### c4 · De ce are Cluj puține contracte? / Почему в Cluj мало контрактов?

Тег: now

**RO:** 

**RU:** 

Пример RO:

```
Cluj, septembrie: 55 lead-uri, 4 contracte (SCR 9,1%, în normă). Ce diferă: 17 oferte active mai vechi de 14 zile — cel mai mare blocaj. Pierderi: Buget 38% față de 27% media.
```

Пример RU:

```
Cluj, сентябрь: 55 лидов, 4 контракта (SCR 9,1%, норма). Отличие: 17 активных оферт старше 14 дней — самое большое зависание. Потери: Бюджет 38% против 27% в среднем.
```

### c5 · Compară august cu iulie pe showroom-uri / Сравни август и июль по шоурумам

Тег: now

**RO:** 

**RU:** 

Пример RO:

```
Contracte: Brașov 6 → 5 · București 7 → 8 · Cluj 4 → 4. Lead-uri: 227 → 251 (+11%). SCR: 8,9% → 9,7%.
```

Пример RU:

```
Контракты: Brașov 6 → 5 · București 7 → 8 · Cluj 4 → 4. Лиды: 227 → 251 (+11%). SCR: 8,9% → 9,7%.
```

### c6 · Cine nu a prelucrat lead-urile de azi? / Кто не обработал сегодняшние лиды?

Тег: now

**RO:** 

**RU:** 

Пример RO:

```
Fără contact la 16:20: Roibu — 2 lead-uri (cel mai vechi 5h), Dragoi — 1 (2h). Ceilalți — 0.
```

Пример RU:

```
Без касания на 16:20: Roibu — 2 лида (старший 5 ч), Dragoi — 1 (2 ч). Остальные — 0.
```

### c7 · Cât am încasat în august? / Сколько мы заработали в августе?

Тег: locked

**RO:** 

**RU:** 

Пример RO:

```
August: 17 contracte în valoare de 296.400 lei (iulie: 14, 241.000 lei, +23%). Încasări: 208.400 lei, din care avansuri la contracte noi 96.000. Date din Contracte/Facturi la 01.09.
```

Пример RU:

```
Август: 17 контрактов на 296 400 лей (июль: 14 на 241 000, +23%). Поступления: 208 400 лей, из них предоплаты по новым контрактам 96 000. Данные из Contracte/Facturi на 01.09.
```

### c8 · Ce prognoză e pentru octombrie? / Какой прогноз на октябрь?

Тег: now

**RO:** Întrebare în afara datelor — botul refuză și oferă fapte.

**RU:** Вопрос вне данных — бот отказывается и предлагает факты.

Пример RO:

```
Nu fac prognoze. Pot arăta octombrie anul trecut și trendul ultimelor 6 luni — doriți?
```

Пример RU:

```
Прогнозов я не делаю. Могу показать октябрь прошлого года и тренд последних 6 месяцев — сказать?
```
