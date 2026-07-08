import os
import time
import json
import sqlite3
import requests
from collections import defaultdict
from dotenv import load_dotenv
from mistralai.client import Mistral

load_dotenv()

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
if not MISTRAL_API_KEY:
    raise EnvironmentError("Переменная окружения MISTRAL_API_KEY не задана.")

LOGINOM_URL_HISTORY   = os.getenv("LOGINOM_URL_HISTORY")
LOGINOM_URL_RHYTHM    = os.getenv("LOGINOM_URL_RHYTHM")
LOGINOM_URL_FORGOTTEN = os.getenv("LOGINOM_URL_FORGOTTEN")
LOGINOM_URL_BEST_TIME = os.getenv("LOGINOM_URL_BEST_TIME")

DB_PATH         = os.getenv("DB_PATH", "instacart_db.sqlite")
CACHE_DB        = os.getenv("CACHE_DB", "cache.db")
MISTRAL_MODEL   = "mistral-small-latest"
_mistral_client = Mistral(api_key=MISTRAL_API_KEY)

SYSTEM_PROMPT = (
    "Вы — персональный помощник покупателя интернет-магазина продуктов. "
    "Не начинайте ответ с приветствия — сразу переходите к сути. "
    "Отвечайте на русском языке, обращайтесь к покупателю на «вы». "
    "Ответ — 5–6 предложений: сначала охарактеризуйте предпочтения клиента, "
    "затем дайте 1–2 конкретных совета по новым товарам или категориям. "
    "Тон — тёплый и позитивный."
)

DOW_NAMES = {
    0: "Воскресенье", 1: "Понедельник", 2: "Вторник",
    3: "Среда", 4: "Четверг", 5: "Пятница", 6: "Суббота",
}

_GREETINGS = [
    "здравствуйте", "добрый день", "добрый вечер", "доброе утро",
    "привет", "уважаемый", "уважаемая", "рады приветствовать",
]

# ── Кэш (SQLite, персистентный, TTL) ─────────────────────────────────────────
CACHE_TTL_HISTORY   = 300    # 5 минут
CACHE_TTL_MISTRAL   = 600    # 10 минут
CACHE_TTL_DASHBOARD = 1800   # 30 минут


