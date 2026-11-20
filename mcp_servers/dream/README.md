# Dream MCP

Sandbox-only RPC через `stdio`: SQLite хранит цели, привязку к счёту и журнал операций. Официальный Sandbox REST остаётся банковским транспортом. Описание сценария, стратегия и сводка живут в скиллах, не в сервере.

Из корня проекта установите `python -m pip install -r mcp_servers/dream/requirements.txt`. Запуск: `python -m mcp_servers.dream.server`. В Hermes задайте абсолютный путь к Python и доступ к корню checkout через рабочий каталог или `PYTHONPATH`. Переменные:

- `DREAM_DB` — абсолютный путь к SQLite; `DREAM_OWNER` — доверенный владелец профиля, не аргумент модели.
- `DREAM_TOKEN_FILE` — абсолютный путь в приватном каталоге `0700`. При отсутствии токена или `401` подключение запрашивает **только Sandbox-токен** через MCP `form`, проверяет его и записывает `0600`. Сначала проверьте поверхность фиктивным маркером: MCP host видит форму. Боевой токен не вводить.
- `DREAM_CA_FILE` — CA bundle для TLS при необходимости. `DREAM_READ_ONLY=1` — только чтение, без ввода токена и мутаций.

Контракт: `connection_status`, `begin_connection`, `create_goal`, `list_goals`, `goal_status`, `fund_goal`, `last_price`, `place_limit_order`, `order_status`, `close_goal`. Суммы — целые копейки. Неизвестные суммы — `null`; состояние счёта и стоимость портфеля не равны доступному остатку. Мутации требуют подтверждения, открытие и взнос сверяются с банком, неоднозначный исход блокирует повтор. В рабочем и read-only экземплярах одного профиля используйте один DB/owner/token file; не давайте общий экземпляр нескольким участникам.

Проверка: `hermes mcp test dream` и `python -m unittest discover -s mcp_servers/dream -p 'test_*.py'`. Это не подтверждает живую работу Telegram и банка. Для cron прикрепите `dream-daily-analysis`, задайте `deliver: origin` и `attach_to_session: true` из проверенного приватного диалога. В `enabled_toolsets` укажите набор отдельного read-only экземпляра (например, `mcp-dream_readonly`, если сервер зарегистрирован как `dream_readonly`), не основной торговый экземпляр. В этом режиме отчёт формирует модель; не используйте `no_agent`. Старые программы, вызывающие `daily_proposals` или `offer_savings_goal`, обновите до перехода на эту версию.
