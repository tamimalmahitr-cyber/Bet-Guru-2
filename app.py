from flask import Flask, render_template, request, redirect, session, flash, jsonify
from db import get_db, init_db
import os
import random
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change_this_in_production_use_long_random_string")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


# ─── Helper: get balance safely ───────────────────────────────────────────────

def get_balance(username):
    """Always returns balance for navbar. Returns 0 if not found."""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE username=%s", (username,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception as e:
        print(f"Database error in get_balance: {e}")
        return 0


# ─── Auth Routes ──────────────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def login():
    if "user" in session:
        return redirect("/games")
    if request.method == "POST":
        user = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        if not user or not pw:
            flash("Username and password are required.", "danger")
            return render_template("login.html")
        try:
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT username, password FROM users WHERE username=%s", (user,))
            data = c.fetchone()
            conn.close()
        except Exception as e:
            print(f"Database error during login: {e}")
            flash("Database connection error. Please try again.", "danger")
            return render_template("login.html")
        # Support both hashed and old plain-text passwords
        if data:
            stored_pw = data[1]
            # Check if password is hashed (werkzeug hashes start with pbkdf2/scrypt/bcrypt)
            if stored_pw.startswith("pbkdf2:") or stored_pw.startswith("scrypt:"):
                valid = check_password_hash(stored_pw, pw)
            else:
                # Old plain-text password — still allow login, will be upgraded on next register
                valid = (stored_pw == pw)
            if valid:
                session["user"] = data[0]
                return redirect("/games")
        flash("Invalid username or password.", "danger")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        user = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        if not user or not pw:
            flash("Username and password are required.", "danger")
            return render_template("register.html")
        if len(pw) < 4:
            flash("Password must be at least 4 characters.", "danger")
            return render_template("register.html")
        hashed_pw = generate_password_hash(pw)
        try:
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT id FROM users WHERE username=%s", (user,))
            if c.fetchone():
                conn.close()
                flash("Username already taken.", "danger")
                return render_template("register.html")
            c.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (user, hashed_pw))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Database error during registration: {e}")
            flash("Database connection error. Please try again.", "danger")
            return render_template("register.html")
        flash("Account created! Please login.", "success")
        return redirect("/")
    return render_template("register.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect("/")


# ─── Games Lobby ──────────────────────────────────────────────────────────────

@app.route("/games")
def games():
    if "user" not in session:
        return redirect("/")
    balance = get_balance(session["user"])
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            SELECT id, game_type, status, bet_amount,
                   (SELECT COUNT(*) FROM game_players WHERE room_id=game_rooms.id) as player_count
            FROM game_rooms WHERE status IN ('waiting','running')
            ORDER BY created_at DESC LIMIT 20
        """)
        rooms = c.fetchall()
        conn.close()
        return render_template("games.html", balance=balance, rooms=rooms)
    except Exception as e:
        print(f"Database error in games: {e}")
        flash("Database connection error. Please try again.", "danger")
        return render_template("games.html", balance=balance, rooms=[])


# ─── Create Game Rooms ────────────────────────────────────────────────────────

def create_room(game_type):
    """Generic room creator for all game types."""
    if "user" not in session:
        return redirect("/")
    try:
        bet = int(request.form.get("bet_amount", 0))
    except (ValueError, TypeError):
        flash("Invalid bet amount.", "danger")
        return redirect("/games")

    if bet <= 0:
        flash("Bet amount must be greater than 0.", "danger")
        return redirect("/games")

    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE username=%s", (session["user"],))
        row = c.fetchone()
        if not row or bet > row[0]:
            conn.close()
            flash("Insufficient balance.", "danger")
            return redirect("/games")

        c.execute(
            "INSERT INTO game_rooms (game_type, bet_amount, max_players) VALUES (%s, %s, 10) RETURNING id",
            (game_type, bet)
        )
        room_id = c.fetchone()[0]

        # FIX: Creator automatically joins the room with a default choice
        default_choice = {"coinflip": "heads", "dice": "1", "colorbet": "red"}[game_type]
        c.execute(
            "UPDATE users SET balance = balance - %s WHERE username=%s",
            (bet, session["user"])
        )
        c.execute(
            "INSERT INTO game_players (room_id, username, bet_amount, choice) VALUES (%s, %s, %s, %s)",
            (room_id, session["user"], bet, default_choice)
        )
        # Store creator in game_rooms so only creator can start
        c.execute("UPDATE game_rooms SET creator=%s WHERE id=%s", (session["user"], room_id))
        conn.commit()
        conn.close()
        flash("Room created! You joined with a default choice — you can change it before the game starts.", "info")
        return redirect(f"/game/room/{room_id}")
    except Exception as e:
        print(f"Database error in create_room: {e}")
        flash("Database connection error. Please try again.", "danger")
        return redirect("/games")


@app.route("/game/coinflip/create", methods=["POST"])
def coinflip_create():
    return create_room("coinflip")


@app.route("/game/dice/create", methods=["POST"])
def dice_create():
    return create_room("dice")


@app.route("/game/colorbet/create", methods=["POST"])
def colorbet_create():
    return create_room("colorbet")


# ─── Game Room ────────────────────────────────────────────────────────────────

@app.route("/game/room/<int:room_id>")
def game_room(room_id):
    if "user" not in session:
        return redirect("/")
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM game_rooms WHERE id=%s", (room_id,))
        room = c.fetchone()
        if not room:
            conn.close()
            flash("Room not found.", "danger")
            return redirect("/games")

        c.execute("""
            SELECT username, bet_amount, choice, result, payout
            FROM game_players WHERE room_id=%s ORDER BY joined_at
        """, (room_id,))
        players = c.fetchall()

        c.execute("SELECT * FROM game_players WHERE room_id=%s AND username=%s", (room_id, session["user"]))
        already_joined = c.fetchone()

        balance = get_balance(session["user"])
        conn.close()

        return render_template("game_room.html",
                               room=room, players=players,
                               already_joined=already_joined,
                               balance=balance,
                               username=session["user"])
    except Exception as e:
        print(f"Database error in game_room: {e}")
        flash("Database connection error. Please try again.", "danger")
        return redirect("/games")


@app.route("/game/room/<int:room_id>/join", methods=["POST"])
def join_room(room_id):
    if "user" not in session:
        return redirect("/")
    choice = request.form.get("choice", "").strip()
    if not choice:
        flash("Please select a choice.", "danger")
        return redirect(f"/game/room/{room_id}")

    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT * FROM game_rooms WHERE id=%s AND status='waiting'", (room_id,))
    room = c.fetchone()
    if not room:
        conn.close()
        flash("Room is not available.", "danger")
        return redirect("/games")

    bet = room[4]  # bet_amount
    c.execute("SELECT balance FROM users WHERE username=%s", (session["user"],))
    bal_row = c.fetchone()
    if not bal_row or bet > bal_row[0]:
        conn.close()
        flash("Insufficient balance.", "danger")
        return redirect(f"/game/room/{room_id}")

    # Check already joined
    c.execute("SELECT id FROM game_players WHERE room_id=%s AND username=%s", (room_id, session["user"]))
    if c.fetchone():
        conn.close()
        flash("You already joined this room.", "warning")
        return redirect(f"/game/room/{room_id}")

    # Check max players
    c.execute("SELECT COUNT(*) FROM game_players WHERE room_id=%s", (room_id,))
    count = c.fetchone()[0]
    if count >= room[3]:  # max_players
        conn.close()
        flash("Room is full.", "danger")
        return redirect("/games")

    # Deduct bet from balance
    c.execute("UPDATE users SET balance = balance - %s WHERE username=%s", (bet, session["user"]))
    c.execute(
        "INSERT INTO game_players (room_id, username, bet_amount, choice) VALUES (%s, %s, %s, %s)",
        (room_id, session["user"], bet, choice)
    )
    conn.commit()
    conn.close()
    return redirect(f"/game/room/{room_id}")


@app.route("/game/room/<int:room_id>/start", methods=["POST"])
def start_game(room_id):
    if "user" not in session:
        return redirect("/")

    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT * FROM game_rooms WHERE id=%s AND status='waiting'", (room_id,))
    room = c.fetchone()
    if not room:
        conn.close()
        flash("Cannot start game.", "danger")
        return redirect(f"/game/room/{room_id}")

    # FIX: Only the room creator can start the game
    creator = room[9] if len(room) > 9 else None  # creator column
    if creator and creator != session["user"]:
        conn.close()
        flash("Only the room creator can start the game.", "danger")
        return redirect(f"/game/room/{room_id}")

    c.execute("SELECT COUNT(*) FROM game_players WHERE room_id=%s", (room_id,))
    count = c.fetchone()[0]
    if count < 2:
        conn.close()
        flash("Need at least 2 players to start.", "warning")
        return redirect(f"/game/room/{room_id}")

    game_type = room[1]

    # Determine result
    if game_type == "coinflip":
        result = random.choice(["heads", "tails"])
    elif game_type == "dice":
        result = str(random.randint(1, 6))
    elif game_type == "colorbet":
        result = random.choice(["red", "green", "blue"])
    else:
        result = "unknown"

    c.execute("SELECT id, username, bet_amount, choice FROM game_players WHERE room_id=%s", (room_id,))
    players = c.fetchall()

    winners = [p for p in players if p[3] == result]
    total_pool = sum(p[2] for p in players)

    if winners:
        share = total_pool // len(winners)
        for p in players:
            if p[3] == result:
                c.execute("UPDATE game_players SET result='won', payout=%s WHERE id=%s", (share, p[0]))
                c.execute("UPDATE users SET balance = balance + %s WHERE username=%s", (share, p[1]))
            else:
                c.execute("UPDATE game_players SET result='lost', payout=0 WHERE id=%s", (p[0],))
    else:
        for p in players:
            c.execute("UPDATE game_players SET result='lost', payout=0 WHERE id=%s", (p[0],))

    c.execute("UPDATE game_rooms SET status='finished', result=%s, ended_at=NOW() WHERE id=%s",
              (result, room_id))
    conn.commit()
    conn.close()
    return redirect(f"/game/room/{room_id}")


# ─── Dashboard (Deposit/Withdraw) ─────────────────────────────────────────────

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if "user" not in session:
        return redirect("/")
    balance = get_balance(session["user"])

    if request.method == "POST":
        req_type = request.form.get("type")
        try:
            amount = int(request.form.get("amount", 0))
        except (ValueError, TypeError):
            flash("Invalid amount.", "danger")
            return redirect("/dashboard")

        if amount <= 0:
            flash("Amount must be greater than 0.", "danger")
            return redirect("/dashboard")

        if req_type == "withdraw":
            # FIX: Check balance before allowing withdraw request
            if amount > balance:
                flash("Insufficient balance for withdrawal.", "danger")
                return redirect("/dashboard")
            # FIX: Lock the amount immediately so user can't double-spend
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT balance FROM users WHERE username=%s", (session["user"],))
            current_balance = c.fetchone()[0]
            if amount > current_balance:
                conn.close()
                flash("Insufficient balance for withdrawal.", "danger")
                return redirect("/dashboard")
            # Deduct immediately, refund if admin rejects
            c.execute("UPDATE users SET balance = balance - %s WHERE username=%s",
                      (amount, session["user"]))
            c.execute(
                "INSERT INTO transactions (username, type, amount, status) VALUES (%s, %s, %s, 'Pending')",
                (session["user"], req_type, amount)
            )
            conn.commit()
            conn.close()
            flash("Withdraw request submitted! Amount has been held pending admin approval.", "info")

        elif req_type == "deposit":
            conn = get_db()
            c = conn.cursor()
            c.execute(
                "INSERT INTO transactions (username, type, amount, status) VALUES (%s, %s, %s, 'Pending')",
                (session["user"], req_type, amount)
            )
            conn.commit()
            conn.close()
            flash("Deposit request submitted! Waiting for admin approval.", "info")

        return redirect("/dashboard")

    return render_template("dashboard.html", balance=balance)


# ─── Profile ──────────────────────────────────────────────────────────────────

@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "user" not in session:
        return redirect("/")
    conn = get_db()
    c = conn.cursor()

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        c.execute("UPDATE users SET email=%s, phone=%s WHERE username=%s",
                  (email, phone, session["user"]))
        conn.commit()
        flash("Profile updated!", "success")

    c.execute("SELECT balance, email, phone FROM users WHERE username=%s", (session["user"],))
    row = c.fetchone()
    balance = row[0] if row else 0
    email = row[1] if row else ""
    phone = row[2] if row else ""

    c.execute("""
        SELECT COUNT(*), COALESCE(SUM(payout), 0),
               SUM(CASE WHEN result='won' THEN 1 ELSE 0 END)
        FROM game_players WHERE username=%s
    """, (session["user"],))
    stats = c.fetchone()
    conn.close()

    return render_template("profile.html",
                           username=session["user"],
                           balance=balance, email=email, phone=phone,
                           total_games=stats[0] or 0,
                           total_won=stats[2] or 0,
                           total_earnings=stats[1] or 0)


# ─── History ──────────────────────────────────────────────────────────────────

@app.route("/history")
def history():
    if "user" not in session:
        return redirect("/")
    balance = get_balance(session["user"])
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT type, amount, status, timestamp FROM transactions WHERE username=%s ORDER BY timestamp DESC",
        (session["user"],)
    )
    transactions = c.fetchall()

    # FIX: Also show game history
    c.execute("""
        SELECT gr.game_type, gp.bet_amount, gp.choice, gp.result, gp.payout, gr.result as game_result, gr.ended_at
        FROM game_players gp
        JOIN game_rooms gr ON gp.room_id = gr.id
        WHERE gp.username=%s ORDER BY gr.created_at DESC
    """, (session["user"],))
    game_history = c.fetchall()

    conn.close()
    return render_template("history.html", transactions=transactions,
                           game_history=game_history, balance=balance)


# ─── Admin Routes ─────────────────────────────────────────────────────────────

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        user = request.form.get("username", "")
        pw = request.form.get("password", "")
        if user == ADMIN_USERNAME and pw == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")
        else:
            return render_template("admin.html", error="Invalid admin credentials")

    if "admin" not in session:
        return render_template("admin.html", error=None)

    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id, username, type, amount FROM transactions WHERE status='Pending' ORDER BY timestamp DESC")
        requests_list = c.fetchall()
        c.execute("SELECT COALESCE(SUM(balance), 0) FROM users")
        total_balance = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        conn.close()

        return render_template("admin_panel.html",
                               requests=requests_list,
                               total_balance=total_balance,
                               total_users=total_users)
    except Exception as e:
        print(f"Database error in admin panel: {e}")
        flash("Database connection error. Please try again.", "danger")
        return render_template("admin.html", error="Database error")


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("admin", None)
    return redirect("/admin")


# FIX: Changed to POST to prevent CSRF
@app.route("/admin/action/<int:txn_id>/<status>", methods=["POST"])
def admin_action(txn_id, status):
    if "admin" not in session:
        return redirect("/admin")
    if status not in ("Approved", "Rejected"):
        return redirect("/admin")

    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT username, type, amount FROM transactions WHERE id=%s AND status='Pending'", (txn_id,))
        txn = c.fetchone()
        if txn:
            username, txn_type, amount = txn
            if status == "Approved":
                if txn_type == "deposit":
                    c.execute("UPDATE users SET balance = balance + %s WHERE username=%s", (amount, username))
                elif txn_type == "withdraw":
                    # FIX: With new system, amount was already deducted. If approved, nothing to do.
                    # If rejected, refund the user.
                    pass
            elif status == "Rejected":
                if txn_type == "withdraw":
                    # Refund the held amount back to user
                    c.execute("UPDATE users SET balance = balance + %s WHERE username=%s", (amount, username))
                # For rejected deposit: nothing was added, so nothing to undo
            c.execute("UPDATE transactions SET status=%s WHERE id=%s", (status, txn_id))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Database error in admin action: {e}")
        flash("Database connection error. Please try again.", "danger")
    return redirect("/admin")


@app.route("/admin/users")
def admin_users():
    if "admin" not in session:
        return redirect("/admin")
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id, username, email, phone, balance FROM users ORDER BY id")
        users = c.fetchall()
        conn.close()
        return render_template("admin_users.html", users=users)
    except Exception as e:
        print(f"Database error in admin users: {e}")
        flash("Database connection error. Please try again.", "danger")
        return redirect("/admin")


@app.route("/admin/user/<int:user_id>", methods=["GET", "POST"])
def admin_user_detail(user_id):
    if "admin" not in session:
        return redirect("/admin")
    conn = get_db()
    c = conn.cursor()

    if request.method == "POST":
        action = request.form.get("action")
        if action == "update_balance":
            try:
                new_balance = int(request.form.get("balance", 0))
                if new_balance < 0:
                    flash("Balance cannot be negative.", "danger")
                else:
                    c.execute("UPDATE users SET balance=%s WHERE id=%s", (new_balance, user_id))
                    conn.commit()
                    flash("Balance updated!", "success")
            except (ValueError, TypeError):
                flash("Invalid balance amount.", "danger")

        elif action == "update_info":
            email = request.form.get("email", "").strip()
            phone = request.form.get("phone", "").strip()
            password = request.form.get("password", "").strip()
            if password:
                hashed = generate_password_hash(password)
                c.execute("UPDATE users SET email=%s, phone=%s, password=%s WHERE id=%s",
                          (email, phone, hashed, user_id))
            else:
                c.execute("UPDATE users SET email=%s, phone=%s WHERE id=%s",
                          (email, phone, user_id))
            conn.commit()
            flash("User info updated!", "success")

        elif action == "delete_user":
            c.execute("DELETE FROM game_players WHERE username=(SELECT username FROM users WHERE id=%s)", (user_id,))
            c.execute("DELETE FROM transactions WHERE username=(SELECT username FROM users WHERE id=%s)", (user_id,))
            c.execute("DELETE FROM users WHERE id=%s", (user_id,))
            conn.commit()
            conn.close()
            flash("User deleted.", "warning")
            return redirect("/admin/users")

    c.execute("SELECT id, username, email, phone, balance FROM users WHERE id=%s", (user_id,))
    user = c.fetchone()
    if not user:
        conn.close()
        flash("User not found.", "danger")
        return redirect("/admin/users")

    c.execute("SELECT type, amount, status, timestamp FROM transactions WHERE username=%s ORDER BY timestamp DESC",
              (user[1],))
    transactions = c.fetchall()

    c.execute("""
        SELECT gr.game_type, gp.bet_amount, gp.choice, gp.result, gp.payout, gr.result as game_result, gr.created_at
        FROM game_players gp
        JOIN game_rooms gr ON gp.room_id = gr.id
        WHERE gp.username=%s ORDER BY gr.created_at DESC
    """, (user[1],))
    game_history = c.fetchall()

    conn.close()
    return render_template("admin_user_detail.html",
                           user=user,
                           transactions=transactions,
                           game_history=game_history)


@app.route("/admin/all_transactions")
def admin_all_transactions():
    if "admin" not in session:
        return redirect("/admin")
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, type, amount, status, timestamp FROM transactions ORDER BY timestamp DESC")
    data = c.fetchall()
    c.execute("SELECT COALESCE(SUM(balance), 0) FROM users")
    total_balance = c.fetchone()[0]
    conn.close()
    return render_template("transactions.html", data=data, total_balance=total_balance)


if __name__ == "__main__":
    init_db()
    app.run(debug=False)