def _cache_conn():
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            expires_at REAL NOT NULL
        )
    """)
    conn.commit()
    return conn


def _cache_get(key: str):
    try:
        with _cache_conn() as conn:
            row = conn.execute(
                "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
        if row:
            value, expires_at = row
            if time.time() < expires_at:
                return json.loads(value), True
            # просрочено — удаляем
            with _cache_conn() as conn:
                conn.execute("DELETE FROM cache WHERE key = ?", (key,))
    except Exception:
        pass
    return None, False


def _cache_set(key: str, val, ttl: int):
    try:
        with _cache_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                (key, json.dumps(val, ensure_ascii=False), time.time() + ttl)
            )
    except Exception:
        pass


def cache_clear_user(user_id: int):
    keys = [f"history_{user_id}", f"mistral_{user_id}", f"recommend_{user_id}"]
    try:
        with _cache_conn() as conn:
            conn.executemany("DELETE FROM cache WHERE key = ?", [(k,) for k in keys])
    except Exception:
        pass


def cache_clear_dashboard():
    try:
        with _cache_conn() as conn:
            conn.executemany("DELETE FROM cache WHERE key = ?",
                             [("dashboard_all",), ("dashboard_summary",)])
    except Exception:
        pass


# ── Вспомогательные ──────────────────────────────────────────────────────────

def _loginom_get(url: str, user_id: int):
    if not url:
        raise RuntimeError("URL Loginom-сервиса не задан в .env")
    try:
        resp = requests.get(url, params={"user_id": user_id}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        raise RuntimeError(f"Ошибка запроса к Loginom: {e}") from e

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "DataSet" in data and isinstance(data["DataSet"], dict):
            rows = data["DataSet"].get("Rows", [])
            if isinstance(rows, list):
                return rows
        for key in ("data", "rows", "Rows", "result", "items"):
            if key in data and isinstance(data[key], list):
                return data[key]
        return data
    return data


def _strip_greeting(text: str) -> str:
    if not text:
        return text
    for sep in (". ", "! ", "?\n", ".\n"):
        idx = text.find(sep)
        if idx != -1:
            first = text[:idx + 1].lower()
            if any(g in first for g in _GREETINGS):
                return text[idx + len(sep):].lstrip()
            break
    # Проверяем первое слово без точки (короткое приветствие без знака)
    first_word = text.split()[0].lower().rstrip("!,") if text.split() else ""
    if first_word in ("привет", "здравствуйте"):
        rest = text[len(text.split()[0]):].lstrip(" ,!\n")
        return rest
    return text


def check_user_exists(user_id: int) -> bool:
    rows = _loginom_get(LOGINOM_URL_HISTORY, user_id)
    return bool(rows)


# ── История покупок ───────────────────────────────────────────────────────────

def get_user_history(user_id: int) -> tuple[list[dict], bool]:
    key = f"history_{user_id}"
    cached, hit = _cache_get(key)
    if hit:
        return cached, True
    rows = _loginom_get(LOGINOM_URL_HISTORY, user_id)
    result = rows[:10] if isinstance(rows, list) else []
    _cache_set(key, result, CACHE_TTL_HISTORY)
    return result, False


def build_products_text(rows: list[dict]) -> str:
    if not rows:
        return "История покупок пуста."
    lines = []
    for i, row in enumerate(rows, start=1):
        name  = row.get("product_name", "—")
        dept  = row.get("department_rus") or row.get("department", "—")
        aisle = row.get("aisle", "—")
        cnt   = row.get("order_count", "?")
        lines.append(f"{i}. {name} (отдел: {dept}, категория: {aisle}, заказов: {cnt})")
    return "\n".join(lines)


def ask_mistral(user_id: int, products_text: str) -> tuple[str, str, bool]:
    key = f"mistral_{user_id}"
    cached, hit = _cache_get(key)
    if hit:
        return cached[0], cached[1], True
    user_message = (
        f"Покупатель #{user_id} чаще всего заказывает следующие товары:\n\n"
        f"{products_text}\n\nДайте персональную рекомендацию этому покупателю."
    )
    resp = _mistral_client.chat.complete(
        model=MISTRAL_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
    )
    recommendation = _strip_greeting(resp.choices[0].message.content.strip())
    _cache_set(key, (user_message, recommendation), CACHE_TTL_MISTRAL)
    return user_message, recommendation, False


# ── Уровень клиента ───────────────────────────────────────────────────────────

def get_client_level(rows: list[dict]) -> dict:
    total  = sum(r.get("order_count", 0) for r in rows)
    unique = len(rows)
    avg    = round(total / unique, 1) if unique else 0

    if total < 20:
        level, emoji = "Новичок", "🌱"
        description  = "Вы только начинаете знакомство с нашим магазином"
    elif total < 60:
        level, emoji = "Постоянный", "⭐"
        description  = "Вы регулярно делаете покупки у нас"
    elif total < 120:
        level, emoji = "Лояльный", "🔥"
        description  = "Вы один из наших преданных покупателей"
    else:
        level, emoji = "VIP", "👑"
        description  = "Вы — наш самый ценный покупатель"

    return {
        "level":        level,
        "emoji":        emoji,
        "total_orders": total,
        "avg_frequency": avg,
        "description":  description,
    }


# ── Рекомендации «Вас может заинтересовать» ──────────────────────────────────

def get_recommendations(user_id: int, rows: list[dict]) -> list[dict]:
    key = f"recommend_{user_id}"
    cached, hit = _cache_get(key)
    if hit:
        return cached

    # Топ-3 категории по количеству заказов
    aisle_counts: dict = defaultdict(int)
    for r in rows:
        aisle_counts[r.get("aisle", "")] += r.get("order_count", 0)
    top_aisles = sorted(aisle_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    categories = ", ".join(a for a, _ in top_aisles if a)

    # Mistral рекомендует по-русски и переводит на английский для поиска в БД
    user_message = (
        f"Покупатель регулярно берёт товары из категорий: {categories}. "
        "Порекомендуй ровно 3 товара, которые ему понравятся. "
        "Отвечай строго в формате нумерованного списка:\n"
        "1. Русское название / English keyword\n"
        "2. Русское название / English keyword\n"
        "3. Русское название / English keyword\n"
        "Только названия, без объяснений. English keyword — одно-два слова для поиска в базе."
    )
    resp = _mistral_client.chat.complete(
        model=MISTRAL_MODEL,
        messages=[{"role": "user", "content": user_message}],
    )
    text = resp.choices[0].message.content.strip()

    # Парсим «Русское / English» из каждой строки
    suggested = []  # [(ru, en), ...]
    for line in text.split("\n"):
        line = line.strip()
        if line and line[0].isdigit():
            pair = line.split(".", 1)[-1].strip()
            if "/" in pair:
                ru, en = pair.split("/", 1)
                suggested.append((ru.strip(), en.strip()))
            else:
                suggested.append((pair.strip(), pair.strip()))
    suggested = suggested[:3]

    # Ищем реальные товары в базе продуктов (SQLite) по английскому ключевому слову
    found = []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        for ru_name, en_name in suggested:
            words = en_name.split()
            db_row = None
            for attempt in [" ".join(words[:2]), words[0]]:
                cur = conn.execute(
                    "SELECT product_name, aisle, department FROM products "
                    "WHERE product_name LIKE ? LIMIT 1",
                    (f"%{attempt}%",)
                )
                db_row = cur.fetchone()
                if db_row:
                    break
            found.append({
                "suggested":    ru_name,                                      # русское слово для показа
                "product_name": db_row["product_name"] if db_row else None,  # реальный товар из БД
                "aisle":        db_row["aisle"]        if db_row else "—",
                "department":   db_row["department"]   if db_row else "—",
            })
        conn.close()
    except Exception:
        found = [{"suggested": ru, "product_name": None, "aisle": "—", "department": "—"}
                 for ru, _ in suggested]

    result = found[:3]
    _cache_set(key, result, CACHE_TTL_MISTRAL)
    return result


# ── Сервис 1: Ритм покупок ────────────────────────────────────────────────────

def get_purchase_rhythm(user_id: int) -> dict:
    data = _loginom_get(LOGINOM_URL_RHYTHM, user_id)
    if isinstance(data, list) and data:
        return data[0] if isinstance(data[0], dict) else {}
    if isinstance(data, dict):
        return data
    return {}


def ask_mistral_rhythm(user_id: int, rhythm: dict) -> str:
    avg = rhythm.get("avg_days", "?")
    mn  = rhythm.get("min_days", "?")
    mx  = rhythm.get("max_days", "?")
    tot = rhythm.get("total_orders", "?")
    user_message = (
        f"Покупатель #{user_id} совершил {tot} заказов. "
        f"Среднее время между заказами: {avg} дней (минимум {mn}, максимум {mx} дней). "
        "Напишите дружелюбное сообщение: когда стоит сделать следующий заказ. "
        "Не начинайте с приветствия. На «вы», 3–4 предложения."
    )
    resp = _mistral_client.chat.complete(
        model=MISTRAL_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
    )
    return _strip_greeting(resp.choices[0].message.content.strip())


# ── Сервис 2: Забытые товары ──────────────────────────────────────────────────

def get_forgotten_products(user_id: int) -> list[dict]:
    rows = _loginom_get(LOGINOM_URL_FORGOTTEN, user_id)
    return rows if isinstance(rows, list) else []


def ask_mistral_forgotten(user_id: int, products: list[dict]) -> str:
    if not products:
        return "Забытых товаров не найдено — вы заказываете всё стабильно!"
    items = "\n".join(
        f"- {r.get('product_name', '—')} (категория: {r.get('aisle', '—')}, "
        f"куплено раз: {r.get('total_bought', '?')})"
        for r in products
    )
    user_message = (
        f"Покупатель #{user_id} регулярно покупал эти товары, но не заказывал их в последних заказах:\n\n"
        f"{items}\n\nНапишите тёплое напоминание. "
        "Не начинайте с приветствия. На «вы», 3–4 предложения."
    )
    resp = _mistral_client.chat.complete(
        model=MISTRAL_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
    )
    return _strip_greeting(resp.choices[0].message.content.strip())


# ── Сервис 3: Лучшее время ───────────────────────────────────────────────────

def get_best_order_time(user_id: int) -> dict:
    rows = _loginom_get(LOGINOM_URL_BEST_TIME, user_id)
    if isinstance(rows, list) and rows:
        best = max(rows, key=lambda r: r.get("cnt", 0))
        return {
            "best_dow":      best.get("order_dow"),
            "best_dow_name": DOW_NAMES.get(best.get("order_dow"), str(best.get("order_dow"))),
            "best_hour":     best.get("order_hour_of_day"),
            "best_cnt":      best.get("cnt"),
            "heatmap":       rows,
        }
    return {}


def ask_mistral_best_time(user_id: int, time_data: dict) -> str:
    dow_name = time_data.get("best_dow_name", "?")
    hour     = time_data.get("best_hour", "?")
    cnt      = time_data.get("best_cnt", "?")
    user_message = (
        f"Покупатель #{user_id} чаще всего делает заказы в {dow_name} около {hour}:00 "
        f"(так было {cnt} раз). "
        "Напишите короткое дружелюбное сообщение о привычном времени заказов. "
        "Не начинайте с приветствия. На «вы», 3–4 предложения."
    )
    resp = _mistral_client.chat.complete(
        model=MISTRAL_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
    )
    return _strip_greeting(resp.choices[0].message.content.strip())


# ── Дашборд по всем пользователям ────────────────────────────────────────────

def _dashboard_db():
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(
            f"Основная БД instacart_db.sqlite не найдена по пути «{DB_PATH}». "
            "Убедитесь, что файл загружен на сервер."
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_dashboard_stats() -> dict:
    cached, hit = _cache_get("dashboard_all")
    if hit:
        return {**cached, "from_cache": True}

    result: dict = {
        "top_products":         [],
        "level_distribution":   [],
        "order_gap":            {},
        "forgotten_categories": [],
        "meta":                 {},
        "errors":               [],
        "from_cache":           False,
    }

    try:
        conn = _dashboard_db()
    except FileNotFoundError as e:
        result["errors"].append(str(e))
        return result

    # 1. Топ-10 товаров по всем клиентам
    try:
        rows = conn.execute("""
            SELECT p.product_name,
                   COALESCE(p.department_rus, p.department) AS dept,
                   sub.cnt
            FROM (
                SELECT product_id, COUNT(*) AS cnt
                FROM orders_prior
                GROUP BY product_id
                ORDER BY cnt DESC LIMIT 10
            ) sub
            JOIN products p ON sub.product_id = p.product_id
            ORDER BY sub.cnt DESC
        """).fetchall()
        result["top_products"] = [dict(r) for r in rows]
    except Exception as e:
        result["errors"].append(f"Топ товаров: {e}")

    # 2. Распределение клиентов по уровням (прокси через MAX(order_number))
    try:
        row = conn.execute("""
            SELECT
                SUM(CASE WHEN orders_cnt < 20  THEN 1 ELSE 0 END) AS newbie,
                SUM(CASE WHEN orders_cnt >= 20 AND orders_cnt < 60  THEN 1 ELSE 0 END) AS regular,
                SUM(CASE WHEN orders_cnt >= 60 AND orders_cnt < 120 THEN 1 ELSE 0 END) AS loyal,
                SUM(CASE WHEN orders_cnt >= 120 THEN 1 ELSE 0 END) AS vip,
                COUNT(*) AS total_users
            FROM (
                SELECT user_id, MAX(order_number) AS orders_cnt
                FROM orders
                GROUP BY user_id
            )
        """).fetchone()
        if row:
            r = dict(row)
            total = r.get("total_users", 1) or 1
            result["level_distribution"] = [
                {"level": "Новичок",    "emoji": "🌱", "count": r.get("newbie",  0),
                 "pct": round(r.get("newbie",  0) / total * 100, 1)},
                {"level": "Постоянный", "emoji": "⭐", "count": r.get("regular", 0),
                 "pct": round(r.get("regular", 0) / total * 100, 1)},
                {"level": "Лояльный",   "emoji": "🔥", "count": r.get("loyal",   0),
                 "pct": round(r.get("loyal",   0) / total * 100, 1)},
                {"level": "VIP",        "emoji": "👑", "count": r.get("vip",     0),
                 "pct": round(r.get("vip",     0) / total * 100, 1)},
            ]
            result["meta"]["total_users"] = total
    except Exception as e:
        result["errors"].append(f"Уровни клиентов: {e}")

    # 3. Средний интервал между заказами
    try:
        row = conn.execute("""
            SELECT ROUND(AVG(days_since_prior_order), 1) AS avg_days,
                   MIN(days_since_prior_order)            AS min_days,
                   MAX(days_since_prior_order)            AS max_days
            FROM orders
            WHERE days_since_prior_order IS NOT NULL
        """).fetchone()
        if row:
            result["order_gap"] = dict(row)
    except Exception as e:
        result["errors"].append(f"Интервал заказов: {e}")

    # 4. Категории с высокой долей повторных покупок (оценочно)
    # Subquery aggregates by product_id first (uses covering index), then JOINs to get aisle
    try:
        rows = conn.execute("""
            SELECT p.aisle,
                   SUM(sub.total)        AS total_orders,
                   ROUND(SUM(sub.reordered_sum) * 100.0 / SUM(sub.total), 1) AS reorder_pct
            FROM (
                SELECT product_id, COUNT(*) AS total, SUM(reordered) AS reordered_sum
                FROM orders_prior
                GROUP BY product_id
            ) sub
            JOIN products p ON sub.product_id = p.product_id
            GROUP BY p.aisle
            HAVING total_orders > 1000
            ORDER BY reorder_pct DESC, total_orders DESC
            LIMIT 10
        """).fetchall()
        result["forgotten_categories"] = [dict(r) for r in rows]
    except Exception as e:
        result["errors"].append(f"Забываемые категории: {e}")

    conn.close()
    result["meta"]["generated_at"] = time.strftime("%d.%m.%Y %H:%M")
    _cache_set("dashboard_all",
               {k: v for k, v in result.items() if k != "from_cache"},
               CACHE_TTL_DASHBOARD)
    return result


def ask_mistral_dashboard(stats: dict) -> str:
    cached, hit = _cache_get("dashboard_summary")
    if hit:
        return cached

    top     = stats.get("top_products", [])[:5]
    top_str = ", ".join(r.get("product_name", "?") for r in top)
    levels  = {r["level"]: r["pct"] for r in stats.get("level_distribution", [])}
    gap     = stats.get("order_gap", {})
    total   = stats.get("meta", {}).get("total_users", "?")

    user_message = (
        f"Аналитическая сводка по всей базе покупателей Instacart.\n\n"
        f"Всего покупателей: {total}.\n"
        f"Топ-5 популярных товаров: {top_str}.\n"
        f"Распределение по уровням лояльности: "
        f"Новичок — {levels.get('Новичок', '?')}%, "
        f"Постоянный — {levels.get('Постоянный', '?')}%, "
        f"Лояльный — {levels.get('Лояльный', '?')}%, "
        f"VIP — {levels.get('VIP', '?')}%.\n"
        f"Среднее время между заказами: {gap.get('avg_days', '?')} дней "
        f"(мин {gap.get('min_days', '?')}, макс {gap.get('max_days', '?')}).\n\n"
        "Напиши 4–6 предложений деловых выводов на русском языке. "
        "Опирайся только на переданные цифры, ничего не придумывай. "
        "Не начинай с приветствия. Стиль — аналитический, нейтральный."
    )
    resp = _mistral_client.chat.complete(
        model=MISTRAL_MODEL,
        messages=[
            {"role": "system", "content": "Ты — бизнес-аналитик. Давай краткие точные выводы на основе данных."},
            {"role": "user",   "content": user_message},
        ],
    )
    summary = _strip_greeting(resp.choices[0].message.content.strip())
    _cache_set("dashboard_summary", summary, CACHE_TTL_DASHBOARD)
    return summary
