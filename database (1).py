import sqlite3
import threading
import time


class Database:
    def __init__(self, path: str) -> None:
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                balance INTEGER NOT NULL DEFAULT 0
                    CHECK (balance >= 0),
                vip INTEGER NOT NULL DEFAULT 0,
                last_bonus INTEGER NOT NULL DEFAULT 0,
                invited_by INTEGER
            );

            CREATE TABLE IF NOT EXISTS rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                game TEXT NOT NULL,
                stake INTEGER NOT NULL,
                payout INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def user(self, user_id: int, name: str, invited_by: int | None = None):
        with self.lock, self.conn:
            existing = self.conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()

            if existing is None:
                referrer = (
                    self.conn.execute(
                        "SELECT user_id FROM users WHERE user_id = ?",
                        (invited_by,),
                    ).fetchone()
                    if invited_by and invited_by != user_id
                    else None
                )

                self.conn.execute(
                    "INSERT INTO users (user_id, name, invited_by) "
                    "VALUES (?, ?, ?)",
                    (user_id, name, invited_by if referrer else None),
                )
                if referrer:
                    # NOT OBSERVED: виртуальная награда за приглашение.
                    self.conn.execute(
                        "UPDATE users SET balance = balance + 50 "
                        "WHERE user_id = ?",
                        (invited_by,),
                    )
            else:
                self.conn.execute(
                    "UPDATE users SET name = ? WHERE user_id = ?",
                    (name, user_id),
                )

            return self.conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()

    def reserve(self, user_id: int, game: str, stake: int) -> int | None:
        with self.lock, self.conn:
            updated = self.conn.execute(
                "UPDATE users SET balance = balance - ? "
                "WHERE user_id = ? AND balance >= ?",
                (stake, user_id, stake),
            )
            if updated.rowcount != 1:
                return None
            cursor = self.conn.execute(
                "INSERT INTO rounds (user_id, game, stake, status, created_at) "
                "VALUES (?, ?, ?, 'pending', ?)",
                (user_id, game, stake, int(time.time())),
            )
            return cursor.lastrowid

    def settle(self, round_id: int, payout: int) -> int:
        with self.lock, self.conn:
            row = self.conn.execute(
                "SELECT user_id FROM rounds WHERE id = ? AND status = 'pending'",
                (round_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Раунд уже завершён")

            self.conn.execute(
                "UPDATE rounds SET payout = ?, status = 'done' WHERE id = ?",
                (payout, round_id),
            )
            self.conn.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (payout, row["user_id"]),
            )
            return self.conn.execute(
                "SELECT balance FROM users WHERE user_id = ?",
                (row["user_id"],),
            ).fetchone()["balance"]

    def refund(self, round_id: int) -> None:
        with self.lock, self.conn:
            row = self.conn.execute(
                "SELECT user_id, stake FROM rounds "
                "WHERE id = ? AND status = 'pending'",
                (round_id,),
            ).fetchone()
            if row:
                self.conn.execute(
                    "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                    (row["stake"], row["user_id"]),
                )
                self.conn.execute(
                    "UPDATE rounds SET status = 'refunded' WHERE id = ?",
                    (round_id,),
                )

    def refund_unfinished_rounds(self) -> None:
        with self.lock:
            rows = self.conn.execute(
                "SELECT id FROM rounds WHERE status = 'pending'"
            ).fetchall()
            for row in rows:
                self.refund(row["id"])

    def bonus(self, user_id: int, amount: int) -> tuple[bool, int]:
        now = int(time.time())
        with self.lock, self.conn:
            updated = self.conn.execute(
                "UPDATE users SET last_bonus = ?, balance = balance + ? "
                "WHERE user_id = ? AND last_bonus <= ?",
                (now, amount, user_id, now - 86400),
            )
            balance = self.conn.execute(
                "SELECT balance FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()["balance"]
            return updated.rowcount == 1, balance

    def buy_vip(self, user_id: int) -> bool:
        # NOT OBSERVED: цена и возможности VIP.
        with self.lock, self.conn:
            updated = self.conn.execute(
                "UPDATE users SET balance = balance - 500, vip = 1 "
                "WHERE user_id = ? AND balance >= 500 AND vip = 0",
                (user_id,),
            )
            return updated.rowcount == 1

    def top(self):
        with self.lock:
            return self.conn.execute(
                "SELECT name, balance FROM users "
                "ORDER BY balance DESC, user_id LIMIT 10"
            ).fetchall()

    def history(self, user_id: int):
        with self.lock:
            return self.conn.execute(
                "SELECT game, stake, payout, status FROM rounds "
                "WHERE user_id = ? ORDER BY id DESC LIMIT 10",
                (user_id,),
            ).fetchall()
