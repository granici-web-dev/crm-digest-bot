# Деплой на VPS

Один сервер Hetzner CX22 с Ubuntu 24.04, один compose-проект `docker-compose.prod.yml`: `app` (бот и планировщик), `postgres` (данные в volume `postgres-data`), `backup` (ежедневный `pg_dump` в `./backups`, хранение 30 дней). Порты наружу не публикуются, reverse proxy и домена нет. Миграции применяет `app` при каждом старте.

Команды ниже выполняются от root на сервере, если не сказано «на ноутбуке».

## 1. Сервер

**Hetzner Cloud Console:**

- Backups у сервера включены (обязательно; включены при заказе, проверить во вкладке Backups). Это ежедневные образы всего диска, 7 штук: они покрывают потерю диска, на котором лежат и база, и `./backups`.
- Firewall привязан к серверу, входящие правила: только TCP 22. Исходящие не ограничиваются (Telegram, mefi, Anthropic, GitHub). Файрвол именно облачный, не ufw: Docker пишет свои правила iptables в обход ufw.

**Обновления:**

```
apt update && apt full-upgrade -y
cat > /etc/apt/apt.conf.d/52unattended-upgrades-local <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "03:00";
EOF
systemctl enable --now unattended-upgrades
```

Время сервера UTC: 03:00 UTC это 05:00–06:00 по Бухаресту, далеко от снапшота 19:00 и отчёта 19:30. После перезагрузки контейнеры поднимаются сами (`restart: unless-stopped`), в служебный бот приходит стартовое сообщение. Пакеты Docker из репозитория Docker unattended-upgrades не трогает, их обновляем вручную.

Проверить, что вход по паролю выключен: `sshd -T | grep passwordauthentication` → `no`.

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
```

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

## 3. Первый запуск: три команды

```
git clone git@github.com:granici-web-dev/crm-digest-bot.git /opt/crm-digest-bot
cd /opt/crm-digest-bot && cp .env.example .env && chmod 600 .env && nano .env
GIT_SHA=$(git rev-parse --short HEAD) docker compose -f docker-compose.prod.yml up -d --build
```

Что заполнить в `.env`:

- `DRY_RUN=1`: неделю отчёты уходят только в `TELEGRAM_TEST_CHAT_ID`.
- `POSTGRES_PASSWORD`: `openssl rand -hex 24`. Только hex: пароль вставляется в URL подключения без экранирования.
- `DATABASE_URL` оставить пустым: в проде его собирает `docker-compose.prod.yml` из `POSTGRES_*` с хостом `postgres`.
- Остальное по комментариям в `.env.example`. Три chat_id (группа, тестовая группа, служебный чат) обязаны различаться, иначе `app` не стартует.

Если нужно перенести снапшоты с ноутбука, сделать раздел 5 до третьей команды.

`GIT_SHA` попадает в образ и в стартовое сообщение. Забытый `GIT_SHA=...` не ломает запуск, но в сообщении будет `версия unknown`.

## 4. Проверка

```
cd /opt/crm-digest-bot
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail 100 app
ls -l backups/
```

- `ps`: `app`, `postgres` (healthy), `backup` в состоянии running.
- **Стартовое сообщение «Бот запущен, версия <sha>, DRY_RUN=1.» должно прийти в служебный бот в течение двух минут после `up`. Если нет: `docker compose -f docker-compose.prod.yml logs app`.** Частые причины: ошибка в `.env` (pydantic назовёт переменную), упавшая миграция (контейнер перезапускается по кругу и сообщение не шлёт), неверный токен служебного бота (строка `ops alert not delivered` в логе).
- В логе `app`: строки alembic, затем `scheduler started` со списком задач.
- В `backups/` в течение минуты после старта появляется `digest-<дата UTC>.dump`.
- Вечером после ежедневного отчёта `app` проверяет, что самый свежий `digest-*.dump` моложе 26 часов, иначе шлёт алерт «Бэкап: ...» в служебный бот. Логи бэкапа: `docker compose -f docker-compose.prod.yml logs backup`.

Логи каждого контейнера ограничены пятью файлами по 10 МБ.

## 5. Перенос базы с ноутбука

Снапшоты за прошлые дни есть только в локальной базе: mefi хранит лишь текущее состояние (CLAUDE.md, инвариант 3), без переноса d1 и сравнения периодов начнутся с нуля.

На ноутбуке, в папке проекта, при запущенном локальном `docker compose up -d postgres`:

```
docker compose exec -T postgres pg_dump -Fc -U digest digest > local-before-deploy.dump
scp local-before-deploy.dump root@<ip сервера>:/opt/crm-digest-bot/backups/
rm local-before-deploy.dump
```

На сервере:

```
cd /opt/crm-digest-bot
docker compose -f docker-compose.prod.yml stop app
docker compose -f docker-compose.prod.yml up -d postgres
docker compose -f docker-compose.prod.yml exec -T postgres \
    pg_restore --clean --if-exists --no-owner -U digest -d digest < backups/local-before-deploy.dump
docker compose -f docker-compose.prod.yml exec postgres \
    psql -U digest -d digest -c "select snapshot_date, status from snapshot_runs order by snapshot_date desc limit 5"
GIT_SHA=$(git rev-parse --short HEAD) docker compose -f docker-compose.prod.yml up -d --build
```

`stop app` нужен, если `app` уже запускался; при первом запуске он ничего не делает. Alembic при старте `app` применит только миграции новее тех, что были в локальной базе. Файл `local-before-deploy.dump` не подходит под `digest-*.dump`: ротация его не удалит, проверка свежести его не учитывает. Удалить вручную, когда убедились, что данные на месте.

## 6. Обновление

```
cd /opt/crm-digest-bot
git pull --ff-only
GIT_SHA=$(git rev-parse --short HEAD) docker compose -f docker-compose.prod.yml up -d --build
```

Пересобирается и перезапускается только `app`; в служебный бот приходит стартовое сообщение с новым sha.

Если в обновлении есть новая миграция (`git diff --stat HEAD@{1} -- alembic/versions`), перед `up` снять дамп вручную, иначе откат вернёт базу только к последнему ежедневному дампу:

```
docker compose -f docker-compose.prod.yml exec -T postgres pg_dump -Fc -U digest digest > backups/before-$(git rev-parse --short HEAD).dump
```

## 7. Откат

```
cd /opt/crm-digest-bot
git log --oneline -10
git checkout <sha>
GIT_SHA=$(git rev-parse --short HEAD) docker compose -f docker-compose.prod.yml up -d --build
```

Репозиторий остаётся в detached HEAD, это ожидаемо. Вернуться к свежему коду: `git checkout main && git pull --ff-only` и команда `up` выше.

**Откат через миграцию.** Старый код не знает о новой миграции, а alembic при старте ничего не откатывает. Два пути:

- До `git checkout`, пока работает новый код: `docker compose -f docker-compose.prod.yml exec app alembic downgrade <ревизия целевого коммита>`, затем checkout и `up`.
- Восстановить дамп, снятый перед обновлением: `stop app`, `pg_restore --clean --if-exists --no-owner` как в разделе 5 с файлом `backups/before-<sha>.dump`, затем checkout и `up`.
