from __future__ import annotations

import json
import os
from dataclasses import dataclass

import psycopg


@dataclass
class OrderState:
    user_id: int
    stage: str = "collecting"
    photo_file_ids: list[str] | None = None
    style: str | None = None
    pack_url: str | None = None
    payment_charge_id: str | None = None

    def __post_init__(self) -> None:
        if self.photo_file_ids is None:
            self.photo_file_ids = []


class OrderStore:
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = (database_url or os.getenv("DATABASE_URL", "")).strip()
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is empty")

    def connect(self):
        return psycopg.connect(self.database_url, autocommit=False)

    def ensure_schema(self) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sticker_orders (
                        user_id BIGINT PRIMARY KEY,
                        stage TEXT NOT NULL DEFAULT 'collecting',
                        photo_file_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                        style TEXT,
                        pack_url TEXT,
                        payment_charge_id TEXT,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            conn.commit()

    def get(self, user_id: int) -> OrderState:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT stage, photo_file_ids, style, pack_url, payment_charge_id
                    FROM sticker_orders
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
        if not row:
            return OrderState(user_id=user_id)
        photos = row[1]
        if isinstance(photos, str):
            photos = json.loads(photos)
        return OrderState(
            user_id=user_id,
            stage=row[0] or "collecting",
            photo_file_ids=list(photos or []),
            style=row[2],
            pack_url=row[3],
            payment_charge_id=row[4],
        )

    def reset(self, user_id: int) -> OrderState:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sticker_orders (user_id, stage, photo_file_ids, style, pack_url, payment_charge_id, updated_at)
                    VALUES (%s, 'collecting', '[]'::jsonb, NULL, NULL, NULL, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
                        stage = 'collecting',
                        photo_file_ids = '[]'::jsonb,
                        style = NULL,
                        pack_url = NULL,
                        payment_charge_id = NULL,
                        updated_at = NOW()
                    """,
                    (user_id,),
                )
            conn.commit()
        return OrderState(user_id=user_id)

    def add_photo(self, user_id: int, file_id: str, max_photos: int = 5) -> OrderState:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sticker_orders (user_id, stage, photo_file_ids, updated_at)
                    VALUES (%s, 'collecting', '[]'::jsonb, NOW())
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    (user_id,),
                )
                cur.execute(
                    """
                    SELECT stage, photo_file_ids, style, pack_url, payment_charge_id
                    FROM sticker_orders
                    WHERE user_id = %s
                    FOR UPDATE
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
                stage = row[0] if row else "collecting"
                photos = row[1] if row else []
                if isinstance(photos, str):
                    photos = json.loads(photos)
                photos = list(photos or [])
                if stage != "collecting":
                    conn.rollback()
                    return OrderState(
                        user_id=user_id,
                        stage=stage,
                        photo_file_ids=photos,
                        style=row[2] if row else None,
                        pack_url=row[3] if row else None,
                        payment_charge_id=row[4] if row else None,
                    )
                if len(photos) < max_photos:
                    photos.append(file_id)
                cur.execute(
                    """
                    UPDATE sticker_orders
                    SET photo_file_ids = %s::jsonb, updated_at = NOW()
                    WHERE user_id = %s
                    """,
                    (json.dumps(photos), user_id),
                )
            conn.commit()
        return self.get(user_id)

    def set_style(self, user_id: int, style: str, stage: str = "ready") -> OrderState:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sticker_orders (user_id, stage, photo_file_ids, style, updated_at)
                    VALUES (%s, %s, '[]'::jsonb, %s, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
                        style = EXCLUDED.style,
                        stage = EXCLUDED.stage,
                        updated_at = NOW()
                    """,
                    (user_id, stage, style),
                )
            conn.commit()
        return self.get(user_id)

    def set_pack_url(self, user_id: int, pack_url: str) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sticker_orders SET pack_url = %s, stage = 'done', updated_at = NOW() WHERE user_id = %s",
                    (pack_url, user_id),
                )
            conn.commit()

    def set_payment(self, user_id: int, charge_id: str) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sticker_orders SET payment_charge_id = %s, stage = 'paid', updated_at = NOW() WHERE user_id = %s",
                    (charge_id, user_id),
                )
            conn.commit()
