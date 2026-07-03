import os
import sqlite3
import requests
from dotenv import load_dotenv
from mistralai.client import Mistral

load_dotenv()

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
if not MISTRAL_API_KEY:
    raise EnvironmentError("Переменная окружения MISTRAL_API_KEY не задана.")

# Loginom REST endpoints (если не заданы — fallback на SQLite)
LOGINOM_URL_HISTORY   = os.getenv("LOGINOM_URL_HISTORY")
LOGINOM_URL_RHYTHM    = os.getenv("LOGINOM_URL_RHYTHM")
LOGINOM_URL_FORGOTTEN = os.getenv("LOGINOM_URL_FORGOTTEN")
LOGINOM_URL_BEST_TIME = os.getenv("LOGINOM_URL_BEST_TIME")

DB_PATH       = os.getenv("DB_PATH", "instacart_db.sqlite")
MISTRAL_MODEL = "mistral-small-latest"
_mistral_client = Mistral(api_key=MISTRAL_API_KEY)

SYSTEM_PROMPT = (
    "Вы — персональный помощник покупателя интернет-магазина продуктов. "
    "Ваша задача — анализировать историю покупок клиента и давать дружелюбные, "
    "полезные рекомендации. Отвечайте на русском языке, обращайтесь к покупателю "
    "на «вы». Ответ должен занимать 5–6 предложений: сначала кратко охарактеризуйте "
    "предпочтения клиента, затем дайте 1–2 конкретных совета по новым товарам или "
    "категориям, которые могут ему понравиться. Тон — тёплый и позитивный."
)

DOW_NAMES = {
    0: "Воскресенье", 1: "Понедельник", 2: "Вторник",
    3: "Среда", 4: "Четверг", 5: "Пятница", 6: "Суббота",
}


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _loginom_get(url: str, user_id: int) -> list[dict] | dict | None:
    """GET-запрос к Loginom REST сервису, возвращает распарсенный JSON."""
    try:
        resp = requests.get(url, params={"user_id": user_id}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        # Loginom может вернуть {"data": [...]} или сразу список
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # пробуем стандартные ключи-обёртки
            for key in ("data", "rows", "result", "items"):
                if key in data and isinstance(data[key], list):
                    return data[key]
            return data
        return data
    except Exception as e:
        raise RuntimeError(f"Ошибка запроса к Loginom ({url}): {e}") from e


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def check_user_exists(user_id: int) -> bool:
    """Проверяет существование пользователя — сначала Loginom, иначе SQLite."""
    if LOGINOM_URL_HISTORY:
        try:
            result = _loginom_get(LOGINOM_URL_HISTORY, user_id)
            return bool(result)
        except Exception:
            pass  # если Loginom недоступен — проверяем через SQLite
    with _get_conn() as conn:
        cur = conn.execute("SELECT 1 FROM orders WHERE user_id = ? LIMIT 1", (user_id,))
        return cur.fetchone() is not None


# ── История покупок (Сервис 1) ────────────────────────────────────────────────

def get_user_history(user_id: int) -> list[dict]:
    if LOGINOM_URL_HISTORY:
        rows = _loginom_get(LOGINOM_URL_HISTORY, user_id)
        if isinstance(rows, list):
            return rows[:10]

    # fallback: SQLite
    query = """
        SELECT p.product_name, p.aisle, p.department, p.department_rus,
               COUNT(*) AS order_count
        FROM orders o
        JOIN orders_prior op ON o.order_id = op.order_id
        JOIN products p      ON op.product_id = p.product_id
        WHERE o.user_id = ?
        GROUP BY p.product_id
        ORDER BY order_count DESC
        LIMIT 10
    """
    with _get_conn() as conn:
        return [dict(r) for r in conn.execute(query, (user_id,)).fetchall()]


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


def ask_mistral(user_id: int, products_text: str) -> tuple[str, str]:
    user_message = (
        f"Покупатель #{user_id} чаще всего заказывает следующие товары:\n\n"
        f"{products_text}\n\n"
        "Пожалуйста, дайте персональную рекомендацию этому покупателю."
    )
    resp = _mistral_client.chat.complete(
        model=MISTRAL_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
    )
    return user_message, resp.choices[0].message.content.strip()


# ── Сервис 1: Ритм покупок ────────────────────────────────────────────────────

def get_purchase_rhythm(user_id: int) -> dict:
    if LOGINOM_URL_RHYTHM:
        data = _loginom_get(LOGINOM_URL_RHYTHM, user_id)
        # Loginom может вернуть список с одной строкой или словарь
        if isinstance(data, list) and data:
            return data[0] if isinstance(data[0], dict) else {}
        if isinstance(data, dict):
            return data

    # fallback: SQLite
    query = """
        SELECT ROUND(AVG(days_since_prior_order), 1) AS avg_days,
               MIN(days_since_prior_order)            AS min_days,
               MAX(days_since_prior_order)            AS max_days,
               COUNT(*)                               AS total_orders
        FROM orders
        WHERE user_id = ? AND eval_set = 'prior'
          AND days_since_prior_order IS NOT NULL
    """
    with _get_conn() as conn:
        row = conn.execute(query, (user_id,)).fetchone()
        return dict(row) if row else {}


def ask_mistral_rhythm(user_id: int, rhythm: dict) -> str:
    avg = rhythm.get("avg_days", "?")
    mn  = rhythm.get("min_days", "?")
    mx  = rhythm.get("max_days", "?")
    tot = rhythm.get("total_orders", "?")
    user_message = (
        f"Покупатель #{user_id} совершил {tot} заказов. "
        f"Среднее время между заказами: {avg} дней (минимум {mn}, максимум {mx} дней). "
        "Напишите покупателю дружелюбное сообщение: когда ему стоит сделать следующий заказ, "
        "основываясь на его привычном ритме. Обращайтесь на «вы», 3–4 предложения."
    )
    resp = _mistral_client.chat.complete(
        model=MISTRAL_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
    )
    return resp.choices[0].message.content.strip()


# ── Сервис 2: Забытые товары ──────────────────────────────────────────────────

def get_forgotten_products(user_id: int, last_n: int = 3) -> list[dict]:
    if LOGINOM_URL_FORGOTTEN:
        rows = _loginom_get(LOGINOM_URL_FORGOTTEN, user_id)
        if isinstance(rows, list):
            return rows

    # fallback: SQLite
    with _get_conn() as conn:
        recent_ids = [
            r[0] for r in conn.execute(
                "SELECT order_id FROM orders WHERE user_id = ? "
                "ORDER BY order_number DESC LIMIT ?",
                (user_id, last_n),
            ).fetchall()
        ]
        if not recent_ids:
            return []
        placeholders = ",".join("?" * len(recent_ids))
        query = f"""
            SELECT p.product_name, p.aisle,
                   p.department_rus AS department,
                   COUNT(*) AS total_bought
            FROM orders o
            JOIN orders_prior op ON o.order_id = op.order_id
            JOIN products p      ON op.product_id = p.product_id
            WHERE o.user_id = ?
              AND op.product_id NOT IN (
                  SELECT product_id FROM orders_prior
                  WHERE order_id IN ({placeholders})
              )
            GROUP BY op.product_id
            HAVING total_bought >= 3
            ORDER BY total_bought DESC
            LIMIT 10
        """
        return [dict(r) for r in conn.execute(query, (user_id, *recent_ids)).fetchall()]


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
        f"{items}\n\n"
        "Напишите тёплое напоминание — возможно, покупатель просто забыл добавить эти товары. "
        "Обращайтесь на «вы», 3–4 предложения."
    )
    resp = _mistral_client.chat.complete(
        model=MISTRAL_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
    )
    return resp.choices[0].message.content.strip()


