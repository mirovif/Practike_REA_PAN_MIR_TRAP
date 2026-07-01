import os
import requests
import pandas as pd
from dotenv import load_dotenv
from mistralai.client import Mistral

load_dotenv()

LOGINOM_BASE_URL = "https://edu.loginom.dev"
LOGINOM_PACKAGE = "instacart_ws_Panichev"
method = "GetUserHistory"
url = f"{LOGINOM_BASE_URL}/lgi/rest/{LOGINOM_PACKAGE}/{method}"

USER_ID = int(input("Введите ID пользователя: "))

payload = {
    "Variables": {
        "user_id": USER_ID
    }
}

response = requests.post(url, json=payload, timeout=30)
response.raise_for_status()

rows = response.json()["DataSet"]["Rows"]
df = pd.DataFrame(rows)
print(df.head(10))

# Формируем текст товаров для промпта
lines = []
for i, row in enumerate(rows[:10], start=1):
    name = row.get("product_name", "—")
    dept = row.get("department_rus") or row.get("department", "—")
    aisle = row.get("aisle", "—")
    orders = row.get("order_count", "?")
    lines.append(f"{i}. {name} (отдел: {dept}, категория: {aisle}, заказов: {orders})")

products_text = "\n".join(lines)

system_prompt = (
    "Вы — персональный помощник покупателя интернет-магазина продуктов. "
    "Ваша задача — анализировать историю покупок клиента и давать дружелюбные, "
    "полезные рекомендации. Отвечайте на русском языке, обращайтесь к покупателю "
    "на «вы». Ответ должен занимать 5–6 предложений: сначала кратко охарактеризуйте "
    "предпочтения клиента, затем дайте 1–2 конкретных совета по новым товарам или "
    "категориям, которые могут ему понравиться. Тон — тёплый и позитивный."
)

user_message = (
    f"Покупатель #{USER_ID} чаще всего заказывает следующие товары:\n\n"
    f"{products_text}\n\n"
    "Пожалуйста, дайте персональную рекомендацию этому покупателю."
)

print("\n=== Промпт, отправляемый в Mistral ===")
print(user_message)

client = Mistral(api_key=os.getenv("MISTRAL_API_KEY"))
resp = client.chat.complete(
    model="mistral-small-latest",
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ],
)

recommendation = resp.choices[0].message.content.strip()
print("\n=== Персональная рекомендация ===")
print(recommendation)
