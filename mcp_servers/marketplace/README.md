# Marketplace MCP

Закрытый Streamable HTTP `/mcp`. Инструменты: `search`, `product`, `quote`, `session_status`; `authorize` доступен при включённом noVNC. MCP не предоставляет корзину, заказы, оплату или произвольное управление браузером. JS/HTML обслуживают отдельный вход через noVNC; `chromium-policy.json` задаёт политики браузера. Выдача поиска предварительная: точный вариант, цену и доставку проверяйте отдельно.

Каждый инструмент требует `marketplace`: **`ozon`** (`ozon.ru`) или **`aliexpress`** (`aliexpress.ru`). Например, вызов `search` с `{"marketplace":"aliexpress","query":"куртка"}` ищет только на AliExpress. Не передавайте прежний массив `providers`: это изменение контракта. В `product` и `quote` используйте `ref` из того же маркетплейса; несовпадение даёт `marketplace_mismatch`. `authorize` открывает интерактивный вход только для Ozon; для AliExpress возвращает `interactive_authorization_unsupported`.

## Запуск

Для пилота с ручным входом через браузер, из корня репозитория:

```sh
# На хосте NGINX (DNS и доверенный TLS-сертификат для этого имени уже настроены):
sudo ./marketplace-novnc-proxy.sh login.example.org vadim /path/fullchain.pem /path/privkey.pem
# На Маке или Linux-хосте с доступом к витринам:
./marketplace-up.sh vadim@ssh.example.org login.example.org --update
```

`--update` явно заменяет `mmcp-pilot` новым образом `:pilot`, сохраняя том `mmcp-pilot-data` (ключи и браузерные профили); `MMCP_IMAGE` позволяет указать тег коммита или digest. На новой установке флаг не нужен. Повторный запуск без `--update` не пересоздаёт работающий контейнер, только перезапускает свой SSH-туннель. Если ещё работает старый ручной туннель на тот же сокет, сначала закрой его: скрипт его не убивает. Отдельный `./marketplace-tunnel.sh USER@SSH_HOST /home/USER/.mmcp-pilot/mcp-live.sock` перезапускает только туннель. На принимающей стороне нужны `python3`, `curl`, разрешённый SSH StreamLocal forward и вход обычной SSH-учёткой; на Маке Caddy не нужен. Для другого home передай `MMCP_REMOTE_SOCKET` клиенту и тот же путь последним аргументом скрипту прокси.

Публичный HTTPS-виртуальный хост обслуживает только viewer и WebSocket. Приватный `/mcp` для Hermes: `http://127.0.0.1:28080/mcp` **на хосте NGINX**; снаружи `/mcp` должен вернуть `404`, локально без ключа — `401`. `--render DIR` у скрипта прокси создаёт файлы для проверки без установки. Скрипт рассчитан на Linux с systemd и NGINX от `www-data`, не выпускает сертификаты и не меняет sshd/DNS.

Один раз на каждый отдельный профиль оператор выполняет `docker exec mmcp-pilot python /app/mcp/market_mcp.py issue LABEL --days 7`; результат переносится по SSH в секреты соответствующего профиля Hermes, не в чат. Затем `authorize` для `ozon` выдаёт **готовую одноразовую ссылку**: передавайте её целиком, с `#invite=…`, только в личном чате владельца сессии. Не вызывайте `authorize` повторно до открытия, если уже отправили ссылку: повторный вызов заменяет неиспользованное приглашение. Ручная выдача viewer key для этого потока не нужна. После входа снова вызовите `authorize`, затем проверьте живой `search`/`quote` тем же токеном.

### Запуск вручную

Из корня проекта на Linux-хосте с доступом к витринам:

```sh
docker build -f mcp_servers/marketplace/Dockerfile -t marketplace-mcp .
docker volume create marketplace-mcp-data
docker run -d --name marketplace-mcp --init --shm-size=512m \
  -p 127.0.0.1:18000:8000 -e MARKET_MCP_BIND=0.0.0.0 \
  -e MARKET_MCP_HOSTS=127.0.0.1:18000,127.0.0.1:28080 \
  --security-opt seccomp=/private/path/chromium-seccomp.json \
  --mount source=marketplace-mcp-data,target=/data marketplace-mcp
```

Seccomp-профиль и политика user namespaces должны позволять запуск Chromium с песочницей. Не используйте `--privileged` или `--no-sandbox`. На принимающем SSH настройте отдельную учётку с `GatewayPorts no` и `PermitListen 127.0.0.1:28080`, проверьте фактический bind. С удалённого хоста:

```sh
ssh -NT -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -R 127.0.0.1:28080:127.0.0.1:18000 mmcp-tunnel@hermes-host.example
```

В Hermes регистрируется `http://127.0.0.1:28080/mcp`. `401` без ключа подтверждает транспорт, но не работу каталога. Выпустите отдельный bearer на участника: `docker exec marketplace-mcp python /app/mcp/market_mcp.py issue LABEL --days 7` (только приватный терминал). Передавайте его через секреты профиля. Не используйте один ключ для всех: он выбирает браузерный слот. Слоты разделяют состояние приложения, но не являются границей изоляции от компрометации воркера.

Для ручного мобильного входа задайте HTTPS `MARKET_MCP_VIEWER_ORIGIN`, добавьте его Host в `MARKET_MCP_HOSTS`, а на принимающей стороне направьте HTTPS и WebSocket viewer через туннель. Не публикуйте VNC/CDP и не логируйте ссылки, cookies или заголовки авторизации.

Проверки из корня: `hermes mcp test marketplace` и `PYTHONPATH=mcp_servers/marketplace python -m unittest discover -s tests/marketplace/mcp`. Офлайн-тесты не проверяют реальный вход и доступность витрины.
