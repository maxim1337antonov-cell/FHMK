with open("auto_withdraw.py", "r", encoding="utf-8") as f:
    text = f.read()

# Это самый надежный способ. Ошибка в test_withdraw.py возникает потому, что
# он импортирует auto_withdraw, а в auto_withdraw есть дублирование кода.

first_import = text.find('import os')
second_import = text.find('\nimport os', first_import + 10)

if second_import != -1:
    triple_quote = text.rfind('"""\n', 0, second_import + 5)
    if triple_quote != -1 and triple_quote > len(text) / 2:
        text = text[:triple_quote]

text = text.replace('"""Отправить команду /withdraw в бота @BmbP2p_bot и автоматически подтвердить\n    \n    Args:',
                    '"""Отправить команду /withdraw в бота @BmbP2p_bot и автоматически подтвердить"""\n    # Args:')
text = text.replace('"""Получить Pyrogram клиент для автовывода    global app', '"""Получить Pyrogram клиент для автовывода"""\n    global app')
text = text.replace('"""Инициализация Pyrogram клиента для автовывода    global app', '"""Инициализация Pyrogram клиента для автовывода"""\n    global app')
text = text.replace('"""Остановка Pyrogram клиента для автовывода    global app', '"""Остановка Pyrogram клиента для автовывода"""\n    global app')
text = text.replace('#\nМодуль для автоматического вывода BTC через бота @BmbP2p_bot используя Pyrogram\n', '# Модуль для автоматического вывода BTC через бота @BmbP2p_bot используя Pyrogram\n')
text = text.replace('"""\nМодуль для автоматического вывода BTC через бота @BmbP2p_bot используя Pyrogram\n"""\n', '# Модуль для автоматического вывода BTC через бота @BmbP2p_bot используя Pyrogram\n')

with open("auto_withdraw.py", "w", encoding="utf-8") as f:
    f.write(text)
