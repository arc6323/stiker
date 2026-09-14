# StickerMe Ai

Telegram-бот для персональных AI-стикерпаков.

## Готово
- принимает 3–5 фото пользователя;
- предлагает 6 стилей;
- делает 2 AI-превью;
- генерирует полный набор из 20 стикеров;
- приводит изображения к PNG 512x512;
- автоматически создаёт Telegram sticker set;
- поддерживает Telegram Stars;
- содержит бесплатный тестовый режим.

## Переменные окружения
Обязательные:
- `BOT_TOKEN` — токен от BotFather;
- `OPENAI_API_KEY` — OpenAI API key.

Дополнительные:
- `OPENAI_IMAGE_MODEL=gpt-image-1`
- `OPENAI_VISION_MODEL=gpt-5-mini`
- `PACK_PRICE_STARS=399`
- `ENABLE_PAYMENTS=false`
- `ALLOW_FREE_PACKS=true`
- `AUTO_PUBLISH_STICKER_SET=true`

## Тестовый режим
Пока продукт проверяется:
```env
ENABLE_PAYMENTS=false
ALLOW_FREE_PACKS=true
```

## Продакшен с оплатой Stars
После проверки генерации:
```env
ENABLE_PAYMENTS=true
ALLOW_FREE_PACKS=false
```

## Запуск
```bash
pip install -r requirements.txt
python bot.py
```

## Docker
```bash
docker build -t stickerme-ai .
docker run --env-file .env stickerme-ai
```

## Безопасность
`.env` и папка `data/` исключены из Git. Не публикуй `BOT_TOKEN` и `OPENAI_API_KEY` в коде.
