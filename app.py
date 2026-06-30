import os
from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-in-production")


@app.route("/")
def index():
    user_id = session.get("user_id")
    return render_template("index.html", user_id=user_id)


@app.route("/login", methods=["POST"])
def login():
    raw = request.form.get("user_id", "").strip()

    # Валидация формата
    if not raw.isdigit():
        return render_template(
            "index.html",
            user_id=None,
            error="user_id должен быть целым положительным числом.",
        )

    user_id = int(raw)

    # Проверка существования пользователя через Loginom
    try:
        from business_rules import check_user_exists
        exists = check_user_exists(user_id)
    except RuntimeError as e:
        return render_template(
            "index.html",
            user_id=None,
            error=f"Ошибка проверки пользователя: {e}",
        )

    if not exists:
        return render_template(
            "index.html",
            user_id=None,
            error=f"Пользователь с ID {user_id} не найден в базе данных.",
        )

    session["user_id"] = user_id
    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/history", methods=["POST"])
def history():
    user_id = session.get("user_id")
    if user_id is None:
        return render_template(
            "index.html",
            user_id=None,
            error="Сначала авторизуйтесь.",
        )

    try:
        from business_rules import get_user_history, build_products_text, ask_mistral
        rows = get_user_history(user_id)
        products_text = build_products_text(rows)
        _, recommendation = ask_mistral(user_id, products_text)
    except RuntimeError as e:
        return render_template(
            "index.html",
            user_id=user_id,
            error=str(e),
        )
    except Exception as e:
        return render_template(
            "index.html",
            user_id=user_id,
            error=f"Непредвиденная ошибка: {e}",
        )

    return render_template(
        "index.html",
        user_id=user_id,
        products=rows[:10],
        recommendation=recommendation,
    )


@app.route("/api/history", methods=["POST"])
def api_history():
    user_id = session.get("user_id")
    if user_id is None:
        return jsonify({"error": "Не авторизован"}), 401

    try:
        from business_rules import get_user_history, build_products_text, ask_mistral
        rows = get_user_history(user_id)
        products_text = build_products_text(rows)
        prompt_text, recommendation = ask_mistral(user_id, products_text)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        return jsonify({"error": f"Непредвиденная ошибка: {e}"}), 500

    return jsonify({
        "user_id": user_id,
        "products": rows[:10],
        "prompt": prompt_text,
        "recommendation": recommendation,
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
