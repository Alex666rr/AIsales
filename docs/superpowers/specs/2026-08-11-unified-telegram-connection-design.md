# Единый процесс подключения Telegram-аккаунта

## Цель

Дать владельцу платформы три равнозначных способа подключить **свой** Telegram-аккаунт: по номеру телефона с кодом и 2FA, через QR-код или локальным импортом Telegram Desktop `tdata`. Каждый успешный путь должен создавать одну зашифрованную серверную сессию в PostgreSQL, после чего Railway работает без включённого компьютера владельца.

## Границы первого подэтапа

В подэтап входят backend API, локальный CLI для `tdata`, зашифрованное сохранение и запуск существующего connection supervisor. Экран кабинета, массовый импорт, загрузка папок через браузер и импорт raw `tdata` на Railway не входят в него.

## Пользовательские пути

### Номер телефона

1. Владелец начинает попытку, передаёт номер в API.
2. API создаёт короткоживущую попытку и просит Telegram выслать код.
3. Владелец передаёт код; если Telegram требует 2FA, API сообщает только `password_required`.
4. Владелец передаёт 2FA-пароль в ту же короткоживущую попытку.
5. После `get_me` подтверждённая сессия шифруется и сохраняется; попытка и пароль уничтожаются.

Код, пароль 2FA и номер не попадают в логи, аудит, исключения или ответ API. Все попытки имеют TTL, лимит ошибок и владельца организации.

### QR-код

1. API создаёт короткоживущую попытку и возвращает одноразовый QR payload с временем истечения.
2. Клиент показывает QR; отдельный polling endpoint возвращает только `pending`, `expired`, `password_required`, `authorized` или безопасную ошибку.
3. После подтверждения в Telegram API выполняет `get_me`, сохраняет зашифрованную сессию и уничтожает QR material.

QR не является сессией, не сохраняется после окончания попытки и не может быть запрошен другим владельцем.

### Telegram Desktop `tdata`

1. Владелец закрывает Telegram Desktop и запускает локальный CLI на компьютере с конкретной папкой `tdata`.
2. CLI делает защищённую временную копию, запрещает UNC, symlink/reparse points, неregular files и изменённый источник; разбирает только копию.
3. API выдаёт одноразовый import ticket с короткоживущей временной публичной частью ключа. CLI подтверждает сессию у Telegram через `get_me`, шифрует session material этой публичной частью и передаёт только ciphertext в authenticated API.
4. Временная копия, ключи Desktop и passcode уничтожаются; исходная папка не покидает компьютер владельца.

Railway никогда не принимает raw `tdata`, zip-архив или Desktop passcode. Повторный импорт одного аккаунта выполняет идемпотентное обновление зашифрованной сессии после явного подтверждения владельца.

## Единая доменная модель

`ConnectionAttempt` содержит UUID попытки, organisation ID, owner ID, метод (`phone`, `qr`, `tdata`), статус, время истечения и серверный transient state. Он не содержит session string, Telegram-код, 2FA-пароль, raw QR или `tdata`.

`TelegramConnection` связывает организацию с внутренним UUID, Telegram numeric account ID, зашифрованной сессией, состоянием supervisor и метаданными подключения. Telegram numeric account ID имеет уникальное ограничение в БД; повторный импорт того же аккаунта обновляет его сессию только в той же организации и после owner authorization. Сеансовый материал шифруется существующим AES-GCM keyring; в repr, pydantic dump и аудит попадают только безопасные метаданные.

## API-контракт

Все маршруты требуют аутентифицированного trusted principal и server-issued organisation context. Роли/organisation ID не принимаются из тела запроса.

- `POST /telegram/connections/phone/start` — принимает номер, возвращает attempt ID и `code_requested`.
- `POST /telegram/connections/{attempt_id}/phone/confirm` — принимает код, возвращает `authorized` или `password_required`.
- `POST /telegram/connections/{attempt_id}/phone/password` — принимает 2FA-пароль и завершает попытку.
- `POST /telegram/connections/qr/start` — возвращает attempt ID, QR payload и expiry.
- `GET /telegram/connections/{attempt_id}/qr/status` — безопасно возвращает состояние; не включает session material.
- `POST /telegram/connections/tdata/ticket` — выдаёт одноразовый короткоживущий ticket и временную публичную часть ключа локальному CLI.
- `POST /telegram/connections/tdata/complete` — принимает ticket и ciphertext session envelope, проверяет владельца/ticket/account, расшифровывает только в памяти и сразу сохраняет сессию AES-GCM keyring.

Все ответы используют ограниченный error taxonomy: `invalid_request`, `attempt_expired`, `attempt_not_found`, `authorization_pending`, `password_required`, `authorization_failed`, `connection_conflict`, `service_unavailable`. Технические исключения, URL/параметры, пароль, код и секреты в ответ не включаются.

## Безопасность и эксплуатация

- API ID/hash, session encryption key и platform owner credentials остаются только в Railway Variables; не в GitHub и не в клиентском JavaScript.
- Локальный CLI запрашивает API ID/hash и passcode через hidden input или локальные env vars; не печатает и не пишет их.
- Временная закрытая часть ticket хранится только в памяти сервера до expiry и уничтожается при consume, expiry либо ошибке; CLI никогда не получает ключ шифрования Railway.
- Перед сохранением каждого метода требуется `get_me`; account ID из результата сопоставляется с материалом метода.
- Любая ошибка хранилища, шифрования или ownership check означает deny и очищает transient material.
- Supervisor стартует только после успешного атомарного сохранения encrypted session; archive/revoke очищает lease и запрещает дальнейшую работу.
- События аудита содержат метод, статус, owner/organisation IDs и время, но не содержат секретов.

## Проверка

Тесты используют только synthetic auth keys и fake Telegram clients. Матрица покрывает phone code/2FA/TTL, QR pending/expiry/owner isolation, `tdata` ticket/replay/source safety, encryption/redaction, идемпотентность, ownership, supervisor start/stop и fail-closed ошибки PostgreSQL.

Локальная acceptance-проверка реального `tdata` уже подтверждена отдельно; её результаты и исходные данные не входят в репозиторий. Реальные QR/phone проверки запускаются только explicit opt-in владельца и не являются частью CI.
