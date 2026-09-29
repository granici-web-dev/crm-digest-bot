# Деплой на VPS

Один сервер Hetzner CPX12 с Ubuntu 24.04, один compose-проект `docker-compose.prod.yml`: `app` (бот и планировщик), `postgres` (данные в volume `postgres-data`), `backup` (ежедневный `pg_dump` в `./backups`, удаляются дампы старше 30 полных суток). Порты наружу не публикуются, reverse proxy и домена нет. Миграции применяет `app` при каждом старте.

Разделы выполняются по порядку. Команды выполняются от root на сервере, если не сказано «на ноутбуке».

## 0. Что нужно заранее

**Доступы:**

- Проект в Hetzner Cloud Console, где можно создать сервер.
- Права администратора в GitHub-репозитории `granici-web-dev/crm-digest-bot`: нужны для deploy key.
- SSH-ключ на ноутбуке. Проверить: `ls ~/.ssh/id_ed25519.pub`. Если файла нет: `ssh-keygen -t ed25519` (на ноутбуке), на все вопросы Enter.
- Два Telegram-бота, тестовая группа и служебный чат (ниже, как их получить).

**Значения `.env`.** Если проект уже запускался на ноутбуке, почти всё есть в локальном `.env`: переносить значения руками, файл целиком не копировать (в нём `DATABASE_URL` на localhost).

| Переменная | Откуда |
|---|---|
| `MEFI_BASE_URL` | Оставить как в `.env.example`. |
| `MEFI_API_KEY` | API-ключ mefi со скоупом `leads:read`, выдаёт администратор mefi Sofabelle. Тот же, что в локальном `.env`. |
| `MEFI_CLIENTS_API_KEY` | API-ключ mefi со скоупом `clients:read`, тоже от администратора mefi. Без него снапшот клиентов падает, в d1 «Contract Cantitate: —». |
| `CONTACT_HASH_KEY` | Сгенерировать на сервере: `openssl rand -hex 32`. Секрет ключей телефона и e-mail лида; короче 32 символов app не стартует. Смена значения ничего не ломает. В чат и логи не вставлять. |
| `TELEGRAM_BOT_TOKEN` | Основной бот. В Telegram: @BotFather → `/newbot` → имя → токен вида `123456:ABC...`. Затем там же `/setprivacy` → выбрать бота → `Disable`: без этого бот в группе не видит обычные сообщения (getUpdates ниже и вопросы в чате). Если бот уже был в группе до `/setprivacy`, удалить его из группы и добавить снова, иначе настройка не подействует. |
| `TELEGRAM_OPS_BOT_TOKEN` | Второй, служебный бот, тоже через @BotFather `/newbot`. Это обязательно другой бот, не основной. |
| `TELEGRAM_TEST_CHAT_ID` | Создать тестовую группу, добавить в неё основного бота, написать в группе любое сообщение. На ноутбуке: `curl -s "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates"`, в ответе найти `"chat":{"id":-100...` с названием тестовой группы. Число с минусом и есть chat_id. |
| `TELEGRAM_OPS_CHAT_ID` | Написать служебному боту `/start` в личку (или добавить его в отдельный служебный чат и написать там). На ноутбуке `getUpdates` как выше, но с `TELEGRAM_OPS_BOT_TOKEN`: взять `chat.id`. |
| `TELEGRAM_GROUP_CHAT_ID` | На неделю `DRY_RUN=1` вписать `-1`: это заглушка, в неё ничего не уходит. Настоящий chat_id управленческой группы вписывается только в разделе 9. Приложение требует, чтобы три chat_id различались, иначе не стартует. |
| `TELEGRAM_ADMIN_IDS` | Обязательно, хотя бы один id: с пустым значением `app` не стартует. Telegram user_id администраторов `/settings` через запятую: в ответе `getUpdates` это `"from":{"id":...}` у их сообщений. Список читается один раз при старте: после изменения `TELEGRAM_ADMIN_IDS` перезапустить app (`docker compose up -d app`), иначе удалённый id сохраняет доступ к меню. |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys. Нужен для режима вопросов в группе. Пусто: бот на вопросы молчит, переключатель Chat в `/settings` серый «fără cheie API». Ключ только в `.env`, в чат и логи не вставлять. |
| `ANTHROPIC_MODEL` | Оставить пустым: модель по умолчанию `claude-sonnet-5`. |
| `POSTGRES_USER`, `POSTGRES_DB` | Оставить `digest`. Все команды этой инструкции написаны для этих значений. |
| `POSTGRES_PASSWORD` | Сгенерировать на сервере: `openssl rand -hex 24`. Только hex: пароль вставляется в URL подключения без экранирования. |
| `DATABASE_URL` | Оставить пустым: в проде его собирает `docker-compose.prod.yml` из `POSTGRES_*` с хостом `postgres`. |
| `COMPOSE_FILE` | Раскомментировать строку `COMPOSE_FILE=docker-compose.prod.yml`. Тогда в папке проекта `docker compose ...` без `-f` работает с продовым стеком; все команды ниже рассчитаны на это. |
| `TENANT_ID` | Оставить `sofabelle`. |
| `DRY_RUN` | `1`. |
| `LOG_LEVEL`, `MEFI_SERVICE_*` | Оставить пустыми. |

