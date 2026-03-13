"""Tests for backfilling game_days on conversations that lack it."""
import pytest
from pathlib import Path
from src.conversation.conversation_db import ConversationDB


@pytest.fixture
def db(tmp_path: Path) -> ConversationDB:
    db = ConversationDB(tmp_path / "test.db")
    yield db
    db.close()


class TestBackfillGameDays:
    def test_backfill_within_session_scales_by_timescale(self, db):
        """Conversations within one session get timescale-scaled game_days."""
        base_ts = 1000000.0
        # 3 conversations 10 minutes apart (600s < 1800s session_gap)
        db.conn.execute("INSERT INTO conversations (id, world_id, started_at, ended_at) VALUES (?, ?, ?, ?)",
                       ("c1", "world1", base_ts, base_ts + 300))
        db.conn.execute("INSERT INTO conversations (id, world_id, started_at, ended_at) VALUES (?, ?, ?, ?)",
                       ("c2", "world1", base_ts + 600, base_ts + 900))
        db.conn.execute("INSERT INTO conversations (id, world_id, started_at, ended_at) VALUES (?, ?, ?, ?)",
                       ("c3", "world1", base_ts + 1200, base_ts + 1500))
        db.conn.commit()

        count = db.backfill_game_days(timescale=20)
        assert count == 3

        rows = db.conn.execute("SELECT id, game_days FROM conversations ORDER BY started_at").fetchall()
        days = [r[1] for r in rows]
        assert days[0] == pytest.approx(1.0, abs=0.01)
        # c3: 1200s * 20 = 24000 game seconds = 0.278 game days
        assert days[2] == pytest.approx(1.0 + 1200 * 20 / 86400, abs=0.01)

    def test_backfill_session_breaks_add_fixed_hours(self, db):
        """Gaps > session_gap should add hours_per_break instead of scaling."""
        base_ts = 1000000.0
        # c1 at base, c2 at base+2h (session break), c3 at base+2h+5m (same session as c2)
        db.conn.execute("INSERT INTO conversations (id, world_id, started_at, ended_at) VALUES (?, ?, ?, ?)",
                       ("c1", "world1", base_ts, base_ts + 300))
        db.conn.execute("INSERT INTO conversations (id, world_id, started_at, ended_at) VALUES (?, ?, ?, ?)",
                       ("c2", "world1", base_ts + 7200, base_ts + 7500))
        db.conn.execute("INSERT INTO conversations (id, world_id, started_at, ended_at) VALUES (?, ?, ?, ?)",
                       ("c3", "world1", base_ts + 7500, base_ts + 7800))
        db.conn.commit()

        count = db.backfill_game_days(timescale=20, session_gap=1800, hours_per_break=8)
        assert count == 3

        rows = db.conn.execute("SELECT id, game_days FROM conversations ORDER BY started_at").fetchall()
        days = [r[1] for r in rows]
        assert days[0] == pytest.approx(1.0, abs=0.01)
        # c2: 1 session break = 8 game hours = 8/24 = 0.333 game days
        assert days[1] == pytest.approx(1.0 + 8 / 24, abs=0.01)
        # c3: 300s after c2 within session = 300*20 = 6000 game seconds more
        assert days[2] == pytest.approx(1.0 + 8 / 24 + 300 * 20 / 86400, abs=0.01)

    def test_backfill_skips_conversations_with_game_days(self, db):
        """Conversations that already have game_days should not be modified."""
        db.conn.execute("INSERT INTO conversations (id, world_id, started_at, ended_at, game_days) VALUES (?, ?, ?, ?, ?)",
                       ("c1", "world1", 1000000.0, 1000600.0, 42.5))
        db.conn.commit()

        count = db.backfill_game_days()
        assert count == 0

        row = db.conn.execute("SELECT game_days FROM conversations WHERE id = 'c1'").fetchone()
        assert row[0] == 42.5  # Unchanged

    def test_backfill_returns_zero_when_no_conversations(self, db):
        count = db.backfill_game_days()
        assert count == 0

    def test_backfill_single_conversation_gets_day_one(self, db):
        db.conn.execute("INSERT INTO conversations (id, world_id, started_at, ended_at) VALUES (?, ?, ?, ?)",
                       ("c1", "world1", 1000000.0, 1000600.0))
        db.conn.commit()

        count = db.backfill_game_days()
        assert count == 1

        row = db.conn.execute("SELECT game_days FROM conversations WHERE id = 'c1'").fetchone()
        assert row[0] == pytest.approx(1.0, abs=0.01)

    def test_backfill_multiple_session_breaks(self, db):
        """Multiple session breaks should each add hours_per_break."""
        base_ts = 1000000.0
        # 4 conversations with 3 session breaks (each 2h apart)
        for i, offset in enumerate([0, 7200, 14400, 21600]):
            db.conn.execute("INSERT INTO conversations (id, world_id, started_at, ended_at) VALUES (?, ?, ?, ?)",
                           (f"c{i+1}", "world1", base_ts + offset, base_ts + offset + 300))
        db.conn.commit()

        count = db.backfill_game_days(timescale=20, session_gap=1800, hours_per_break=8)
        assert count == 4

        rows = db.conn.execute("SELECT id, game_days FROM conversations ORDER BY started_at").fetchall()
        days = [r[1] for r in rows]
        assert days[0] == pytest.approx(1.0, abs=0.01)
        # Each break adds 8 game hours = 1/3 day
        assert days[1] == pytest.approx(1.0 + 8 / 24, abs=0.01)
        assert days[2] == pytest.approx(1.0 + 16 / 24, abs=0.01)
        assert days[3] == pytest.approx(1.0 + 24 / 24, abs=0.01)  # = 2.0
