# Народное мнение — `social-research`

**Навык для Claude Code: за 20–30 минут узнать, что реально пишут люди в соцсетях — и отделить живой опыт от рекламы.**

Вопрос «что выбрать / что работает / на что жалуются» → поиск по X, Reddit, Hacker News, YouTube, Хабру, Дзену, vc.ru,
Пикабу, отзовикам и Telegram через API → модель размечает каждый пост → рейтинг вариантов по **разным авторам** →
разбор аналитика, два критика, редактор → страница с выводами и ссылками на посты. Если по итогу кто-то будет делать
работу — ещё и документ-задание исполнителю.

Пара к «опросу нейросетей»: там спрашиваем сотни моделей, здесь слушаем, что люди уже написали.

> English: a Claude Code skill that searches social networks via APIs (X, Reddit, HN, YouTube, Russian platforms,
> Telegram), labels every post with an LLM, ranks options by **distinct independent authors with first-hand experience**
> (vendors and self-promotion counted separately), and writes a verified report or an actionable brief. Docs are in Russian.

## Что на выходе

- **`report.html`** — итог сверху, рейтинг «что выбирают / хвалят / ругают / свой опыт», динамика по месяцам, цитаты
  «почему», все посты с фильтрами. Каждая ссылка проверена: она есть в собранных данных.
- **Честная сила вывода** — число разных независимых авторов, которые пробовали сами: «сила 3 (сработало 2, не сработало 1)».
  Продавцы, авторы своих продуктов, новости и мнения в силу не входят и подписаны прямо.
- **Близость к задаче** у каждого вывода: наша задача / соседняя / другой домен.
- **Документ исполнителю** (`workflows/brief.js`): вводные заказчика → варианты карточками → грабли → тесты с порогами
  «прошёл» → первые два дня → вопросы до старта. Его проверяют три сверщика, потом «исполнитель» читает и ставит оценку.
  Выход — самодостаточный HTML, который удобно отправить файлом в Telegram.

## Как устроено

```
вопрос + контекст заказчика
  → план запросов (пишет модель)            data/plans/*.json
  → поиск с пределом кредитов, дедуп        scripts/run_plan.py
  → добор комментариев и полных текстов     scripts/expand.py
  → разметка постов → аналитик → 2 критика → редактор     workflows/analyze.js (Claude Code Workflow)
  → страница и проверка ссылок              scripts/save_analysis.py, render.py
  → рейтинг по разным авторам               scripts/opinions.py (два сводчика-модели → слияние синонимов → цитаты)
  → документ исполнителю (по желанию)       workflows/brief.js → scripts/check_doc.py → scripts/doc_html.py
```

Принцип **AI-first**: смысл (что релевантно, кто продавец, что сработало, какие названия — одно и то же) решает
модель. Скрипты держат только контракты: ключи, форматы, учёт кредитов, проверку ссылок.

## Источники