`getUpdates` показывает только сообщения за последние сутки и пуст, если бот уже где-то запущен. Если ответ `"result":[]`, написать в чат ещё одно сообщение и повторить. Токены никуда, кроме `.env`, не вставлять.

## 1. Сервер

**Заказ в Hetzner Cloud Console** (Servers → Add Server):

- Location: Nuremberg.
- Image: Ubuntu 24.04.
- Type: CPX12.
- Networking: публичный IPv4 и IPv6 по умолчанию.
- SSH keys: Add SSH key, вставить содержимое `~/.ssh/id_ed25519.pub` с ноутбука. С ключом Hetzner не создаёт пароль root.
- Backups: включить. Это ежедневные образы всего диска, 7 штук: они покрывают потерю диска, на котором лежат и база, и `./backups`.
- Firewalls: Create Firewall, входящие правила только TCP 22, исходящие не трогать (нужны Telegram, mefi, Anthropic, GitHub). Файрвол именно облачный, не ufw: Docker пишет свои правила iptables в обход ufw.
- Name: `crm-digest-bot`. Create & Buy.

**Первый вход** (на ноутбуке): `ssh root@<IPv4 сервера>`. На вопрос `Are you sure you want to continue connecting` ответить `yes`.

**Вход только по ключу:**

```
sshd -T | grep passwordauthentication
```

Должно быть `passwordauthentication no`. Если `yes`:

```
echo "PasswordAuthentication no" > /etc/ssh/sshd_config.d/10-no-password.conf
systemctl restart ssh
sshd -T | grep passwordauthentication
```

Файлы в `sshd_config.d` читаются по алфавиту и первое значение побеждает, поэтому `10-` перекрывает `50-cloud-init.conf`. Не закрывать текущую сессию, пока в новом окне не проверен вход `ssh root@<IPv4>`.

**Обновления:**

```
apt update && apt full-upgrade -y
apt install -y git
cat > /etc/apt/apt.conf.d/52unattended-upgrades-local <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "03:00";
EOF
systemctl enable --now unattended-upgrades
```

Время сервера UTC: 03:00 UTC это 05:00–06:00 по Бухаресту, далеко от снапшота 19:00 и отчёта 19:30. Пакеты Docker из репозитория Docker unattended-upgrades не трогает, их обновляем вручную.

**Docker из официального репозитория:**

```
apt install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
docker compose version
systemctl is-enabled docker
```

Последняя команда должна ответить `enabled`: тогда после перезагрузки Docker стартует сам и поднимает контейнеры (`restart: unless-stopped`).

## 2. Доступ к репозиторию

