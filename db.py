import psycopg2
import os

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_db():
    url = DATABASE_URL
    if not url:
        # Use SQLite fallback if no PostgreSQL URL is provided
        import sqlite3
        return sqlite3.connect("betting_app.db")
    if url and url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url)


def init_db():
    try:
        conn = get_db()
        c = conn.cursor()

        # Check if using SQLite or PostgreSQL
        is_sqlite = DATABASE_URL is None

        if is_sqlite:
            # SQLite schema
            c.execute("""CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                email TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                balance INTEGER DEFAULT 100
            )""")

            c.execute("""CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                type TEXT NOT NULL,
                amount INTEGER NOT NULL,
                status TEXT DEFAULT 'Pending',
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")

            c.execute("""CREATE TABLE IF NOT EXISTS game_rooms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_type TEXT NOT NULL,
                status TEXT DEFAULT 'waiting',
                max_players INTEGER DEFAULT 10,
                bet_amount INTEGER DEFAULT 0,
                result TEXT DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                ended_at DATETIME DEFAULT NULL,
                creator TEXT DEFAULT NULL
            )""")

            c.execute("""CREATE TABLE IF NOT EXISTS game_players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER,
                username TEXT NOT NULL,
                bet_amount INTEGER NOT NULL,
                choice TEXT DEFAULT NULL,
                payout INTEGER DEFAULT 0,
                result TEXT DEFAULT 'pending',
                joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (room_id) REFERENCES game_rooms(id)
            )""")
        else:
            # PostgreSQL schema
            c.execute("""CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                email TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                balance INTEGER DEFAULT 100
            )""")

            c.execute("""CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL,
                type TEXT NOT NULL,
                amount INTEGER NOT NULL,
                status TEXT DEFAULT 'Pending',
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")

            c.execute("""CREATE TABLE IF NOT EXISTS game_rooms (
                id SERIAL PRIMARY KEY,
                game_type TEXT NOT NULL,
                status TEXT DEFAULT 'waiting',
                max_players INTEGER DEFAULT 10,
                bet_amount INTEGER DEFAULT 0,
                result TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP DEFAULT NULL,
                creator TEXT DEFAULT NULL
            )""")

            c.execute("""CREATE TABLE IF NOT EXISTS game_players (
                id SERIAL PRIMARY KEY,
                room_id INTEGER REFERENCES game_rooms(id),
                username TEXT NOT NULL,
                bet_amount INTEGER NOT NULL,
                choice TEXT DEFAULT NULL,
                payout INTEGER DEFAULT 0,
                result TEXT DEFAULT 'pending',
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")

        # Safe migrations
        try:
            if is_sqlite:
                # SQLite doesn't support IF NOT EXISTS for column additions
                try:
                    c.execute("ALTER TABLE game_rooms ADD COLUMN creator TEXT DEFAULT NULL")
                except:
                    pass
            else:
                c.execute("ALTER TABLE game_rooms ADD COLUMN IF NOT EXISTS creator TEXT DEFAULT NULL")
            conn.commit()
        except Exception:
            conn.rollback()

        # Add constraint (SQLite version)
        try:
            if is_sqlite:
                # SQLite doesn't support CHECK constraints in ALTER TABLE
                pass
            else:
                c.execute("ALTER TABLE users ADD CONSTRAINT balance_non_negative CHECK (balance >= 0)")
                conn.commit()
        except Exception:
            conn.rollback()

        conn.commit()
        conn.close()
        print("Database initialized successfully!")
    except Exception as e:
        print(f"Error initializing database: {e}")
        raise
