# Асинхронный сервис обработки платежей

Это моя реализация тестового задания на асинхронный процессинг платежей.

Сервис принимает платёж через HTTP API, сохраняет его вместе с событием Outbox, передаёт
событие в RabbitMQ, эмулирует работу платёжного шлюза и отправляет результат на webhook.

## Стек

- Python 3.12+
- FastAPI и Pydantic v2
- SQLAlchemy 2.0 в асинхронном режиме
- PostgreSQL
- RabbitMQ и FastStream
- Alembic
- Docker Compose

## Запуск

Сначала создаю локальный файл настроек:

```bash
cp .env.example .env
```

В `.env` нужно заменить значения:

```text
PAYMENTS_API_KEY=replace-with-a-long-random-api-key
PAYMENTS_WEBHOOK_SIGNING_SECRET=replace-with-a-long-random-webhook-secret
```

После этого запускаю сервис:

```bash
docker compose up --build -d
```

Проверяю состояние контейнеров:

```bash
docker compose ps -a
```

Доступны:

- API: <http://localhost:8000>
- RabbitMQ Management: <http://localhost:15672>
- RabbitMQ login/password: `payments` / `payments`

## API

Все endpoints требуют заголовок `X-API-Key`.

### Создание платежа

```bash
curl -i -X POST http://localhost:8000/api/v1/payments \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: replace-with-a-long-random-api-key' \
  -H 'Idempotency-Key: order-42-payment-1' \
  -d '{
    "amount": "100.50",
    "currency": "RUB",
    "description": "Order 42",
    "metadata": {"order_id": 42},
    "webhook_url": "https://example.org/payment-webhook"
  }'
```

Ответ:

```http
HTTP/1.1 202 Accepted
```

```json
{
  "payment_id": "f7eb8539-85cb-45b8-a580-4ca950b625a7",
  "status": "pending",
  "created_at": "2026-10-21T12:00:00Z"
}
```

Повтор с тем же `Idempotency-Key` и тем же телом возвращает тот же платёж. Если использовать
ключ с другими данными, сервис отвечает `409 Conflict`.

### Получение платежа

```bash
curl http://localhost:8000/api/v1/payments/PAYMENT_ID \
  -H 'X-API-Key: replace-with-a-long-random-api-key'
```

### Проверка процесса

```bash
curl http://localhost:8000/health/live \
  -H 'X-API-Key: replace-with-a-long-random-api-key'
```

## Как проходит платёж

```text
POST /api/v1/payments
        │
        ▼
PostgreSQL transaction
  ├── payments
  └── outbox
        │
        ▼
Outbox publisher
        │
        ▼
RabbitMQ: payments.new
        │
        ▼
FastStream consumer
  ├── эмуляция 2-5 секунд
  ├── succeeded или failed
  └── webhook
        │
        ├── успешно → ACK
        └── ошибка → retry 2 → retry 3 → DLQ
```

API не ждёт окончания обработки. Он валидирует запрос, сохраняет платёж и сразу возвращает
`202 Accepted`.

## Почему я использовал Outbox

Если сначала сохранить платёж, а потом отдельно отправить сообщение в RabbitMQ, процесс
может упасть между этими операциями. Платёж останется в базе, но consumer о нём не узнает.

Поэтому платёж и событие создаются в одной транзакции PostgreSQL. Отдельный publisher
выбирает неопубликованные события, отправляет их с publisher confirm и только после этого
записывает `published_at`.

Outbox использует `FOR UPDATE SKIP LOCKED` и короткий lease. Блокировка базы не удерживается
во время сетевого обращения к RabbitMQ.

Гарантия доставки здесь `at-least-once`. Если publisher успел отправить событие, но упал до
обновления Outbox, событие придёт повторно. Consumer учитывает сохранённые номера попыток и
не повторяет уже завершённую работу.

## Идемпотентность

`Idempotency-Key` имеет уникальное ограничение в PostgreSQL. Одного предварительного
`SELECT` недостаточно, потому что два параллельных запроса могут одновременно не увидеть
запись.

Также я сохраняю SHA-256 от провалидированного запроса. Поэтому одинаковый повтор возвращает
существующий платёж, а использование того же ключа с другим телом даёт `409 Conflict`.

## Эмуляция шлюза

Эмулятор ждёт от двух до пяти секунд и возвращает примерно 90% успешных и 10% неуспешных
результатов. Результат вычисляется из UUID платежа, поэтому повторная обработка одного и того
же сообщения не меняет итог операции.

## Retry и DLQ

Для каждого сообщения допускается три попытки. После ошибки consumer сохраняет следующий
retry в Outbox. Publisher отправляет его в очередь с TTL:

```text
payments.retry.2 --1 секунда--> payments.new
payments.retry.3 --2 секунды--> payments.new
```

После третьей ошибки consumer делает reject без requeue. Основная очередь связана с
`payments.dlx`, поэтому сообщение попадает в `payments.dlq`.

## Webhook

Webhook содержит ID платежа, сумму, валюту, статус и номер попытки. Для HTTP-запроса заданы
явные таймауты. Любой неуспешный ответ или сетевая ошибка участвует в общей схеме трёх
попыток.

Адрес проверяется перед сохранением и перед отправкой, чтобы запрос нельзя было направить на
локальные или приватные адреса. Тело подписывается HMAC-SHA256. Это небольшие защитные меры,
которые не меняют основной сценарий задания.

## Структура проекта

```text
app/api                 HTTP endpoints и зависимости
app/integrations        эмулятор шлюза и webhook client
app/messaging           RabbitMQ topology, consumer и publisher
app/services            создание платежа, Outbox и обработка сообщения
app/models.py           SQLAlchemy модели
app/schemas.py          Pydantic схемы
alembic                 миграции
compose.yaml            локальное окружение
```

Я не добавлял repository-слой: запросов немного, и отдельные классы не дали бы новой
границы. HTTP handlers отвечают только за HTTP и вызывают сервисы. Работа с RabbitMQ и
внешний HTTP вынесены отдельно.

## Проверки

```bash
.venv/bin/ruff format --check app tests alembic
.venv/bin/ruff check app tests alembic
.venv/bin/mypy app
.venv/bin/pytest -q
```

Тесты проверяют валидацию денег, распределение 90/10, идемпотентный hash, API key,
ограничение тела запроса, безопасность webhook и RabbitMQ topology. Реальный полный сценарий
можно проверить через Docker Compose.

## Остановка

Остановить контейнеры с сохранением данных:

```bash
docker compose stop
```

Удалить контейнеры, но сохранить volumes:

```bash
docker compose down
```

Удаление вместе с локальными данными PostgreSQL и RabbitMQ:

```bash
docker compose down -v
```
