# Phuket Invest-Auditor — Deployment & Parser Guide

## Prerequisites

- Python 3.11+
- Ubuntu 22.04 / Debian 12 (или аналог)
- Интернет-доступ к fazwaz.com, dotproperty.co.th

---

## 1. Установка

```bash
git clone https://github.com/ashakov/cloud_solutions.git
cd cloud_solutions

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

# Установить браузер Playwright (≈150 МБ)
playwright install chromium
```

---

## 2. Конфигурация

```bash
cp .env.example .env
```

Заполни `.env`:

```env
DATABASE_URL=sqlite+aiosqlite:///./phuket_invest.db
GEMINI_API_KEY=AIza...         # для AI Reasoning Engine
TELEGRAM_BOT_TOKEN=7...        # для Telegram-бота

# Настройки парсера
SCRAPER_DELAY_MIN=2.0          # пауза между запросами (сек)
SCRAPER_DELAY_MAX=5.0
SCRAPER_MAX_PAGES=20           # страниц на один запрос (≈20 объектов/стр)
SCRAPER_HEADLESS=true          # false — открыть браузер (для отладки)
LOG_LEVEL=INFO
```

---

## 3. Инициализация БД и первый запуск

```bash
# Инициализировать схему БД и наполнить синтетическими данными (без интернета)
python main.py seed

# Проверить результат
python main.py status
```

---

## 4. Запуск реальных парсеров

### Полный прогон (все районы, condo + villa)
```bash
python main.py scrape
```

### Один район, один тип
```bash
python main.py scrape --district bang-tao --type condo
python main.py scrape --district kamala --type villa
```

### Только один источник
```bash
python main.py scrape --source fazwaz
python main.py scrape --source dotproperty
```

### Комбинирование параметров
```bash
python main.py scrape --district surin --type condo --source fazwaz
```

### Доступные районы
```
bang-tao  kamala  surin  layan  rawai  nai-harn
patong  cherng-talay  mai-khao  chalong  phuket-town
```

### Типы недвижимости
```
condo  villa  house  townhouse  land
```

---

## 5. Пересчёт бенчмарков

После накопления данных из нескольких источников пересчитай бенчмарки вручную:

```bash
python main.py benchmarks
```

Бенчмарки автоматически пересчитываются после каждого полного `scrape`.

---

## 6. Просмотр статистики

```bash
python main.py status
```

Пример вывода:
```
  ID  Source       District         Type         Status   Fetched    New
---------------------------------------------------------------------------
   3  fazwaz       bang-tao         condo        done         340    312
   2  dotproperty  bang-tao         condo        done         280    265
   1  seeder       all              all          done        1280   1280
```

---

## 7. Расписание (cron) — рекомендуется

Добавить в crontab для ежедневного обновления данных:

```bash
crontab -e
```

```cron
# Каждый день в 03:00 — полный прогон парсеров
0 3 * * * cd /path/to/cloud_solutions && .venv/bin/python main.py scrape >> /var/log/phuket_scraper.log 2>&1
```

---

## 8. Что собирают парсеры

| Поле | Описание | Используется в |
|---|---|---|
| `price_thb` | Цена объекта в тайских батах | True Entry Cost |
| `price_per_sqm_thb` | Цена за м² | Бенчмарк сравнение |
| `ownership_type` | freehold / leasehold | Налог на перевод (1.1% / 3.3%) |
| `leasehold_years` | Лет аренды (30/90) | Оценка риска |
| `cam_fee_per_sqm` | CAM fee за м²/мес | OPEX расчёт |
| `sinking_fund_per_sqm` | Разовый взнос за м² | True Entry Cost |
| `furniture_package_thb` | Стоимость меблировки | True Entry Cost |
| `monthly_rent_thb` | Фактическая аренда (если указана) | Gross Yield расчёт |
| `rental_type` | short_term / long_term | Выбор стратегии |
| `rental_yield_claimed` | Заявленная доходность % | Стресс-тест |
| `rental_program` | Управляемая аренда застройщика | Red Flag детектор |
| `rental_pool_split` | Доля инвестора (0–1) | Реальная доходность |
| `has_hotel_license` | Наличие Hotel License | Red Flag |
| `has_pool` | Наличие бассейна | OPEX (обслуживание) |
| `is_off_plan` | Объект на стадии строительства | Risk Score |
| `completion_date` | Дата сдачи (off-plan) | Risk Score |
| `year_built` | Год постройки | Амортизация |
| `days_on_market` | Дней в листинге | Ликвидность |
| `price_drop_count` | Кол-во снижений цены | Ликвидность / переговоры |
| `zone_type` | tourist / residential / mixed | Целевая аудитория |
| `title_deed` | Chanote / Nor Sor 3 | Due Diligence |

---

## 9. Устранение проблем

### Playwright не находит браузер
```bash
# Найти где установлен браузер
playwright install --dry-run

# Переустановить
playwright install chromium
```

### TLS ошибки / блокировка по IP
Добавить в `.env`:
```env
SCRAPER_DELAY_MIN=5.0
SCRAPER_DELAY_MAX=10.0
```
Или использовать VPN / прокси-сервер.

### FazWaz требует CAPTCHA
FazWaz использует Cloudflare. При блокировке:
1. Установить `SCRAPER_HEADLESS=false` для ручного прохода CAPTCHA один раз
2. Сохранить cookies в контекст Playwright (планируется в v2)

### SQLite → PostgreSQL (продакшн)
```env
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/phuket_invest
```
```bash
pip install asyncpg
python main.py seed  # пересоздаст схему в Postgres
```

---

## 10. Структура проекта

```
cloud_solutions/
├── config.py              # Настройки (URL-шаблоны, районы, env)
├── main.py                # CLI: scrape / seed / benchmarks / status
├── db/
│   ├── models.py          # SQLAlchemy модели: Property, DistrictBenchmark, ScraperRun
│   └── database.py        # Async engine, init_db()
├── parsers/
│   ├── base.py            # BaseParser + RawListing dataclass
│   ├── fazwaz.py          # FazWaz парсер
│   ├── dotproperty.py     # DotProperty парсер
│   └── normalizer.py      # upsert_listing() + rebuild_benchmarks()
├── scrapers/
│   ├── runner.py          # Оркестратор (все комбинации district×type×source)
│   └── seeder.py          # Синтетические данные (для тестов без интернета)
└── utils/
    └── logging_setup.py   # Настройка логирования
```
