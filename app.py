import os
from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-in-production")


def require_auth():
    if not session.get("user_id"):
        return redirect(url_for("index"))
    return None


# ── Главная ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", client_level=session.get("client_level"))


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
    products = recommendation = client_level = None
    from_cache = False

    if request.method == "POST":
        try:
            from business_rules import (
                get_user_history, build_products_text,
                ask_mistral, get_client_level,
            )
            products, from_cache_h   = get_user_history(user_id)
            _, recommendation, from_cache_m = ask_mistral(
                user_id, build_products_text(products)
            )
            from_cache   = from_cache_h and from_cache_m
            client_level = get_client_level(products)
            session["client_level"] = client_level
        except Exception as e:
            return render_template("history.html", error=str(e))

    return render_template(
        "history.html",
        products=products,
        recommendation=recommendation,
        client_level=client_level,
        from_cache=from_cache,
    )


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

    return render_template(
        "insights.html",
        rhythm=rhythm, msg_rhythm=msg_rhythm,
        forgotten=forgotten, msg_forgotten=msg_forgotten,
        best_time=best_time, msg_best_time=msg_best_time,
    )


# ── /recommend — Вас может заинтересовать ────────────────────────────────────

@app.route("/recommend", methods=["POST"])
def recommend():
    redir = require_auth()
    if redir:
        return jsonify({"error": "Не авторизован"}), 401
    user_id = session["user_id"]
    try:
        from business_rules import get_user_history, get_recommendations
        products, _ = get_user_history(user_id)
        items = get_recommendations(user_id, products)
        return jsonify({"items": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── /cache/clear ──────────────────────────────────────────────────────────────

@app.route("/cache/clear", methods=["POST"])
def cache_clear():
    if not session.get("user_id"):
        return jsonify({"error": "Не авторизован"}), 401
    from business_rules import cache_clear_user
    cache_clear_user(session["user_id"])
    return jsonify({"ok": True})


# ── /dashboard — Админ-дашборд ───────────────────────────────────────────────

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    redir = require_auth()
    if redir:
        return redir

    stats = summary = None
    from_cache = False

    if request.method == "POST":
        try:
            from business_rules import get_dashboard_stats, ask_mistral_dashboard
            stats      = get_dashboard_stats()
            from_cache = stats.get("from_cache", False)
            summary    = ask_mistral_dashboard(stats)
        except Exception as e:
            return render_template("dashboard.html", error=str(e))

    return render_template("dashboard.html",
                           stats=stats, summary=summary, from_cache=from_cache)


@app.route("/dashboard/refresh", methods=["POST"])
def dashboard_refresh():
    redir = require_auth()
    if redir:
        return redirect(url_for("index"))
    from business_rules import cache_clear_dashboard
    cache_clear_dashboard()
    return redirect(url_for("dashboard"))


# ── JSON API ──────────────────────────────────────────────────────────────────

@app.route("/api/history", methods=["POST"])
def api_history():
    if not session.get("user_id"):
        return jsonify({"error": "Не авторизован"}), 401
    try:
        from business_rules import get_user_history, build_products_text, ask_mistral
        user_id = session["user_id"]
        rows, _ = get_user_history(user_id)
        prompt, recommendation, _ = ask_mistral(user_id, build_products_text(rows))
        return jsonify({"user_id": user_id, "products": rows,
                        "prompt": prompt, "recommendation": recommendation})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
