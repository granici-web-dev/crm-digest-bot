# ADR-005 · Висящая оферта только у лида в работе

Статус: **accepted** (25.09.2026). Shape: `docs/shapes/2026-09-25-daily-d2-d6.md`, «Дополнение после critique», п. 1. Формула: `docs/kpi-definitions.md`, «Базовые множества».

## Контекст

`ACTIVE_OFFERS_14` повторяла `01_Input_Leads!AC` из SB KPi.xlsx: оферта, не `Clienți`, не IRELEVANT, последний контакт больше 14 дней назад. Лиды в LOST (BUGET, A REFUZAT, CONCURENTA, NU A RASPUNS, Stand BY и другие) с `Ofertat = ✅DA` в истории попадали в счёт. На живом снапшоте 25.09.2026 d4 показал 564 «висящие» оферты, почти все у потерянных лидов. Бриф и меню отчётов говорят «oferte active»: оферта, по которой продавец ещё должен вернуться к клиенту.

## Решение

`ACTIVE_OFFERS_14` = `is_ofertat` ∧ категория `ACTIVE` или `ACTIVE_FOLLOWUP` ∧ `analysis_date − date(last_contact_at) > active_offer_stale_days`. Категория по `status.name` через `config/status-mapping.yaml`. UNMAPPED не входит: у неизвестного статуса нет признака «в работе», он и так идёт алертом.

## Последствия

- Меняются ACR (`ACTIVE_OFFERS_14 / LEADS`) в m5 и счёт d4 «висящие оферты»: обе цифры считает одна функция `count_flags`.
- Эталон пересобран `scripts/build_etalon.py`: изменились только `active_offers_14` и `acr`. Ручная проверка Moaca Andreea: 6 оферт, висевших по старой формуле, в статусах A REFUZAT (3) и BUGET (3), по новой 0.
- Расхождение с workbook добавлено в таблицу `docs/kpi-definitions.md`.
