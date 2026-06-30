import os
import requests
from dotenv import load_dotenv
from mistralai import Mistral

load_dotenv()

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
if not MISTRAL_API_KEY:
    raise EnvironmentError(
        "Переменная окружения MISTRAL_API_KEY не задана. "
        "Создайте файл .env на основе .env.example и укажите ключ."
    )

LOGINOM_URL = os.getenv("LOGINOM_URL", "https://<ваш-сервер-loginom>/lgi/rest/<имя_пакета>")

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


def get_user_history(user_id: int) -> list[dict]:
    """Запрашивает историю покупок пользователя из веб-сервиса Loginom."""
    url = f"{LOGINOM_URL}/GetUserHistory"
    try:
        response = requests.post(
            url,
            json={"user_id": user_id},
            timeout=15,
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise RuntimeError(f"Не удалось подключиться к Loginom по адресу: {url}")
    except requests.exceptions.Timeout:
        raise RuntimeError("Превышено время ожидания ответа от Loginom.")
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"Loginom вернул ошибку: {e.response.status_code} {e.response.text}")

    data = response.json()
    rows = data.get("DataSet", {}).get("Rows", [])
    return rows


def check_user_exists(user_id: int) -> bool:
    """Проверяет существование пользователя через веб-сервис Loginom CheckUserId."""
    url = f"{LOGINOM_URL}/CheckUserId"
    try:
        response = requests.post(
            url,
            json={"user_id": user_id},
            timeout=10,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Ошибка при проверке пользователя в Loginom: {e}")

    data = response.json()
    # Ожидаем поле exists (bool) или rows с результатом
    rows = data.get("DataSet", {}).get("Rows", [])
    if rows:
        first = rows[0]
        # Loginom может вернуть поле с разными именами — проверяем оба варианта
        return bool(first.get("exists") or first.get("user_exists") or first.get("count", 0))
    return bool(data.get("exists", False))


def build_products_text(rows: list[dict]) -> str:
    """Форматирует список товаров в нумерованный текст для промпта."""
    if not rows:
        return "История покупок пуста."

    lines = []
    for i, row in enumerate(rows[:10], start=1):
        name = row.get("product_name", "—")
        department = row.get("department", "—")
        aisle = row.get("aisle", "—")
        orders = row.get("order_count", row.get("orders_count", "?"))
        lines.append(f"{i}. {name} (отдел: {department}, категория: {aisle}, заказов: {orders})")

    return "\n".join(lines)


def ask_mistral(user_id: int, products_text: str) -> tuple[str, str]:
    """
    Отправляет запрос в Mistral и возвращает (prompt_text, recommendation).
    """
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
