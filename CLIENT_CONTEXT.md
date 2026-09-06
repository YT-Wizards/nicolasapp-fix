# Контекст клиента: VYT

## Кто клиент и что делает приложение

Клиент — Nico. Он разработал macOS-приложение VYT, которое превращает готовое видео с HeyGen-презентером в полностью смонтированный длинный YouTube-ролик.

Текущий pipeline:

1. Пользователь загружает готовое видео HeyGen.
2. Приложение извлекает аудио и транскрибирует narration с таймкодами.
3. AI анализирует историю и планирует визуальные сцены.
4. Для сцен генерируются разные типы визуала:
   - Veo/SnapGen video clips;
   - AI images через Algrow;
   - фрагменты исходного HeyGen-презентера;
   - split-screen и статичные сцены.
5. Assets проходят AI quality review.
6. FFmpeg собирает финальный ролик в 1080p и сохраняет оригинальную аудиодорожку.

Обычная длина видео: 8–35 минут. Визуальные сцены должны меняться примерно каждые 4–6 секунд.

## Требуемый визуальный стиль

Ориентир — FaceTuber/Bertha:

- простой, реалистичный и информативный YouTube B-roll;
- естественная consumer-camera съёмка;
- ordinary smartphone/camcorder look;
- без cinematic, glossy или рекламного вида;
- без очевидных признаков AI-generated imagery;
- статичные изображения должны иметь только лёгкий стабильный zoom;
- визуал должен прямо соответствовать конкретной фразе narration.

## Основные проблемы клиента

### Надёжность генерации и скачивания

- Veo/SnapGen иногда не создаёт video clip.
- Provider requests завершаются timeout-ами.
- Уже созданный asset может быть оплачен, но не скачан.
- Signed URLs или storage provider могут временно не отвечать.
- Повторный запуск иногда может привести к повторной оплате.

### Восстановление и состояние

- Нужно восстанавливать оплаченные assets без повторной генерации.
- Нужно продолжать job после закрытия приложения, сбоя сети или падения процесса.
- Должны сохраняться промежуточные этапы: transcription, planning, review, generation, render.
- Очередь должна быть durable, а не только находиться в памяти Electron.
- Нужно надёжно выполнять два video jobs одновременно.

### Качество визуального результата

- AI иногда создаёт нелогичные сцены.
- В сценах могут появляться дублирующиеся предметы.
- Персонажи и объекты могут быть непоследовательными.
- Визуал иногда не соответствует narration.
- Prompt должен описывать буквально один понятный subject/action/place.
- Нужно сохранять реалистичный consumer-camera стиль.

### Quality control

- AI review иногда слишком строгий.
- Один неудачный asset не должен останавливать весь production.
- Технически валидный, но неидеальный paid asset не должен автоматически приводить к новой покупке.
- Нужны hard technical checks и soft editorial warnings.
- QC должен уметь деградировать в image/avatar fallback, не разрушая весь монтаж.

### Транскрипция и AI-анализ

- Длинные аудиофайлы транскрибируются нестабильно или слишком долго.
- AI иногда возвращает неполный JSON/ответ.
- Нужно уменьшить стоимость анализа и review.
- Нужно уменьшить количество повторных запросов.
- Планирование должно точно связывать каждый visual beat с соответствующей частью narration.

### Производительность

- Сейчас production может занимать больше часа.
- Цель — по возможности 20–30 минут.
- Нужно уменьшить лишние retries, повторный анализ и последовательные операции.
- Параллельная генерация двух jobs должна быть предсказуемой и не приводить к перегрузке providers.

## Требуемые архитектурные улучшения

### Durable queue

Очередь должна храниться в SQLite или другом надёжном локальном хранилище и переживать:

- закрытие Electron;
- перезапуск приложения;
- падение Python-процесса;
- временную потерю сети;
- перезапуск компьютера.

Для каждого job нужны статусы вроде:

```text
queued
running
paused
waiting_for_provider
waiting_for_download
rendering
completed
failed
cancelled
recoverable
```

### Resumable checkpoints

Checkpoint должен сохранять результат каждого этапа:

```text
source identity
transcript
story bible
beats
planned scenes
reviewed scenes
provider operations
paid remote IDs
download status
local asset paths
asset checksums
rendered segments
final export status
cost ledger
```

### Idempotency

Каждая платная операция должна иметь стабильный operation key, например:

```text
job_id + scene_id + asset_type + prompt_hash
```

До платного запроса нужно записывать pending operation. После ответа provider нужно немедленно сохранять remote job ID. При повторном запуске сначала нужно проверять существующую операцию и только после подтверждённого terminal failure разрешать новую покупку.

### Controlled retries

Нужно разделить ошибки на категории:

- retryable network error;
- rate limit;
- provider temporary failure;
- paid asset recovery state;
- terminal provider failure;
- quality rejection;
- local media failure;
- budget exhaustion.

Retry должен иметь ограничение, exponential backoff, jitter и общий лимит стоимости.

### Provider fallbacks

Нужно предусмотреть fallback между media providers, а не только между AI-моделями анализа.

Пример стратегии:

```text
Veo video
  -> alternate video provider
  -> AI image
  -> original presenter/avatar
```

Fallback не должен покупать новый asset, если старый paid job ещё может быть восстановлен.

### Asset cache

Кэш должен быть content-addressed и использовать hash исходного файла/промпта, а не только абсолютный путь и mtime.

Кэш должен хранить:

- provider;
- remote ID;
- prompt hash;
- source hash;
- checksum локального файла;
- media type;
- cost;
- creation date;
- recovery status.

Оплаченные assets нельзя автоматически удалять до истечения подтверждённого retention period и явного разрешения на очистку.

### Monitoring

В интерфейсе нужны понятные данные:

- текущий этап;
- процент выполнения;
- ожидаемое время;
- текущая и прогнозируемая стоимость;
- сколько assets восстановлено;
- сколько assets сгенерировано;
- какие сцены используют fallback;
- какие операции ожидают provider/download;
- причина остановки или паузы;
- возможность Resume/Retry/Cancel.

## Рекомендуемый порядок реализации

1. Durable SQLite job queue и восстановление jobs после перезапуска.
2. Asset operation ledger с remote IDs и idempotency keys.
3. Запрет повторной оплаты при crash-after-payment.
4. Content-addressed asset cache и безопасный retention.
5. Checkpoints для render segments и final export.
6. Chunked transcription с кэшированием и проверкой таймкодов.
7. Provider circuit breaker, rate limiter и controlled retries.
8. Настоящий media provider fallback.
9. Двухуровневый QC: технический hard gate и редакторские warnings.
10. Снижение стоимости AI analysis/review через batching, кэширование и adaptive review.
11. End-to-end тесты для crash, timeout, 429, provider failure, restart и двух параллельных jobs.

## Критерий успеха

Пользователь выбирает готовое видео HeyGen, нажимает одну кнопку и получает coherent, high-quality, fully edited YouTube video без ручного восстановления, повторной оплаты уже созданных assets и необходимости перезапускать весь процесс после временной ошибки.

## Важное техническое наблюдение по текущему репозиторию

В текущей версии уже есть полезные механизмы checkpoint, paid-asset recovery, cost limits и fallback. Однако состояние jobs Electron хранится в памяти, платные POST-запросы не имеют полноценной idempotency-защиты, а render-сегменты не сохраняются как отдельные resumable steps. Поэтому первым архитектурным приоритетом должен быть durable operation ledger, а не дальнейшее усложнение prompt-ов.
