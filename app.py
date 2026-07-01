import os
from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-in-production")


def require_auth():
    """Возвращает None если авторизован, иначе redirect."""
    if not session.get("user_id"):
        return redirect(url_for("index"))
    return None


# ── Главная ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login", methods=["POST"])
def login():
    raw = request.form.get("user_id", "").strip()
    if not raw.isdigit():
        return render_template("index.html",
                               error="user_id должен быть целым положительным числом.")
    user_id = int(raw)
    try:
        from business_rules import check_user_exists
        if not check_user_exists(user_id):
            return render_template("index.html",
                                   error=f"Пользователь с ID {user_id} не найден.")
    except Exception as e:
        return render_template("index.html", error=str(e))

    session["user_id"] = user_id
    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("index"))


# ── /history — Мои покупки ────────────────────────────────────────────────────

@app.route("/history", methods=["GET", "POST"])
def history():
    redir = require_auth()
    if redir:
        return redir

    user_id = session["user_id"]
    products = recommendation = None

    if request.method == "POST":
        try:
            from business_rules import get_user_history, build_products_text, ask_mistral
            products = get_user_history(user_id)
            _, recommendation = ask_mistral(user_id, build_products_text(products))
        except Exception as e:
            return render_template("history.html", error=str(e))

    return render_template("history.html", products=products, recommendation=recommendation)


# ── /insights — Аналитика ─────────────────────────────────────────────────────

@app.route("/insights", methods=["GET", "POST"])
def insights():
    redir = require_auth()
    if redir:
        return redir

    user_id = session["user_id"]
    rhythm = msg_rhythm = None
    forgotten = msg_forgotten = None
    best_time = msg_best_time = None

    if request.method == "POST":
        try:
            from business_rules import (
                get_purchase_rhythm, ask_mistral_rhythm,
                get_forgotten_products, ask_mistral_forgotten,
                get_best_order_time, ask_mistral_best_time,
            )
            rhythm      = get_purchase_rhythm(user_id)
            msg_rhythm  = ask_mistral_rhythm(user_id, rhythm)

            forgotten     = get_forgotten_products(user_id)
            msg_forgotten = ask_mistral_forgotten(user_id, forgotten)

            best_time     = get_best_order_time(user_id)
            msg_best_time = ask_mistral_best_time(user_id, best_time)

        except Exception as e:
            return render_template("insights.html", error=str(e))

    return render_template("insights.html",
                           rhythm=rhythm, msg_rhythm=msg_rhythm,
                           forgotten=forgotten, msg_forgotten=msg_forgotten,
                           best_time=best_time, msg_best_time=msg_best_time)


# ── JSON API ──────────────────────────────────────────────────────────────────

@app.route("/api/history", methods=["POST"])
def api_history():
    if not session.get("user_id"):
        return jsonify({"error": "Не авторизован"}), 401
    try:
        from business_rules import get_user_history, build_products_text, ask_mistral
        user_id = session["user_id"]
        rows = get_user_history(user_id)
        prompt, recommendation = ask_mistral(user_id, build_products_text(rows))
        return jsonify({"user_id": user_id, "products": rows,
                        "prompt": prompt, "recommendation": recommendation})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
