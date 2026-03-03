# HB Booking (финал) — подтверждение в WebApp

## Функции
- Гость: создаёт заявку, видит "Мои заявки" и статус
- Админ: входит по паролю, видит все заявки, подтверждает/отклоняет
- Конфликты времени запрещены (pending + approved)
- Прошедшие даты/время запрещены
- Excel экспорт: /excel

## Render
Build: pip install -r requirements.txt
Start: python app.py
Env: ADMIN_PASSWORD=1234
