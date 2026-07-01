import os
import sqlite3
from dotenv import load_dotenv
from mistralai.client import Mistral

load_dotenv()

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
if not MISTRAL_API_KEY:
    raise EnvironmentError(
        "Переменная окружения MISTRAL_API_KEY не задана. "
        "Создайте файл .env на основе .env.example и укажите ключ."
    )

DB_PATH = os.getenv("DB_PATH", "instacart_db.sqlite")

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


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def check_user_exists(user_id: int) -> bool:
    """Проверяет наличие пользователя в таблице orders."""
    with _get_conn() as conn:
        cur = conn.execute(
            "SELECT 1 FROM orders WHERE user_id = ? LIMIT 1", (user_id,)
        )
        return cur.fetchone() is not None


def get_user_history(user_id: int) -> list[dict]:
    """
    Возвращает топ-10 товаров пользователя по частоте заказов.
    Объединяет orders → orders_prior → products.
    """
    query = """
        SELECT
            p.product_name,
            p.aisle,
            p.department,
            p.department_rus,
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
        cur = conn.execute(query, (user_id,))
        rows = [dict(r) for r in cur.fetchall()]
    return rows


def build_products_text(rows: list[dict]) -> str:
    """Форматирует список товаров в нумерованный текст для промпта."""
    if not rows:
        return "История покупок пуста."

    lines = []
    for i, row in enumerate(rows, start=1):
        name = row.get("product_name", "—")
        dept = row.get("department_rus") or row.get("department", "—")
        aisle = row.get("aisle", "—")
        orders = row.get("order_count", "?")
        lines.append(f"{i}. {name} (отдел: {dept}, категория: {aisle}, заказов: {orders})")

    return "\n".join(lines)


def ask_mistral(user_id: int, products_text: str) -> tuple[str, str]:
    """Отправляет запрос в Mistral, возвращает (prompt_text, recommendation)."""
    user_message = (
        f"Покупатель #{user_id} чаще всего заказывает следующие товары:\n\n"
        f"{products_text}\n\n"
        "Пожалуйста, дайте персональную рекомендацию этому покупателю."
    )

    response = _mistral_client.chat.complete(
        model=MISTRAL_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )

    recommendation = response.choices[0].message.content.strip()
    return user_message, recommendation
