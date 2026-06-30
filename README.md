# Рекомендательный сервис Instacart

Веб-приложение на Flask, которое:
- Авторизует пользователя по `user_id` через Loginom REST API (`CheckUserId`)
- Получает историю покупок из Loginom (`GetUserHistory`)
- Формирует персональную рекомендацию с помощью Mistral AI
- Отображает топ-10 товаров и рекомендацию в браузере

## Стек

| Компонент | Технология |
|-----------|-----------|
| Аналитический веб-сервис | Loginom (публикуется вручную) |
| Бэкенд | Python / Flask |
| LLM | Mistral AI (`mistral-small-latest`) |
| Деплой | Amvera Cloud |
| Репозиторий | GitHub |

---

## Локальный запуск

### 1. Клонировать репозиторий

```bash
git clone https://github.com/<ваш-логин>/<имя-репо>.git
cd <имя-репо>
```

### 2. Создать виртуальное окружение и установить зависимости

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Настроить переменные окружения

```bash
cp .env.example .env
# Отредактируйте .env — укажите MISTRAL_API_KEY, LOGINOM_URL, FLASK_SECRET_KEY
```

### 4. Запустить приложение

```bash
python app.py
```

Откройте в браузере: http://localhost:8000

---

## Структура проекта

```
├── app.py              # Flask-приложение (маршруты)
├── business_rules.py   # Бизнес-логика: Loginom API + Mistral
├── demo_rest_api.ipynb # Демо-ноутбук для проверки интеграции
├── templates/
│   └── index.html      # HTML-шаблон интерфейса
├── requirements.txt
├── amvera.yml          # Конфигурация деплоя Amvera
├── .env.example        # Шаблон переменных окружения
└── .gitignore
```

---

## Деплой на Amvera

Подробная инструкция приведена в разделе «Деплой» ниже в этом файле.

### Шаги деплоя

1. **Зарегистрируйтесь** на [amvera.ru](https://amvera.ru) и создайте новое приложение типа **Python**.

2. **Загрузите файлы** одним из способов:
   - Через интерфейс Amvera (вкладка «Репозиторий» → загрузить файлы)
   - Через git remote:
     ```bash
     git remote add amvera https://git.amvera.ru/<логин>/<имя-приложения>.git
     git push amvera main
     ```

3. **Добавьте переменные окружения** в настройках приложения Amvera:
   - `MISTRAL_API_KEY` — ключ от [console.mistral.ai](https://console.mistral.ai)
   - `LOGINOM_URL` — базовый URL вашего Loginom-сервера
   - `FLASK_SECRET_KEY` — любая длинная случайная строка

4. **Проверьте `amvera.yml`** — он уже настроен (`containerPort: 8000`).

5. **Создайте домен** в настройках приложения Amvera.

6. **Запустите сборку** — Amvera сам установит зависимости из `requirements.txt` и запустит `app.py`.

---

## Переменные окружения

| Переменная | Описание |
|-----------|----------|
| `MISTRAL_API_KEY` | API-ключ Mistral (получить на console.mistral.ai) |
| `LOGINOM_URL` | Базовый URL опубликованного пакета Loginom |
| `FLASK_SECRET_KEY` | Секрет для подписи Flask-сессий |
| `PORT` | Порт приложения (по умолчанию `8000`) |