Deploy key только на чтение, свой для этого сервера:

```
ssh-keygen -t ed25519 -N "" -C "crm-digest-bot deploy" -f ~/.ssh/crm-digest-bot-deploy
cat >> ~/.ssh/config <<'EOF'
Host github.com
    IdentityFile ~/.ssh/crm-digest-bot-deploy
    IdentitiesOnly yes
EOF
cat ~/.ssh/crm-digest-bot-deploy.pub
```

GitHub → репозиторий → Settings → Deploy keys → Add deploy key: вставить публичный ключ, «Allow write access» **не** отмечать.

При первом `git clone` ssh спросит про ключ github.com. Отпечаток ED25519 должен быть `SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU` (сверить на docs.github.com, страница «GitHub's SSH key fingerprints»); если совпадает, ответить `yes`.

## 3. Первый запуск

```
git clone git@github.com:granici-web-dev/crm-digest-bot.git /opt/crm-digest-bot
cd /opt/crm-digest-bot && cp .env.example .env && chmod 600 .env && nano .env
```

Заполнить `.env` по таблице раздела 0 (`nano`: сохранить Ctrl+O, Enter, выйти Ctrl+X). Проверить, что `COMPOSE_FILE` раскомментирован:

```
grep ^COMPOSE_FILE .env
```

Должно вывести `COMPOSE_FILE=docker-compose.prod.yml`. Запуск:

```
GIT_SHA=$(git rev-parse --short HEAD) docker compose up -d --build
```

Первая сборка занимает несколько минут. `GIT_SHA` попадает в образ и в стартовое сообщение; если его забыть, запуск не сломается, но в сообщении будет `версия unknown`.

## 4. Проверка

```
cd /opt/crm-digest-bot
docker compose ps
docker compose logs --tail 100 app
ls -l backups/
```

- `ps`: `app`, `postgres` (healthy), `backup` в состоянии running.
- **Стартовое сообщение «Бот запущен, версия <sha>, DRY_RUN=1.» должно прийти в служебный чат в течение двух минут после `up`. Если нет: `docker compose logs app`.** Частые причины: ошибка в `.env` (pydantic назовёт переменную), упавшая миграция (контейнер перезапускается по кругу и сообщение не шлёт), неверный токен служебного бота (строка `ops alert not delivered` в логе).
- В логе `app`: строки alembic, затем `scheduler started` со списком задач.
- `/settings` в личке с ботом отчётов от пользователя из `TELEGRAM_ADMIN_IDS` открывает меню Zilnic, Săptămânal, Lunar, Chat; любое переключение даёт строку «Настройки: ...» в служебном чате. Чужим и в группах бот не отвечает.
- **Режим вопросов.** Нужны `ANTHROPIC_API_KEY` в `.env` и выключенный privacy mode у основного бота (раздел 0, `/setprivacy` → `Disable`, затем удалить бота из группы и добавить снова). В `/settings` → Chat включить переключатель, в служебный чат приходит «Настройки: ... Chat выключен → включён». В тестовой группе написать `@<username бота> câte lead-uri am avut săptămâna aceasta?`: бот отвечает reply по-румынски, под ответом курсивом «Perioada: ... · funnel(...)». Если бот молчит: privacy mode (бот не видит упоминание), пустой ключ, Chat выключен. При сбое API Anthropic бот пишет «Nu pot răspunde acum...», в служебный чат приходит «Chat: API Anthropic недоступен ...». Конкретные даты: `@<username бота> câte lead-uri am avut <вчерашняя дата дд.мм>?` даёт подпись «Perioada: дд.мм.гггг · funnel()», а `... câte lead-uri în <прошлый месяц словом>?` даёт «Perioada: <месяц> <этот год>»; месяц позже текущего без года должен уйти в прошлый год. Уточнение: после вопроса про București в течение 10 минут написать `@<username бота> Dar Cluj?` или ответить reply на ответ бота «Dar Cluj?»: подпись «funnel(showroom=Cluj)» с тем же периодом. Больше 30 вопросов в день на группу бот не принимает. Все вопросы в таблице `chat_questions`.
- **На ноутбуке `run_app` не запускать, пока работает сервер, а если нужно, то только с `DRY_RUN=1`:** с тем же токеном два экземпляра делят long polling, и нажатия в `/settings` уходят не тому процессу; процесс без `DRY_RUN=1` при старте догоняет пропущенные отчёты в группу.
- **Догон при старте.** Поднятый после 19:10 процесс без сегодняшнего снапшота снимает его сам: в служебный чат «Снапшот за <дата> снят с опозданием в HH:MM», в ежедневном отчёте строка «Date extrase din mefi la HH:MM…» (она же после ручного `python -m digest snapshot`). Отчёт, время которого прошло, уходит с первой строкой «Raport trimis cu întârziere»: ежедневный только в тот же день и при снапшоте за сегодня, недельный и месячный в пределах `catch_up_days` из `config/modules.yaml`. Пропущенный снапшот прошлых дней: одно сообщение «Снапшот за <дата> пропущен…» при старте или в 09:00 (`time.missed_snapshot_check`).
- В `backups/` в течение минуты после старта появляется `digest-<дата UTC>.dump`.
- Вечером после ежедневного отчёта `app` проверяет, что самый свежий `digest-*.dump` моложе 26 часов, иначе шлёт алерт «Бэкап: ...» в служебный чат. Если упала сама проверка, приходит «Проверка бэкапа упала: ...». Логи бэкапа: `docker compose logs backup`.

**Перезагрузка.** Один раз проверить, что после перезагрузки сервер поднимается сам:

```
reboot
```

Через минуту зайти снова (`ssh root@<IPv4>`):

```
cd /opt/crm-digest-bot
docker compose ps
```

Все три контейнера снова running, в служебный чат пришло второе стартовое сообщение. Сразу после загрузки `app` может пару раз перезапуститься, пока Postgres не готов: это нормально, важно, что через две минуты он running и сообщение пришло.

Логи каждого контейнера ограничены пятью файлами по 10 МБ.

## 5. Перенос базы с ноутбука

Снапшоты за прошлые дни есть только в локальной базе: mefi хранит лишь текущее состояние (CLAUDE.md, инвариант 3), без переноса d1 и сравнения периодов начнутся с нуля. Перенос делается после раздела 4, лучше до 19:00 по Бухаресту: всё, что сервер успел записать сам, заменяется данными ноутбука. Переносить только пока сервер работает с `DRY_RUN=1`: `pg_restore --clean` стирает записи `report_runs` сервера, и сервер, уже отправлявший в группу, при старте отправил бы те же отчёты второй раз.

**0. На ноутбуке**, до подсчёта и дампа: остановить локальный `python -m digest`, если он где-то запущен (Ctrl+C в забытом терминале), и поставить в локальном `.env` `DRY_RUN=1`. Процесс, работающий между дампом и восстановлением, отправил бы отчёт, которого нет в дампе, а сервер при первом старте догнал бы его в группу второй раз. При старте процесс догоняет пропущенное: поздний снапшот после 19:10, недельный и месячный отчёт, если время отправки прошло.

**1. На ноутбуке**, в папке проекта, при запущенном локальном `docker compose up -d postgres`. Записать числа, они понадобятся для сверки:

```
docker compose exec postgres psql -U digest -d digest -c "select (select count(*) from snapshot_runs) as snapshot_runs, (select count(*) from lead_snapshots) as lead_snapshots"
```

**2. На ноутбуке**, дамп и копирование на сервер. Папку `backups/` на сервере создал первый запуск:

```
docker compose exec -T postgres pg_dump -Fc -U digest digest > local-before-deploy.dump
scp local-before-deploy.dump root@<IPv4 сервера>:/opt/crm-digest-bot/backups/
```

**3. На сервере**, остановить всё, что пишет в базу, дождаться готовности Postgres и восстановить:

```
cd /opt/crm-digest-bot
docker compose stop app backup
docker compose up -d --wait postgres
docker compose exec -T postgres \
    pg_restore --clean --if-exists --no-owner --single-transaction --exit-on-error \
    -U digest -d digest < backups/local-before-deploy.dump
```

Команда ничего не выводит при успехе. Если есть вывод с `error`, восстановление откатилось целиком (одна транзакция), база осталась прежней: разобраться с ошибкой и повторить шаг 3.

**4. На сервере**, сверка. Числа должны совпасть с шагом 1:

```
docker compose exec postgres psql -U digest -d digest -c "select (select count(*) from snapshot_runs) as snapshot_runs, (select count(*) from lead_snapshots) as lead_snapshots"
```

**5. На сервере**, запуск:

```
docker compose up -d
```

Приходит стартовое сообщение. Alembic при старте применит только миграции новее тех, что были в локальной базе.

**6. На ноутбуке больше ничего не запускать, а если запускать, то только с `DRY_RUN=1`:** ни `python -m digest snapshot`, ни `report`, ни `alembic`. С этого момента снапшоты делает только сервер; всё, что снимет ноутбук, в прод не попадёт, а ручной `report` с теми же токенами отправит отчёт второй раз. Локальный Postgres можно остановить: `docker compose stop postgres`. Файл `local-before-deploy.dump` на ноутбуке удалить.

На сервере `backups/local-before-deploy.dump` не подходит под `digest-*.dump`: ротация его не удалит, проверка свежести его не учитывает. Удалить вручную через неделю, когда ясно, что данные на месте.

## 6. Обновление

Все шаги в одной сессии ssh: шаг 1 запоминает переменную `OLD_SHA`.

**1. Запомнить текущую версию:**

```
cd /opt/crm-digest-bot
OLD_SHA=$(git rev-parse --short HEAD) && echo $OLD_SHA
```

Записать выведенный sha: он нужен для отката.

**2. Забрать код:**

```
git pull --ff-only
```

**2а. Сверить `.env`.** Новые переменные в обновлении:

```
git diff $OLD_SHA HEAD -- .env.example
```

Каждую добавленную строку перенести в `.env` на сервере до шага 5. Обязательные без значения по умолчанию: `TELEGRAM_ADMIN_IDS` (непустой список id через запятую), без неё новый `app` уходит в цикл рестартов. `ANTHROPIC_API_KEY` и `ANTHROPIC_MODEL` необязательны: без ключа режим вопросов не включается.

**3. Сверить миграции.** Ревизия базы (её выполняет ещё старый `app`):

```
docker compose exec app alembic current
```

Выводит ревизию, например `0001 (head)`. Записать её: к ней откатывается база в разделе 7. Новые миграции в обновлении:

```
git diff --name-only $OLD_SHA HEAD -- alembic/versions
```

Пустой вывод значит, что схема не меняется. Если есть файлы, в каждом строка `revision = "..."` это новая ревизия, которую применит `up`. (`alembic history` в работающем `app` показывает историю старого кода, поэтому новые миграции смотрим через git.)

**4. Дамп перед обновлением,** всегда, даже без новых миграций:

```
docker compose exec -T postgres pg_dump -Fc -U digest digest > backups/before-$OLD_SHA.dump
ls -l backups/before-$OLD_SHA.dump
```

Файл не подходит под `digest-*.dump`, ротация его не удаляет: удалить вручную через неделю, если откат не понадобился.

**5. Запуск новой версии:**

```
GIT_SHA=$(git rev-parse --short HEAD) docker compose up -d --build
```

Пересобирается и перезапускается `app`. В служебный чат приходит стартовое сообщение с новым sha. Если сообщения нет две минуты: `docker compose logs app` и раздел 7.

## 7. Откат

**Без новых миграций** (шаг 3 обновления дал пустой вывод):

```
cd /opt/crm-digest-bot
git checkout <OLD_SHA>
GIT_SHA=$(git rev-parse --short HEAD) docker compose up -d --build
```

**С новой миграцией.** Старый код не знает о новой ревизии, а alembic при старте ничего не откатывает. Поэтому сначала база, потом код. Два пути:

- Новый `app` работает: откатить схему к ревизии, записанной на шаге 3 обновления, затем код:

  ```
  docker compose exec app alembic downgrade 0001
  git checkout <OLD_SHA>
  GIT_SHA=$(git rev-parse --short HEAD) docker compose up -d --build
  ```

  Вместо `0001` подставить записанную ревизию. Если её не записали: `git show <OLD_SHA>:alembic/versions` перечисляет файлы миграций старой версии, ревизия с наибольшим номером в имени файла (строка `revision = "..."` внутри) и есть нужная.

- Новый `app` не стартует (миграция упала или код падает): восстановить дамп шага 4, затем код:

  ```
  docker compose stop app backup
  docker compose up -d --wait postgres
  docker compose exec -T postgres \
      pg_restore --clean --if-exists --no-owner --single-transaction --exit-on-error \
      -U digest -d digest < backups/before-<OLD_SHA>.dump
  git checkout <OLD_SHA>
  GIT_SHA=$(git rev-parse --short HEAD) docker compose up -d --build
  ```

  Данные, записанные между дампом и откатом, теряются.

После отката репозиторий в detached HEAD, это ожидаемо. Вернуться к свежему коду, когда ошибка исправлена: `git checkout main` и раздел 6 целиком.

## 8. Смена скрипта бэкапа

`backup` видит папку `deploy/` целиком, поэтому новый `deploy/backup.sh` после `git pull` лежит на месте, но работающий цикл выполняет уже загруженный старый. После обновления, где менялся `deploy/backup.sh`:

```
docker compose restart backup
```

Что бэкап восстанавливается, проверяет репетиция `deploy/restore-drill.sh` (ворота M9, `docs/acceptance/README.md`): дамп тем же `backup.sh`, восстановление во временный Postgres, сверка счётчиков, `alembic upgrade head`, рендер отчёта по копии без отправки. После смены `backup.sh` репетицию повторить.

## 9. Переход на прод через неделю

**Необратимое действие (CLAUDE.md: смена chat_id продовой группы).** Отчёты уйдут руководству Sofabelle, отправленное не отзывается. Выполнять только после письменного подтверждения владельца проекта, что неделя `DRY_RUN=1` прошла и отчёты сверены с ручными.

1. Узнать chat_id управленческой группы: основной бот добавлен в группу, написать в ней сообщение, на ноутбуке `getUpdates` с `TELEGRAM_BOT_TOKEN` (раздел 0). Сверить название группы в ответе.
2. На сервере `nano /opt/crm-digest-bot/.env`: `DRY_RUN=0`, `TELEGRAM_GROUP_CHAT_ID=<chat_id группы>`.
3. Применить:

   ```
   cd /opt/crm-digest-bot
   docker compose up -d --force-recreate app
   ```

4. В служебный чат приходит «Бот запущен, версия <sha>, DRY_RUN=0.». Сразу после этого рестарта в группу уходят с первой строкой «Raport trimis cu întârziere» отчёты, время которых прошло: недельный с понедельника 09:00 до среды, месячный с 1-го 09:00 до 5-го, ежедневный после 19:30 (`catch_up_days`). Удобнее переключать в четверг–воскресенье после 5-го числа, до 19:00 (в среду недельный ещё догоняется). Если пришло `DRY_RUN=1`, `.env` не сохранился: повторить шаги 2–3.
5. С `DRY_RUN=0` бот отвечает на вопросы и в управленческой группе, если Chat включён в `/settings`. Не готовы к вопросам руководства: выключить Chat до шага 3.

Вернуться в тестовый режим: `DRY_RUN=1` в `.env` и шаг 3.
