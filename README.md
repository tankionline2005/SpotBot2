# SpotBot2 — Учёт заказов курьера

Веб-приложение для курьеров в маленьких службах доставки без своего приложения: ведёт учёт заказов, автоматически распознаёт чеки через ИИ и отправляет отчёты в Telegram.

<img width="3240" height="2060" alt="Image" src="https://github.com/user-attachments/assets/3116d0ed-94b6-4359-8867-52373da38269" />

---

## Возможности

- **Журнал заказов** — добавление, редактирование и удаление заказов с адресом, суммой и временем
- **Распознавание чеков** — загрузите фото чека, Google Gemini автоматически извлечёт сумму и время
- **Статистика** — топ адресов, почасовая аналитика, вероятность заказов по часам
- **Telegram-отчёты** — отправка дневной и недельной сводки прямо в нужный топик чата
- **Фото к заказу** — прикрепляйте несколько фото чеков, они автоматически отправляются в Telegram
- **Экспорт в Excel** — выгрузка всех заказов одним кликом в `.xlsx`
- **Авторизация по паролю** — сессия хранится 90 дней, повторный вход не требуется

---

## Структура проекта

```
spotbot/
├── app.py              # Основной файл приложения
├── spotbot.db          # База данных SQLite (создаётся автоматически)
├── .env                # Переменные окружения (не коммитить!)
├── uploads/            # Загруженные фото чеков (создаётся автоматически)
├── templates/
│   └── index.html      # Шаблон интерфейса
└── static/             # CSS, JS, изображения
```

---

## Telegram-интеграция

Приложение использует **топики** (threads) в группе Telegram:

- **Топик 4** (`TG_THREAD_ORDERS`) — фото чеков с подписью при создании заказа
- **Топик 8** (`TG_THREAD_STATS`) — дневные и недельные отчёты со статистикой

Чтобы изменить номера топиков, отредактируйте константы в начале `app.py`:

```python
TG_THREAD_ORDERS = 4
TG_THREAD_STATS  = 8
```

---

## Деплой на VPS сервере

### 1. Установите зависимости

```bash
apt update
apt install python3 python3-pip python3-venv libjpeg-dev zlib1g-dev libwebp-dev -y
```

### 2. Настройте окружение

```bash
cd /root/SpotBot2
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install python-dotenv gunicorn
```

### 3. Создайте файл `.env`

```bash
nano .env
```

Заполните своими данными:

```env
SPOTBOT_PASSWORD=ваш_пароль
SECRET_KEY=длинная-случайная-строка
TG_BOT_TOKEN=123456789:AABBCCDDEEFFaabbccddeeff
TG_CHAT_ID=-1001234567890
GEMINI_API_KEY=your_gemini_api_key_here
```

Сохраните: `Ctrl+O` → `Enter` → `Ctrl+X`

> **Где взять ключи?**
> - `TG_BOT_TOKEN` — создайте бота через [@BotFather](https://t.me/BotFather) в Telegram
> - `TG_CHAT_ID` — ID группового чата (можно узнать через [@userinfobot](https://t.me/userinfobot))
> - `GEMINI_API_KEY` — получите бесплатно на [Google AI Studio](https://aistudio.google.com/)

### 4. Создайте systemd-сервис

```bash
nano /etc/systemd/system/spotbot2.service
```

Вставьте содержимое:

```ini
[Unit]
Description=SpotBot2
After=network.target

[Service]
User=root
WorkingDirectory=/root/SpotBot2
EnvironmentFile=/root/SpotBot2/.env
ExecStart=/root/SpotBot2/venv/bin/gunicorn -w 2 -b 0.0.0.0:5000 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Сохраните: `Ctrl+O` → `Enter` → `Ctrl+X`

```bash
systemctl daemon-reload
systemctl enable spotbot2
systemctl start spotbot2
```

### 5. Откройте порт в фаерволе

```bash
apt install ufw -y
ufw allow 22
ufw allow 5000
ufw enable
```

> ⚠️ `allow 22` — обязательно, чтобы не потерять SSH доступ

### 6. Проверьте, что приложение работает

```bash
systemctl status spotbot2
```

Должно быть `active (running)`. Откройте в браузере `http://ваш_ip:5000`.

---

## Полезные команды

```bash
systemctl restart spotbot2               # перезапуск после обновления файлов
systemctl stop spotbot2                  # остановить
journalctl -u spotbot2 -f                # логи в реальном времени
tar -czf SpotBot2.tar.gz /root/SpotBot2  # создать архив на сервере
tar -xf /root/SpotBot2.tar -C /root/     # распаковать архив на сервере
```

---

## Лицензия

MIT — используйте свободно.
