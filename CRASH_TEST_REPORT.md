# Crash test report

Пройдено: **16/16**, провалено: **0**

## Результаты
### Импорты модулей
PASS
- Основные модули импортируются

### Парсинг лимитов
PASS
- Диапазон 0.001-0.10: OK
- Европейская запятая: OK
- Некорректные вводы отвергнуты
- parse_limits(None): исключение поймано
- Огромные числа: InvalidOperation
- Научная запись 1e-8 → regex: ['1', '8']

### Валидация BTC-адресов
PASS
- bech32 / мусор / None / legacy / testnet: OK

### Расчёт комиссий
PASS
- Нормальная сумма 0.01: OK
- 0 и отрицательные отвергнуты

### Утилиты форматирования
PASS
- format_datetime мусор: не падает
- format_btc_amount мусор: не падает
- get_role_name({}) → KeyError (хрупкий контракт)
- FINDING: format_admin_stats(None volume) не упал
- FINDING: list_keyboard с битым expires_at не упал

### Свежий init_db vs upsert_user
PASS
- upsert_user на свежей БД сработал

### Операции с БД (изолированная)
PASS
- Несуществующие user/deal → None
- SQL-инъекция в username не сломала схему
- update несуществующей сделки: без исключения
- FINDING: upsert_user принимает отрицательный tg_id
- FINDING: create_deal принял невалидный escrow (deal #258)

### Переходы статусов сделок
PASS
- Сделка #258 после скачков: Active
- FINDING: Нет защиты переходов: CANCELLED → ACTIVE прошло без ошибки

### Конкурентная запись в БД
PASS
- Создано 25/25, ошибок: 0

### Целостность комиссий
PASS
- 0.001: net=0.00100000
- 0.01: net=0.01000000
- 0.1: net=0.10000000
- 1.0: net=1.00000000
- 1E-8: net=1E-8

### Парсинг callback_data
PASS
- int() на битых payload: 9 обработаны как ValueError/IndexError
- FINDING: Многие хендлеры делают int(callback.data.split(...)[n]) без try; ошибка ловится только глобальным error_handler

### FSM: message.text is None
PASS
- sell_limits / buy_create_limits_or_amount делают message.text.strip() без проверки None
- FINDING: Стикер/фото в WAIT_LIMITS уронит хендлер (AttributeError); ошибка должна быть поймана error_handler

### Дубли хендлеров
PASS
- FINDING: handlers/admin_handlers.py: '@router.message(Command("chat"))' встречается 2 раз
- FINDING: handlers/deal_handlers.py: '@router.callback_query(F.data.startswith("deal:actions:"))' встречается 2 раз
- FINDING: handlers/main_handlers.py: '@router.callback_query(F.data == "menu:help")' встречается 2 раз

### Чтение прод-БД (копия)
PASS
- Таблицы: 9; users=195, deals=235

### Telegram Bot API (getMe)
PASS
- Бот жив: @p2pBmBv2_bot id=8449504382

### Шторм апдейтов в хендлеры
PASS
- Апдейтов без исключения хендлера: 45

## Находки
- Утилиты форматирования: format_admin_stats(None volume) не упал
- Утилиты форматирования: list_keyboard с битым expires_at не упал
- Операции с БД (изолированная): upsert_user принимает отрицательный tg_id
- Операции с БД (изолированная): create_deal принял невалидный escrow (deal #258)
- Переходы статусов сделок: Нет защиты переходов: CANCELLED → ACTIVE прошло без ошибки
- Парсинг callback_data: Многие хендлеры делают int(callback.data.split(...)[n]) без try; ошибка ловится только глобальным error_handler
- FSM: message.text is None: Стикер/фото в WAIT_LIMITS уронит хендлер (AttributeError); ошибка должна быть поймана error_handler
- Дубли хендлеров: handlers/admin_handlers.py: '@router.message(Command("chat"))' встречается 2 раз
- Дубли хендлеров: handlers/deal_handlers.py: '@router.callback_query(F.data.startswith("deal:actions:"))' встречается 2 раз
- Дубли хендлеров: handlers/main_handlers.py: '@router.callback_query(F.data == "menu:help")' встречается 2 раз