| Источник | Что даёт | Ключ |
|---|---|---|
| [SocialCrawl](https://www.socialcrawl.dev) | X, Reddit (+ комментарии), YouTube (+ комментарии), LinkedIn, Bluesky и др. | `socialcrawl.env` → `SOCIALCRAWL_API_KEY` |
| [twitterapi.io](https://twitterapi.io) | X: глубина, статьи, авторы | `twitterapi.env` → `TWITTERAPI_IO_API_KEY` |
| Hacker News (Algolia) | посты и треды | не нужен |
| [Yandex Search API](https://yandex.cloud/ru/services/search-api) | Дзен, Пикабу, vc.ru, Хабр, отзовики | `yandex-search.env` → `YANDEX_SEARCH_API_KEY`, `YANDEX_FOLDER_ID` |
| [TGStat API](https://api.tgstat.ru) | посты Telegram-каналов и чатов | `tgstat.env` → `TGSTAT_TOKEN` |
| VK API | посты ВКонтакте | `vk.env` → `VK_SERVICE_TOKEN` |
| UnifAPI | запасной X/YouTube | `unifapi.env` → `UNIFAPI_API_KEY` |

Новый источник — один файл `scripts/sources/<имя>.py` по образцу `_template.py`. Что подключено:
`python3 scripts/social.py --list`. Обычный запуск стоит 0,3–0,5 $ поиска и 0,5–1 $ на модели сводки.

## Установка

```bash
git clone https://github.com/Victory-SergD/SergD-Social-Research.git && cd SergD-Social-Research
pip install -r requirements.txt                      # Python 3.10+

# ключи: одна строка ИМЯ=значение в файле, права 600; папку можно сменить переменной SOCIAL_RESEARCH_KEYS
K=~/.config/sergd-social && mkdir -p $K && chmod 700 $K
for f in keys.example/*.env.example; do cp -n "$f" "$K/$(basename "${f%.example}")"; done
chmod 600 $K/*.env      # впишите свои ключи в нужные файлы; без ключа источник просто «НЕТ КЛЮЧА»

# навык для Claude Code — с путём к этой папке
mkdir -p ~/.claude/skills/social-research
sed "s|^P=<папка[^#]*|P=\"$PWD\"   |" skill/SKILL.md > ~/.claude/skills/social-research/SKILL.md
mkdir -p data/plans briefs
```

Дальше просто попросите Claude Code: «что пишут люди про …», «поищи по соцсетям …» — навык возьмётся сам.
Ваши планы, запуски и документы лежат в `data/`, `runs/`, `briefs/` и в git не попадают (`.gitignore`).

**Модели для сводки мнений и отбора веток** — `scripts/llm_backend.py`: любые OpenAI-совместимые API, по умолчанию
[OpenRouter](https://openrouter.ai) (`openrouter.env` → `OPENROUTER_API_KEY`). Модели меняются переменными
`SOCIAL_LLM_*` (описание в начале файла). Разметка и разбор (`workflows/*.js`) идут помощниками Claude Code.

## Быстрый старт вручную

```bash
python3 scripts/social.py --list
python3 scripts/run_plan.py examples/plan.example.json --tag 2026-01-01-пример --ordered-by "я" --max-sc 150 --max yandex=10
# подключаемые сервисы (yandex, tgstat, vk) без --max <сервис>=N пропускаются; since в плане поставьте посвежее
python3 scripts/expand.py runs/2026-01-01-пример --max-sc 40
# в Claude Code: Workflow workflows/analyze.js с args из последней строки вывода (без Workflow — те же промпты
# через Agent и JSON для save_analysis.py, формат — в его --help), затем:
python3 scripts/save_analysis.py <output-file> runs/2026-01-01-пример
python3 scripts/opinions.py runs/2026-01-01-пример && python3 scripts/render.py runs/2026-01-01-пример
```

## Безопасность

- **Только чтение.** Навык ничего не постит, не лайкает, не подписывается и никому не пишет.
- **Ключи** живут только в папке ключей на вашей машине и нигде не печатаются. Перед коммитом —
  `python3 scripts/check_secrets.py` (ищет точные значения ключей в индексе git, сами значения не показывает).
- **Тексты постов — недоверенные данные**: «инструкции» в них модели не выполняют.
- Сырые ответы API, тексты постов, планы, документы и страницы запусков в git не попадают (`.gitignore`).
- Перед публикацией своих правок — `python3 scripts/check_secrets.py` (папки с другими ключами —
  `SOCIAL_RESEARCH_EXTRA_KEYS`).

## Структура

| Путь | Что |
|---|---|
| `skill/SKILL.md` | сам навык: когда брать, шаги, правила |
| `scripts/social.py` | единый поиск по всем источникам; `--list` — что готово |
| `scripts/sources/` | подключаемые источники (Яндекс, TGStat, VK), образец `_template.py` |
| `scripts/run_plan.py`, `expand.py` | поиск по плану с пределом кредитов; добор комментариев и полных текстов |
| `workflows/analyze.js`, `brief.js` | разбор и документ исполнителю (Claude Code Workflow) |
| `scripts/opinions.py` | рейтинг по разным авторам: выбирают / хвалят / ругают / свой опыт, месяцы, цитаты |
| `scripts/llm_backend.py`, `_llm.py` | модели для сводки (OpenRouter и др.) |
| `scripts/render.py`, `save_analysis.py`, `check_links.py` | страница запуска и проверка ссылок |
| `scripts/check_doc.py`, `doc_html.py` | контракт документа (все ссылки из данных) и HTML для отправки |

## Автор

**Сергей Дышкант** — предприниматель: IT, маркетинг, лидогенерация и автоматизация бизнеса с нейросетями.

- Telegram: [@sergei_dyshkant](https://t.me/sergei_dyshkant)
- Канал «Лидогенерация для бизнеса | SergD»: [t.me/SergD_leads](https://t.me/SergD_leads) — там разборы вроде этого
- GitHub: [Victory-SergD](https://github.com/Victory-SergD)

## Лицензия

MIT — см. [LICENSE](LICENSE).
