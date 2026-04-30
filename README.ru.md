**Языки:** [English](README.md) · [简体中文](README.zh-CN.md) · [Español](README.es.md) · [日本語](README.ja.md) · [Русский](README.ru.md)

<!-- synced-with: README.md @ 8838be2 -->

> Перевод выполнен с помощью ИИ. Корректировки от носителей языка приветствуются — открывайте PR.

<div align="center">

# 🧠 supamem

**Двойная память на базе Qdrant для AI-агентов программирования**

*Дайте Claude Code, Cursor и OpenCode постоянную семантическую + структурную память во всех проектах.*

[![PyPI](https://img.shields.io/pypi/v/supamem?style=flat-square&logo=pypi&logoColor=white&color=blue)](https://pypi.org/project/supamem/)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-success?style=flat-square)](LICENSE)
[![Qdrant](https://img.shields.io/badge/Qdrant-1.10%2B-DC382D?style=flat-square&logo=qdrant&logoColor=white)](https://qdrant.tech/)
[![MCP](https://img.shields.io/badge/MCP-1.13%2B-9333EA?style=flat-square)](https://modelcontextprotocol.io/)
[![Powered by SoftChat](https://img.shields.io/badge/Powered%20by-SoftChat-FF4D8D?style=flat-square)](https://app.softchat.ru)

</div>

---

> ### 👋 Создано [**Дмитрием Суховым**](https://www.linkedin.com/in/dzmitrys/) — AI-native Solution / Software Architect / CTO
>
> Доступен для **консалтинга** по AI-продуктам, **интеграции AI в существующие продукты** и **автоматизации бизнес-процессов**.
>
> Если вы запускаете LLM-фичи, оцениваете retrieval-пайплайны, укрепляете агентные системы или строите AI-first продукт с нуля — давайте поговорим.
>
> [![LinkedIn — Dzmitry Sukhau](https://img.shields.io/badge/LinkedIn-Dzmitry%20Sukhau-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/dzmitrys/)
> &nbsp;&nbsp;
> [![Open to Consulting](https://img.shields.io/badge/Open%20to-Consulting%20%26%20Architecture-22C55E?style=for-the-badge&logo=anthropic&logoColor=white)](https://www.linkedin.com/in/dzmitrys/)

---

## ✨ Что такое supamem?

`supamem` — это однобинарный CLI, который подключает **слой памяти продакшен-уровня** к любому
AI-ассистенту программирования. Закиньте его в свежий репозиторий, выполните `supamem init` —
и ваши агенты мгновенно получат:

- 🔍 **Семантический поиск** по проектным заметкам, ADR, решениям и прошлым диалогам (гибридный sparse+dense retrieval)
- 🤖 **MCP-сервер**, к которому может подключиться любой совместимый клиент (Claude Code, Cursor, OpenCode)
- 🪝 **Хуки на каждого клиента**, автоматически подгружающие релевантную память при старте сессии и редактировании файлов
- 📊 **Статистику Welford**, чтобы видеть, какая память реально вызывается
- 🧪 **Eval-харнесс** с золотым корпусом из 33 запросов для обнаружения регрессий retrieval

Обкатано в продакшене внутри [SoftChat](https://app.softchat.ru) (фазы 80.1–80.5) перед тем,
как было выделено в самостоятельный пакет, который может принять любая команда.

---

## 🎯 Зачем нужен supamem

**Проблема:** У агентов программирования нет памяти между сессиями. Каждый раз, открывая новый
диалог в Claude Code / Cursor / OpenCode, модель не знает ничего о вашем коде, прошлых решениях,
ADR, известных проблемах или соглашениях. Поэтому либо:

1. Вы **переклеиваете 5–15 КБ контекста** в начале каждой сессии (медленно, чревато ошибками, дорого), либо
2. Вы даёте агенту **барахтаться** — он бегает grep'ом по репозиторию, задаёт повторные вопросы,
   забывает решения недельной давности и заново открывает те же грабли, которые вы документировали
   полгода назад.

**Решение:** Постоянный семантический + структурный слой памяти, который автоматически достаёт
*правильные* 1–2 КБ контекста для *текущего* промпта — без ручной вставки, без повторных объяснений,
без раздувания контекста.

> **Бенч фазы 80.1 (33 размеченных golden, реальные сессии Claude Code):**
> **−78.5% токенов vs наивный whole-doc retrieval** при том же recall, **p95 73 мс** end-to-end.
>
> Полная оценка — та же, что мы прогнали внутри SoftChat для фиксации продакшен-пайплайна.
> Методология: 33 репрезентативных dev-запроса → сравнение 4 retrieval-плеч (baseline_union,
> tuned_current, tuned_hybrid, mem0_vector) → измерены token count + recall CI + латентность по плечам.

### 📊 Расход токенов: агент с памятью vs без

Цифры ниже — на **типичную 30-ходовую сессию Claude Code** при реальной кодовой базе с ~50 ADR /
insights / rules (≈ как у SoftChat). YMMV — но *соотношение* между плечами держится.

| Подход | Токенов/ход | Токенов/30-ходовую сессию | Заметки |
|---|---:|---:|---|
| ❌ Без слоя памяти | Авто-инъекция **≈ 0**, но контекст вы клеите вручную | **30 000–80 000** (ручная вставка, повторно) | Когнитивная нагрузка уходит на копирование, а не на сборку |
| ⚠️ Наивный RAG (whole-doc embed) | ~5 800 / ход | **~174 000** | Раздуто, тянет большие файлы, когда нужен был один абзац |
| ✅ **supamem `tuned_hybrid`** | **~1 250 / ход** | **~37 500** | Тот же recall, **−78.5% токенов** vs наивный RAG |

### 💰 Примерная экономия на стоимости инференса

Прайс Anthropic API (март 2026):
**Sonnet 4.6 = $3 / Mtok input** · **Opus 4.7 = $15 / Mtok input**.

| Модель | Сэкономлено токенов/сессию vs наивный RAG | Сэкономлено $/сессию | В месяц (110 сессий) |
|---|---:|---:|---:|
| Sonnet 4.6 | **136 500** | **$0.41** | **~$45/разработчик** |
| Opus 4.7 | **136 500** | **$2.05** | **~$225/разработчик** |

Команда из 10 инженеров на Opus экономит **~$2 250/месяц** только на input-токенах — без учёта
стоимости медленных итераций, потерянных решений и времени на повторное вставление контекста.
Экономия output-токенов (меньше галлюцинаций, меньше туда-сюда) ложится сверху.

### 🥊 Сравнение с альтернативами

| | Без памяти | Наивный RAG | mem0 / атомарные факты | **supamem (tuned_hybrid)** |
|---|:---:|:---:|:---:|:---:|
| Авто-инъекция при старте сессии | ❌ | ⚠️ | ✅ | ✅ |
| Гибридный sparse+dense retrieval | ❌ | ❌ | ❌ | ✅ |
| Сохранение код-идентификаторов | ❌ | ✅ | ❌ (теряет имена) | ✅ |
| Зафиксированная схема + golden eval | ❌ | ❌ | ❌ | ✅ |
| Multi-client (Claude/Cursor/OpenCode) | ❌ | ❌ | ⚠️ | ✅ |
| Латентность p95 | n/a | ~120 мс | ~80 мс | **73 мс** |
| Раздувание токенов | Высокое (вручную) | Самое высокое | Низкое, но с потерями | **Самое низкое при полном recall** |

**Почему гибрид?** BM25 ловит *точные идентификаторы* (`ChatService.generate`, имена env-переменных,
пути файлов), которые dense embeddings размазывают. Dense ловит *семантическое намерение*
("как мы обрабатываем billing-вебхуки?"), которое BM25 пропускает. RRF-фьюжн объединяет оба
ранжирования, чтобы получить лучшее от каждого.

**Почему не mem0?** Извлечение атомарных фактов в mem0 теряет код-идентификаторы — recall на бенче
из 33 запросов был **0.015** (фактически ноль). Отлично для персональной памяти типа CRM, не для
кодо-aware retrieval.

---

## ⚡️ Быстрый старт за 60 секунд

```bash
# 1. Установка (uv — самый быстрый путь)
uv tool install supamem

# 2. Запустить Qdrant (один раз, ~30с)
docker run -d -p 6333:6333 -p 6334:6334 -v $HOME/.qdrant:/qdrant/storage qdrant/qdrant:latest

# 3. Проинициализировать ваш проект
cd your-project
supamem init

# 4. Подключить к AI-клиенту
supamem install --client claude-code   # или cursor / opencode

# 5. Проверить, что всё здорово
supamem doctor
```

И всё. Откройте Claude Code (или ваш предпочитаемый клиент) внутри проекта — инструмент памяти уже в меню. ✨

---

## 👀 Посмотрите как работает — `supamem live`

Запустите `supamem live` в соседнем терминале, чтобы видеть каждый retrieval-вызов в реальном времени — отлично подходит рядом с Claude Code / Cursor / OpenCode для мгновенной видимости тихих PreToolUse-инъекций (которые и экономят токены за счёт того, что НЕ показывают UI).

![supamem live dashboard](docs/media/supamem-live.svg)

**SessionStart-баннер** (v0.1.4+) также вставляет однострочный статус в ваш AI-клиент при открытии сессии: `🧠 supamem v0.1.4 · <collection> · <N> chunks · audit <path>` — авто-детект Claude Code / Cursor / OpenCode по env-переменным.

> 🎬 **Интерактивное демо:** [`supamem-live.cast`](docs/media/supamem-live.cast) — закиньте в [asciinema.org/player](https://asciinema.org/) или запустите локально `asciinema play docs/media/supamem-live.cast`.

---

## 🚀 Возможности

| Возможность | Описание |
|---|---|
| 🔍 **Гибридный retrieval** | Настроенная фьюжн sparse (BM25) + dense (MiniLM), зафиксированная схема D-25 |
| 📚 **Markdown-чанкер** | Header-aware, чанки по 200 токенов с мягким потолком 250 (T-1) |
| 🤖 **MCP-сервер** | Транспорты `stdio` (по умолчанию) и `http`, официальный SDK `mcp` |
| 🪝 **Multi-client хуки** | session-start Claude Code, session-start OpenCode, MDC Cursor |
| 🧰 **Установка одной командой** | Атомарный патч конфигов с авто-бэкапом и откатом |
| 🩺 **`supamem doctor`** | Пинг Qdrant, разрешение цепочки конфига, сигнал о дрейфе версий |
| 👀 **`supamem live`** | Терминальный дашборд на Rich Live, отслеживающий audit JSONL — retrieval-вызовы видны в реальном времени (v0.1.4+) |
| 🎬 **SessionStart-баннер** | Однострочный кросс-клиентский баннер при открытии сессии (Claude Code / Cursor / OpenCode), v0.1.4+ |
| 📊 **Welford-счётчики** | Трекают recall-rate, латентность, объём запросов на проект |
| 🧪 **Eval-харнесс** | Золотой корпус из 33 запросов + детектор регрессий |
| 🔁 **Brownfield-миграция** | Детектит существующий `dev_memory` и мигрирует неразрушающе |
| 🎨 **Стильный CLI** | Спиннеры, панели и цвет на Rich — всегда видно прогресс |

---

## 📋 Предварительные требования

Реально нужно две вещи: **Python 3.12+** и **Qdrant**. Всё остальное опционально.

<details>
<summary><b>🐍 Python 3.12+ &nbsp;·&nbsp; кликните, чтобы развернуть команды установки</b></summary>

```bash
# macOS (Homebrew)
brew install python@3.12

# Linux (Ubuntu/Debian)
sudo apt install python3.12 python3.12-venv

# Windows (PowerShell)
winget install Python.Python.3.12
```

Настоятельно рекомендуем установить [`uv`](https://docs.astral.sh/uv/) — самый быстрый менеджер пакетов Python:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

</details>

<details>
<summary><b>🗄️ Qdrant 1.10+ &nbsp;·&nbsp; векторная БД (обязательна)</b></summary>

Самый простой путь — Docker:

```bash
docker run -d --name qdrant \
  -p 6333:6333 -p 6334:6334 \
  -v $HOME/.qdrant:/qdrant/storage \
  qdrant/qdrant:latest
```

Или через `docker compose`:

```yaml
services:
  qdrant:
    image: qdrant/qdrant:latest
    ports: ["6333:6333", "6334:6334"]
    volumes: ["./qdrant_data:/qdrant/storage"]
    restart: unless-stopped
```

Нет Docker? Запустите managed-кластер на [Qdrant Cloud](https://cloud.qdrant.io/) (бесплатный тариф)
и укажите URL в `supamem init`.

</details>

<details>
<summary><b>🤖 Клиент с поддержкой MCP &nbsp;·&nbsp; выберите хотя бы один</b></summary>

| Клиент | Установка | Заметки |
|---|---|---|
| [Claude Code](https://claude.com/claude-code) | `npm install -g @anthropic-ai/claude-code` | Поддержка MCP первого класса |
| [Cursor](https://cursor.com/) | Скачать с cursor.com | Использует MDC-правила + MCP |
| [OpenCode](https://opencode.ai/) | `curl -fsSL https://opencode.ai/install \| bash` | Open-source TUI, нативный MCP |

</details>

---

## 📦 Установка

```bash
# Рекомендуется: uv (быстрее всего, изолированно)
uv tool install supamem

# Альтернатива: pipx (тоже изолированно)
pipx install supamem

# Обычный pip (в venv)
pip install supamem
```

Проверить:

```bash
supamem --version
```

Должны увидеть цветной баннер и строку об авторах. 🎨

> **Актуальная версия:** `v0.1.5` опубликован на [PyPI](https://pypi.org/project/supamem/).
> Релиз через Trusted Publisher OIDC — у каждого wheel есть подтверждение происхождения.

---

## 🎯 Команды CLI

| Команда | Назначение |
|---|---|
| `supamem init` | Greenfield-инициализация — пинг Qdrant, создание коллекции, запись `.supamem/config.toml` |
| `supamem install --client <name>` | Патч конфига клиента (`claude-code`, `cursor`, `opencode`) — атомарно с бэкапом |
| `supamem index` | Embed dev-памяти в Qdrant зафиксированным tuned-hybrid пайплайном (D-25) |
| `supamem mcp-server` | Запуск MCP-сервера (`--transport stdio` по умолчанию; `--transport http` для HTTP) |
| `supamem hook <client>` | Хуки сессии/редактирования на клиента (вызываются самим клиентом) |
| `supamem doctor` | 🩺 Пинг Qdrant, печать разрешённой цепочки конфига, отчёт о дрейфе версий |
| `supamem stats` | Welford schema-v2 счётчики использования из `.supamem/state/` |
| `supamem live` | 👀 Live-дашборд audit JSONL — безопасен в пайпе (plain JSONL вне TTY); обрабатывает ротацию, ресайз, Ctrl-C |
| `supamem migrate` | Brownfield-миграция с уже существующей коллекции `dev_memory` |
| `supamem eval` | Прогон регрессионного харнесса по встроенному корпусу из 33 запросов |
| `supamem uninstall --client <name>` | Чисто откатить `supamem install` |

Каждая долгая команда показывает **живой спиннер** с прошедшим временем — всегда видно, что она работает.
`--help` на любой подкоманде даёт детали.

---

## 🪛 Подключение к клиенту

<details>
<summary><b>Claude Code</b></summary>

```bash
supamem install --client claude-code
```

Добавляет запись в `~/.claude.json` под `mcpServers` и регистрирует хук session-start в `~/.claude/hooks/`.
Превью без применения — флаг `--dry-run`.

</details>

<details>
<summary><b>Cursor</b></summary>

```bash
supamem install --client cursor
```

Патчит `.cursor/mcp.json` и пишет `.cursor/rules/dual-memory.mdc`.

</details>

<details>
<summary><b>OpenCode</b></summary>

```bash
supamem install --client opencode
```

Обновляет `~/.config/opencode/opencode.json` и пишет хук session-start в `~/.config/opencode/hooks/`.

</details>

> ✨ **v0.2.0 — мульти-проектная установка (по умолчанию теперь per-workspace).** `supamem install` теперь по умолчанию пишет в `<repo>/.mcp.json` (project scope для Claude Code) и `<repo>/.cursor/mcp.json` (workspace-scope для Cursor), автоматически инжектируя `SUPAMEM_PROJECT_ROOT`. Миграция с устаревшей глобальной установки: запустите `supamem repair` в каждом supamem-проекте — он удалит устаревшие глобальные записи и переустановит на уровне проекта.
>
> Жёсткое требование поиска (opt-in, только Claude Code): `supamem install --client claude-code --enforce-search` регистрирует PreToolUse-гейт, который ОТКЛОНЯЕТ `Edit/Write/MultiEdit`, если в текущем пользовательском ходе не было вызова `mcp__supamem__dual_memory_search`. Локальный обход на сессию: `SUPAMEM_GATE_DISABLE=1`. У Cursor API хуков пока нет fail-closed события до редактирования — вместо этого мы внедряем `agentMessage`-подсказку через `beforeSubmitPrompt`; отключается через `SUPAMEM_ADVISORY_DISABLE=1`.
>
> SessionStart-баннер теперь начинается с одно-символьного флага здоровья (`✓` / `⚠`) и добавляет `update v0.X.Y available`, когда фоновый update-check кешировал новый релиз.

> 🛟 **MCP запущен из неправильного cwd?** Некоторые хосты (Cursor, отдельные IDE-обёртки) запускают MCP-подпроцесс из `$HOME`, а не из рабочей области — supamem откатывается к коллекции по умолчанию (`dev_memory_tuned_hybrid`) и получает 404 от Qdrant.
> Задайте `SUPAMEM_PROJECT_ROOT=/abs/path/to/workspace` в MCP-конфиге хоста (например, в блоке `env` файла `~/.cursor/mcp.json` или в `~/.claude.json` под `mcpServers.supamem.env`).
> Если переменная не задана, supamem пройдёт вверх по родительским каталогам в поисках `.supamem/config.toml` или `pyproject.toml` с `[tool.supamem]` — и выведет однострочное предупреждение в stderr, если ничего не найдёт.
> Проверьте через `supamem doctor` из корня репозитория: разрешённая коллекция должна совпадать с тем, что возвращает `dual_memory_search` в вашем MCP-клиенте.

---

## 🧠 Как это работает

```text
┌─────────────────┐    MCP/stdio     ┌─────────────────┐    REST    ┌─────────────┐
│ Claude / Cursor │ ───────────────► │  supamem MCP    │ ─────────► │   Qdrant    │
│   / OpenCode    │ ◄─────────────── │     server      │ ◄───────── │  (векторы)  │
└─────────────────┘                  └─────────────────┘            └─────────────┘
        │                                    ▲
        │ хук session-start                  │ tuned-hybrid retrieval
        ▼                                    │ (BM25 + MiniLM фьюжн)
┌─────────────────┐                          │
│ supamem hook    │ ─────────────────────────┘
│ (авто-recall)   │
└─────────────────┘
```

- **Indexer** чанкует Markdown по заголовкам (T-1 chunker, цель 200 токенов / мягкий максимум 250)
- **Embedders** производят sparse (BM25) и dense (MiniLM-L6) векторы
- **Retrieval** запускает оба плеча параллельно, фьюзит через reciprocal rank fusion, возвращает top-k
- **MCP-сервер** экспонирует `dual_memory_search` (чтение) и `dual_memory_write`
  (запись/идемпотентная персистенция агентской памяти) — плюс `qdrant_find` и `qdrant_store`
  как drop-in алиасы для пользователей upstream `mcp-server-qdrant` (отключить `SUPAMEM_QDRANT_ALIASES=0`)
- **Хуки** вызывают `supamem hook <client>` в нужный момент, и память подгружается прозрачно

---

## 🤝 Контрибьютинг

Принимаем PR! Быстрый старт:

```bash
git clone https://github.com/dzmitrys-dev/supamem.git
cd supamem
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest
ruff check .
```

Приходите с in-tree сетапом `dev_memory`? Смотрите [MIGRATION.md](MIGRATION.md).

---

## 📜 Лицензия

MIT — см. [LICENSE](LICENSE).

---

<div align="center">

### 💜 С заботой делают

<a href="https://app.softchat.ru"><b>SoftChat</b></a> &nbsp;·&nbsp; <a href="https://softskillz.ai"><b>SoftSkillz</b></a>

*Русскоязычная AI-чат платформа &nbsp;·&nbsp; AI-first продуктовая инженерия*

`supamem` выделили из продакшен-стека памяти SoftChat, чтобы каждая команда могла работать на одном
обкатанном пайплайне. Если он сделал ваших агентов умнее — поставьте ⭐. И заходите посмотреть, что мы
с его помощью строим.

<sub>Сделано с заботой в Беларуси &nbsp;🇧🇾&nbsp; · &nbsp;<a href="https://app.softchat.ru">app.softchat.ru</a> &nbsp;·&nbsp; <a href="https://softskillz.ai">softskillz.ai</a></sub>

</div>
