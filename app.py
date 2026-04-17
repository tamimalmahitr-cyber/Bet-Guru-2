from datetime import datetime
import os
import random

from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, func
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
app.secret_key = os.environ.get(
    "SECRET_KEY", "change_this_in_production_use_long_random_string"
)

database_url = os.environ.get("DATABASE_URL", "").strip()
if not database_url:
    raise RuntimeError("DATABASE_URL environment variable is required for PostgreSQL.")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
if "sslmode=" not in database_url:
    separator = "&" if "?" in database_url else "?"
    database_url = f"{database_url}{separator}sslmode=require"

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

db = SQLAlchemy(app)

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


class User(db.Model):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("balance >= 0", name="balance_non_negative"),)

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password = db.Column(db.Text, nullable=False)
    email = db.Column(db.Text, nullable=False, default="")
    phone = db.Column(db.Text, nullable=False, default="")
    balance = db.Column(db.Integer, nullable=False, default=100)


class Transaction(db.Model):
    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(
        db.String(80),
        db.ForeignKey("users.username", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Pending")
    timestamp = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, server_default=func.now()
    )


class GameRoom(db.Model):
    __tablename__ = "game_rooms"

    id = db.Column(db.Integer, primary_key=True)
    game_type = db.Column(db.String(30), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="waiting")
    max_players = db.Column(db.Integer, nullable=False, default=10)
    bet_amount = db.Column(db.Integer, nullable=False, default=0)
    result = db.Column(db.String(30), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, server_default=func.now()
    )
    ended_at = db.Column(db.DateTime, nullable=True)
    creator = db.Column(
        db.String(80),
        db.ForeignKey("users.username", ondelete="SET NULL"),
        nullable=True,
    )


class GamePlayer(db.Model):
    __tablename__ = "game_players"

    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(
        db.Integer, db.ForeignKey("game_rooms.id", ondelete="CASCADE"), nullable=False
    )
    username = db.Column(
        db.String(80),
        db.ForeignKey("users.username", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bet_amount = db.Column(db.Integer, nullable=False)
    choice = db.Column(db.String(30), nullable=True)
    payout = db.Column(db.Integer, nullable=False, default=0)
    result = db.Column(db.String(20), nullable=False, default="pending")
    joined_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, server_default=func.now()
    )


def init_db():
    with app.app_context():
        db.create_all()
        db.session.commit()
        app.logger.info("PostgreSQL tables created successfully.")


def get_balance(username):
    user = User.query.filter_by(username=username).first()
    return user.balance if user else 0


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
            existing_user = User.query.filter_by(username=user).first()
        except Exception as e:
            db.session.rollback()
            app.logger.exception("Database error during login: %s", e)
            flash("Database connection error. Please try again.", "danger")
            return render_template("login.html")

        if existing_user:
            stored_pw = existing_user.password
            if stored_pw.startswith(("pbkdf2:", "scrypt:")):
                valid = check_password_hash(stored_pw, pw)
            else:
                valid = stored_pw == pw

            if valid:
                session["user"] = existing_user.username
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

        try:
            if User.query.filter_by(username=user).first():
                flash("Username already taken.", "danger")
                return render_template("register.html")

            new_user = User(username=user, password=generate_password_hash(pw))
            db.session.add(new_user)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            app.logger.exception("Database error during registration: %s", e)
            flash("Database connection error. Please try again.", "danger")
            return render_template("register.html")

        flash("Account created! Please login.", "success")
        return redirect("/")

    return render_template("register.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect("/")


@app.route("/games")
def games():
    if "user" not in session:
        return redirect("/")

    balance = get_balance(session["user"])
    try:
        player_counts = (
            db.session.query(
                GamePlayer.room_id, func.count(GamePlayer.id).label("player_count")
            )
            .group_by(GamePlayer.room_id)
            .subquery()
        )

        rows = (
            db.session.query(
                GameRoom.id,
                GameRoom.game_type,
                GameRoom.status,
                GameRoom.bet_amount,
                func.coalesce(player_counts.c.player_count, 0),
            )
            .outerjoin(player_counts, GameRoom.id == player_counts.c.room_id)
            .filter(GameRoom.status.in_(["waiting", "running"]))
            .order_by(GameRoom.created_at.desc())
            .limit(20)
            .all()
        )
        rooms = [tuple(row) for row in rows]
        return render_template("games.html", balance=balance, rooms=rooms)
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Database error in games: %s", e)
        flash("Database connection error. Please try again.", "danger")
        return render_template("games.html", balance=balance, rooms=[])


def create_room(game_type):
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
        current_user = User.query.filter_by(username=session["user"]).first()
        if not current_user or bet > current_user.balance:
            flash("Insufficient balance.", "danger")
            return redirect("/games")

        room = GameRoom(
            game_type=game_type,
            bet_amount=bet,
            max_players=10,
            creator=current_user.username,
        )
        db.session.add(room)
        db.session.flush()

        default_choice = {"coinflip": "heads", "dice": "1", "colorbet": "red"}[game_type]
        current_user.balance -= bet
        db.session.add(
            GamePlayer(
                room_id=room.id,
                username=current_user.username,
                bet_amount=bet,
                choice=default_choice,
            )
        )
        db.session.commit()
        flash(
            "Room created! You joined with a default choice and can change it before the game starts.",
            "info",
        )
        return redirect(f"/game/room/{room.id}")
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Database error in create_room: %s", e)
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


@app.route("/game/room/<int:room_id>")
def game_room(room_id):
    if "user" not in session:
        return redirect("/")

    try:
        room = GameRoom.query.get(room_id)
        if not room:
            flash("Room not found.", "danger")
            return redirect("/games")

        players = (
            GamePlayer.query.filter_by(room_id=room_id)
            .order_by(GamePlayer.joined_at)
            .all()
        )
        players_data = [
            (player.username, player.bet_amount, player.choice, player.result, player.payout)
            for player in players
        ]
        already_joined = GamePlayer.query.filter_by(
            room_id=room_id, username=session["user"]
        ).first()
        room_data = (
            room.id,
            room.game_type,
            room.status,
            room.max_players,
            room.bet_amount,
            room.result,
            room.created_at,
            room.ended_at,
            None,
            room.creator,
        )

        return render_template(
            "game_room.html",
            room=room_data,
            players=players_data,
            already_joined=already_joined,
            balance=get_balance(session["user"]),
            username=session["user"],
        )
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Database error in game_room: %s", e)
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

    try:
        room = GameRoom.query.filter_by(id=room_id, status="waiting").first()
        if not room:
            flash("Room is not available.", "danger")
            return redirect("/games")

        current_user = User.query.filter_by(username=session["user"]).first()
        if not current_user or room.bet_amount > current_user.balance:
            flash("Insufficient balance.", "danger")
            return redirect(f"/game/room/{room_id}")

        if GamePlayer.query.filter_by(room_id=room_id, username=session["user"]).first():
            flash("You already joined this room.", "warning")
            return redirect(f"/game/room/{room_id}")

        player_count = GamePlayer.query.filter_by(room_id=room_id).count()
        if player_count >= room.max_players:
            flash("Room is full.", "danger")
            return redirect("/games")

        current_user.balance -= room.bet_amount
        db.session.add(
            GamePlayer(
                room_id=room_id,
                username=current_user.username,
                bet_amount=room.bet_amount,
                choice=choice,
            )
        )
        db.session.commit()
        return redirect(f"/game/room/{room_id}")
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Database error in join_room: %s", e)
        flash("Database connection error. Please try again.", "danger")
        return redirect("/games")


@app.route("/game/room/<int:room_id>/start", methods=["POST"])
def start_game(room_id):
    if "user" not in session:
        return redirect("/")

    try:
        room = GameRoom.query.filter_by(id=room_id, status="waiting").first()
        if not room:
            flash("Cannot start game.", "danger")
            return redirect(f"/game/room/{room_id}")

        if room.creator and room.creator != session["user"]:
            flash("Only the room creator can start the game.", "danger")
            return redirect(f"/game/room/{room_id}")

        players = GamePlayer.query.filter_by(room_id=room_id).all()
        if len(players) < 2:
            flash("Need at least 2 players to start.", "warning")
            return redirect(f"/game/room/{room_id}")

        if room.game_type == "coinflip":
            result = random.choice(["heads", "tails"])
        elif room.game_type == "dice":
            result = str(random.randint(1, 6))
        elif room.game_type == "colorbet":
            result = random.choice(["red", "green", "blue"])
        else:
            result = "unknown"

        winners = [player for player in players if player.choice == result]
        total_pool = sum(player.bet_amount for player in players)

        if winners:
            share = total_pool // len(winners)
            winner_names = {winner.username for winner in winners}
            for player in players:
                if player.username in winner_names:
                    player.result = "won"
                    player.payout = share
                    winning_user = User.query.filter_by(username=player.username).first()
                    if winning_user:
                        winning_user.balance += share
                else:
                    player.result = "lost"
                    player.payout = 0
        else:
            for player in players:
                player.result = "lost"
                player.payout = 0

        room.status = "finished"
        room.result = result
        room.ended_at = datetime.utcnow()
        db.session.commit()
        return redirect(f"/game/room/{room_id}")
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Database error in start_game: %s", e)
        flash("Database connection error. Please try again.", "danger")
        return redirect("/games")


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

        try:
            current_user = User.query.filter_by(username=session["user"]).first()
            if not current_user:
                flash("User not found.", "danger")
                return redirect("/dashboard")

            if req_type == "withdraw":
                if amount > current_user.balance:
                    flash("Insufficient balance for withdrawal.", "danger")
                    return redirect("/dashboard")
                current_user.balance -= amount
                db.session.add(
                    Transaction(
                        username=current_user.username,
                        type=req_type,
                        amount=amount,
                        status="Pending",
                    )
                )
                flash(
                    "Withdraw request submitted! Amount has been held pending admin approval.",
                    "info",
                )
            elif req_type == "deposit":
                db.session.add(
                    Transaction(
                        username=current_user.username,
                        type=req_type,
                        amount=amount,
                        status="Pending",
                    )
                )
                flash("Deposit request submitted! Waiting for admin approval.", "info")
            else:
                flash("Invalid request type.", "danger")
                return redirect("/dashboard")

            db.session.commit()
            return redirect("/dashboard")
        except Exception as e:
            db.session.rollback()
            app.logger.exception("Database error in dashboard: %s", e)
            flash("Database connection error. Please try again.", "danger")
            return redirect("/dashboard")

    return render_template("dashboard.html", balance=balance)


@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "user" not in session:
        return redirect("/")

    try:
        current_user = User.query.filter_by(username=session["user"]).first()
        if not current_user:
            flash("User not found.", "danger")
            return redirect("/")

        if request.method == "POST":
            current_user.email = request.form.get("email", "").strip()
            current_user.phone = request.form.get("phone", "").strip()
            db.session.commit()
            flash("Profile updated!", "success")

        stats = (
            db.session.query(
                func.count(GamePlayer.id),
                func.coalesce(func.sum(GamePlayer.payout), 0),
                func.sum(func.case((GamePlayer.result == "won", 1), else_=0)),
            )
            .filter(GamePlayer.username == current_user.username)
            .first()
        )

        return render_template(
            "profile.html",
            username=current_user.username,
            balance=current_user.balance,
            email=current_user.email,
            phone=current_user.phone,
            total_games=stats[0] or 0,
            total_won=stats[2] or 0,
            total_earnings=stats[1] or 0,
        )
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Database error in profile: %s", e)
        flash("Database connection error. Please try again.", "danger")
        return redirect("/")


@app.route("/history")
def history():
    if "user" not in session:
        return redirect("/")

    try:
        transactions = (
            Transaction.query.filter_by(username=session["user"])
            .order_by(Transaction.timestamp.desc())
            .all()
        )
        transaction_data = [
            (txn.type, txn.amount, txn.status, txn.timestamp) for txn in transactions
        ]

        game_history_rows = (
            db.session.query(
                GameRoom.game_type,
                GamePlayer.bet_amount,
                GamePlayer.choice,
                GamePlayer.result,
                GamePlayer.payout,
                GameRoom.result,
                GameRoom.ended_at,
            )
            .join(GameRoom, GamePlayer.room_id == GameRoom.id)
            .filter(GamePlayer.username == session["user"])
            .order_by(GameRoom.created_at.desc())
            .all()
        )
        game_history = [tuple(row) for row in game_history_rows]

        return render_template(
            "history.html",
            transactions=transaction_data,
            game_history=game_history,
            balance=get_balance(session["user"]),
        )
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Database error in history: %s", e)
        flash("Database connection error. Please try again.", "danger")
        return redirect("/games")


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        user = request.form.get("username", "")
        pw = request.form.get("password", "")
        if user == ADMIN_USERNAME and pw == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")
        return render_template("admin.html", error="Invalid admin credentials")

    if "admin" not in session:
        return render_template("admin.html", error=None)

    try:
        requests_list = (
            Transaction.query.filter_by(status="Pending")
            .order_by(Transaction.timestamp.desc())
            .all()
        )
        requests_data = [
            (txn.id, txn.username, txn.type, txn.amount) for txn in requests_list
        ]
        total_balance = db.session.query(func.coalesce(func.sum(User.balance), 0)).scalar()
        total_users = db.session.query(func.count(User.id)).scalar()

        return render_template(
            "admin_panel.html",
            requests=requests_data,
            total_balance=total_balance or 0,
            total_users=total_users or 0,
        )
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Database error in admin panel: %s", e)
        flash("Database connection error. Please try again.", "danger")
        return render_template("admin.html", error="Database error")


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("admin", None)
    return redirect("/admin")


@app.route("/admin/action/<int:txn_id>/<status>", methods=["POST"])
def admin_action(txn_id, status):
    if "admin" not in session:
        return redirect("/admin")
    if status not in ("Approved", "Rejected"):
        return redirect("/admin")

    try:
        txn = Transaction.query.filter_by(id=txn_id, status="Pending").first()
        if txn:
            if status == "Approved":
                if txn.type == "deposit":
                    user = User.query.filter_by(username=txn.username).first()
                    if user:
                        user.balance += txn.amount
            elif status == "Rejected" and txn.type == "withdraw":
                user = User.query.filter_by(username=txn.username).first()
                if user:
                    user.balance += txn.amount

            txn.status = status
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Database error in admin action: %s", e)
        flash("Database connection error. Please try again.", "danger")

    return redirect("/admin")


@app.route("/admin/users")
def admin_users():
    if "admin" not in session:
        return redirect("/admin")

    try:
        users = User.query.order_by(User.id).all()
        users_data = [
            (user.id, user.username, user.email, user.phone, user.balance) for user in users
        ]
        return render_template("admin_users.html", users=users_data)
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Database error in admin users: %s", e)
        flash("Database connection error. Please try again.", "danger")
        return redirect("/admin")


@app.route("/admin/user/<int:user_id>", methods=["GET", "POST"])
def admin_user_detail(user_id):
    if "admin" not in session:
        return redirect("/admin")

    try:
        user = User.query.get(user_id)
        if not user:
            flash("User not found.", "danger")
            return redirect("/admin/users")

        if request.method == "POST":
            action = request.form.get("action")
            if action == "update_balance":
                try:
                    new_balance = int(request.form.get("balance", 0))
                except (ValueError, TypeError):
                    flash("Invalid balance amount.", "danger")
                    return redirect(url_for("admin_user_detail", user_id=user_id))

                if new_balance < 0:
                    flash("Balance cannot be negative.", "danger")
                    return redirect(url_for("admin_user_detail", user_id=user_id))

                user.balance = new_balance
                db.session.commit()
                flash("Balance updated!", "success")

            elif action == "update_info":
                user.email = request.form.get("email", "").strip()
                user.phone = request.form.get("phone", "").strip()
                password = request.form.get("password", "").strip()
                if password:
                    user.password = generate_password_hash(password)
                db.session.commit()
                flash("User info updated!", "success")

            elif action == "delete_user":
                Transaction.query.filter_by(username=user.username).delete()
                GamePlayer.query.filter_by(username=user.username).delete()
                GameRoom.query.filter_by(creator=user.username).update({"creator": None})
                db.session.delete(user)
                db.session.commit()
                flash("User deleted.", "warning")
                return redirect("/admin/users")

        transactions = (
            Transaction.query.filter_by(username=user.username)
            .order_by(Transaction.timestamp.desc())
            .all()
        )
        transactions_data = [
            (txn.type, txn.amount, txn.status, txn.timestamp) for txn in transactions
        ]

        game_history_rows = (
            db.session.query(
                GameRoom.game_type,
                GamePlayer.bet_amount,
                GamePlayer.choice,
                GamePlayer.result,
                GamePlayer.payout,
                GameRoom.result,
                GameRoom.created_at,
            )
            .join(GameRoom, GamePlayer.room_id == GameRoom.id)
            .filter(GamePlayer.username == user.username)
            .order_by(GameRoom.created_at.desc())
            .all()
        )
        game_history = [tuple(row) for row in game_history_rows]
        user_data = (user.id, user.username, user.email, user.phone, user.balance)

        return render_template(
            "admin_user_detail.html",
            user=user_data,
            transactions=transactions_data,
            game_history=game_history,
        )
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Database error in admin_user_detail: %s", e)
        flash("Database connection error. Please try again.", "danger")
        return redirect("/admin/users")


@app.route("/admin/all_transactions")
def admin_all_transactions():
    if "admin" not in session:
        return redirect("/admin")

    try:
        rows = Transaction.query.order_by(Transaction.timestamp.desc()).all()
        data = [(row.username, row.type, row.amount, row.status, row.timestamp) for row in rows]
        total_balance = db.session.query(func.coalesce(func.sum(User.balance), 0)).scalar()
        return render_template(
            "transactions.html", data=data, total_balance=total_balance or 0
        )
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Database error in admin_all_transactions: %s", e)
        flash("Database connection error. Please try again.", "danger")
        return redirect("/admin")


init_db()


if __name__ == "__main__":
    app.run(debug=False)
