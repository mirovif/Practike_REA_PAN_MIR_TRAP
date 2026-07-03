import os
import requests
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

MISTRAL_MODEL   = "mistral-small-latest"
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


# ── Loginom REST ──────────────────────────────────────────────────────────────

def _loginom_get(url: str, user_id: int) -> list | dict:
    """GET-запрос к Loginom REST, возвращает распарсенный JSON."""
    if not url:
        raise RuntimeError("URL Loginom-сервиса не задан в .env")
    try:
        resp = requests.get(url, params={"user_id": user_id}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        raise RuntimeError(f"Ошибка запроса к Loginom: {e}") from e

    # Loginom может вернуть список или {"data": [...]}
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "rows", "result", "items"):
            if key in data and isinstance(data[key], list):
                return data[key]
        return data
    return data


# ── Проверка пользователя ─────────────────────────────────────────────────────

def check_user_exists(user_id: int) -> bool:
    rows = _loginom_get(LOGINOM_URL_HISTORY, user_id)
    return bool(rows)


# ── История покупок ───────────────────────────────────────────────────────────

def get_user_history(user_id: int) -> list[dict]:
    rows = _loginom_get(LOGINOM_URL_HISTORY, user_id)
    if isinstance(rows, list):
        return rows[:10]
    return []


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

def get_forgotten_products(user_id: int) -> list[dict]:
    rows = _loginom_get(LOGINOM_URL_FORGOTTEN, user_id)
    if isinstance(rows, list):
        return rows
    return []


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