# ── Сервис 3: Лучшее время для заказа ────────────────────────────────────────

def get_best_order_time(user_id: int) -> dict:
    if LOGINOM_URL_BEST_TIME:
        rows = _loginom_get(LOGINOM_URL_BEST_TIME, user_id)
        if isinstance(rows, list) and rows:
            # Loginom возвращает тепловую карту — берём строку с max cnt
            best = max(rows, key=lambda r: r.get("cnt", 0))
            return {
                "best_dow":      best.get("order_dow"),
                "best_dow_name": DOW_NAMES.get(best.get("order_dow"), str(best.get("order_dow"))),
                "best_hour":     best.get("order_hour_of_day"),
                "best_cnt":      best.get("cnt"),
                "heatmap":       rows,
            }

    # fallback: SQLite
    query = """
        SELECT order_dow, order_hour_of_day, COUNT(*) AS cnt
        FROM orders
        WHERE user_id = ? AND eval_set = 'prior'
        GROUP BY order_dow, order_hour_of_day
        ORDER BY cnt DESC
        LIMIT 1
    """
    heatmap_query = """
        SELECT order_dow, order_hour_of_day, COUNT(*) AS cnt
        FROM orders
        WHERE user_id = ? AND eval_set = 'prior'
        GROUP BY order_dow, order_hour_of_day
        ORDER BY order_dow, order_hour_of_day
    """
    with _get_conn() as conn:
        top = conn.execute(query, (user_id,)).fetchone()
        heatmap = [dict(r) for r in conn.execute(heatmap_query, (user_id,)).fetchall()]
    if top:
        return {
            "best_dow":      top["order_dow"],
            "best_dow_name": DOW_NAMES.get(top["order_dow"], str(top["order_dow"])),
            "best_hour":     top["order_hour_of_day"],
            "best_cnt":      top["cnt"],
            "heatmap":       heatmap,
        }
    return {}


def ask_mistral_best_time(user_id: int, time_data: dict) -> str:
    dow_name = time_data.get("best_dow_name", "?")
    hour     = time_data.get("best_hour", "?")
    cnt      = time_data.get("best_cnt", "?")
    user_message = (
        f"Покупатель #{user_id} чаще всего делает заказы в {dow_name} около {hour}:00 "
        f"(так было {cnt} раз). "
        "Напишите короткое дружелюбное сообщение, сообщая покупателю о его привычном "
        "времени заказов и предложите удобный момент для следующего заказа. "
        "Обращайтесь на «вы», 3–4 предложения."
    )
    resp = _mistral_client.chat.complete(
        model=MISTRAL_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
    )
    return resp.choices[0].message.content.strip()
