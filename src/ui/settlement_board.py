"""Settlement Public Board — browse NPC diary entries and memories.

Mounted as a Gradio app at /diary. Reads directly from the conversation DB.
"""
import os
import sqlite3
from pathlib import Path

import gradio as gr

from src.config.config_loader import ConfigLoader
import src.utils as utils

logger = utils.get_logger()


def _get_db_path(config: ConfigLoader) -> Path:
    game_folder = "Fallout4" if "fallout" in str(config.game).lower() else "Skyrim"
    return Path(config.save_folder) / "data" / game_folder / "conversations" / "conversations.db"


def _read_diary_entries(db_path: Path) -> list[dict]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("""
            SELECT npc_name, npc_ref_id, content, game_days_from, game_days_to, created_at
            FROM diary_entries
            ORDER BY game_days_to DESC
        """)
        return [dict(row) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _read_recent_summaries(db_path: Path) -> list[dict]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("""
            SELECT npc_name, npc_ref_id, content, from_ts, to_ts, created_at
            FROM summaries
            ORDER BY to_ts DESC
            LIMIT 50
        """)
        return [dict(row) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _get_npc_list(db_path: Path) -> list[str]:
    """Get unique NPC names that have diary entries or summaries."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    try:
        names = set()
        for table in ["diary_entries", "summaries"]:
            try:
                cur = conn.execute(f"SELECT DISTINCT npc_name FROM {table}")
                names.update(row[0] for row in cur.fetchall())
            except sqlite3.OperationalError:
                pass
        return sorted(names)
    finally:
        conn.close()


def _format_game_days(game_days: float) -> str:
    days = int(game_days)
    hours = int((game_days - days) * 24)
    time_12h = hours - 12 if hours > 12 else hours
    period = utils.get_time_group(hours) if hasattr(utils, 'get_time_group') else ""
    return f"Day {days}, {time_12h} {period}".strip()


def _build_board_html(db_path: Path, npc_filter: str = "All") -> str:
    diary_entries = _read_diary_entries(db_path)
    summaries = _read_recent_summaries(db_path)

    if npc_filter and npc_filter != "All":
        diary_entries = [d for d in diary_entries if d["npc_name"] == npc_filter]
        summaries = [s for s in summaries if s["npc_name"] == npc_filter]

    html = """<div style="font-family: 'Courier New', monospace; max-width: 800px; margin: 0 auto;">"""
    html += """<div style="text-align: center; margin-bottom: 30px; padding: 20px; border-bottom: 2px solid #4CAF50;">
        <h1 style="color: #4CAF50; margin: 0; font-size: 28px;">SETTLEMENT PUBLIC BOARD</h1>
        <p style="color: #888; margin: 5px 0 0 0; font-style: italic;">Community log — what your settlers have been up to</p>
    </div>"""

    if diary_entries:
        html += """<div style="margin-bottom: 30px;">
            <h2 style="color: #4CAF50; border-bottom: 1px solid #333; padding-bottom: 8px;">Diary Entries</h2>"""
        for entry in diary_entries:
            days_range = f"{_format_game_days(entry['game_days_from'])} — {_format_game_days(entry['game_days_to'])}"
            content_html = entry["content"].replace("\n", "<br>")
            html += f"""<div style="margin: 15px 0; padding: 15px; background: #1a1a1a; border-left: 3px solid #4CAF50; border-radius: 4px;">
                <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                    <strong style="color: #4CAF50; font-size: 16px;">{entry['npc_name']}</strong>
                    <span style="color: #666; font-size: 12px;">{days_range}</span>
                </div>
                <div style="color: #ccc; line-height: 1.6;">{content_html}</div>
            </div>"""
        html += "</div>"

    if summaries:
        html += """<div style="margin-bottom: 30px;">
            <h2 style="color: #4CAF50; border-bottom: 1px solid #333; padding-bottom: 8px;">Recent Memories</h2>
            <p style="color: #666; font-size: 12px; margin-top: 4px;">Not yet consolidated into diary entries</p>"""
        for s in summaries:
            content_html = s["content"].replace("\n", "<br>")
            html += f"""<div style="margin: 10px 0; padding: 12px; background: #111; border-left: 3px solid #555; border-radius: 4px;">
                <strong style="color: #888;">{s['npc_name']}</strong>
                <div style="color: #aaa; line-height: 1.5; margin-top: 5px;">{content_html}</div>
            </div>"""
        html += "</div>"

    if not diary_entries and not summaries:
        html += """<div style="text-align: center; padding: 40px; color: #666;">
            <p style="font-size: 18px;">The board is empty.</p>
            <p>NPCs will post diary entries after enough conversations and game time has passed.</p>
            <p style="font-size: 12px; margin-top: 20px;">Thresholds: 7 game-days + 3 conversations per NPC</p>
        </div>"""

    html += "</div>"
    return html


def create_settlement_board(config: ConfigLoader) -> gr.Blocks:
    db_path = _get_db_path(config)

    with gr.Blocks(
        title="Settlement Board",
        analytics_enabled=False,
        theme=gr.themes.Soft(
            primary_hue="green",
            secondary_hue="green",
            neutral_hue="zinc",
            font=['Courier New', 'monospace'],
        ),
        css="""
        .gradio-container { background: #0a0a0a !important; }
        .dark .gradio-container { background: #0a0a0a !important; }
        """
    ) as board:
        with gr.Row():
            npc_dropdown = gr.Dropdown(
                choices=["All"] + _get_npc_list(db_path),
                value="All",
                label="Filter by NPC",
                interactive=True,
            )
            refresh_btn = gr.Button("Refresh", size="sm")

        board_html = gr.HTML(value=_build_board_html(db_path))

        def update_board(npc_filter):
            fresh_db_path = _get_db_path(config)
            npcs = ["All"] + _get_npc_list(fresh_db_path)
            html = _build_board_html(fresh_db_path, npc_filter)
            return gr.update(choices=npcs, value=npc_filter), html

        npc_dropdown.change(
            fn=update_board,
            inputs=[npc_dropdown],
            outputs=[npc_dropdown, board_html],
        )
        refresh_btn.click(
            fn=update_board,
            inputs=[npc_dropdown],
            outputs=[npc_dropdown, board_html],
        )

    return board
