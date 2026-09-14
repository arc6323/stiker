import os

from state_store import OrderStore


def test_order_survives_store_restart():
    url = os.environ["DATABASE_URL"]
    store1 = OrderStore(url)
    store1.ensure_schema()

    user_id = 987654321
    store1.reset(user_id)
    store1.add_photo(user_id, "telegram-file-1")
    store1.add_photo(user_id, "telegram-file-2")
    store1.add_photo(user_id, "telegram-file-3")
    store1.set_style(user_id, "gangster", "ready")

    # Simulate a brand-new bot process after Render sleep/restart.
    store2 = OrderStore(url)
    state = store2.get(user_id)

    assert state.photo_file_ids == [
        "telegram-file-1",
        "telegram-file-2",
        "telegram-file-3",
    ]
    assert state.style == "gangster"
    assert state.stage == "ready"
