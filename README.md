# HB Booking — финальная версия (подтверждение в WebApp)

## Что работает
- WebApp (ваш UI) открывается из Telegram
- Гость создаёт заявку → статус сразу виден в "Мои заявки"
- Админ подтверждает/отклоняет в WebApp по паролю
- Конфликты времени запрещены на сервере
- Прошедшие даты/время запрещены
- Excel экспорт: /excel

## Запуск локально
1) Распакуйте архив
2) В Anaconda Prompt:
   cd /d "папка_проекта"
   pip install -r requirements.txt
   set ADMIN_PASSWORD=1234
   python app.py
3) Открыть: http://127.0.0.1:8000

## Деплой на Render (чтобы работало на телефоне)
1) Создайте новый репозиторий GitHub и загрузите содержимое архива (не zip):
   - app.py
   - requirements.txt
   - README.md
   - templates/index.html

2) Render → New → Web Service (НЕ Docker)
   - Build: pip install -r requirements.txt
   - Start: python app.py
   - Env:
     ADMIN_PASSWORD=1234

3) После деплоя откройте ссылку Render.
4) В BotFather:
   /setmenubutton → URL = ссылка Render

## Пароль админа
Меняется через переменную окружения ADMIN_PASSWORD в Render.
