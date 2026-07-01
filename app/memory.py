# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
import sqlite3


def get_db_path() -> str:
    """Returns the database file path, allowing dynamic overrides.

    Reads from TWOKEYS_DB_PATH so the dashboard, the local batch runner, and
    the live agent workflow can all point at the same database without any
    of them hardcoding a path — useful for keeping a separate eval database
    isolated from production decision history during testing.
    """
    return os.environ.get("TWOKEYS_DB_PATH", "twokeys_memory.db")


def init_db() -> None:
    """Initialize the SQLite database and create the table if it doesn't exist.

    SQLite was chosen deliberately over a vector store for this skill.
    Vendor history is structured, relational data (counts, timestamps,
    categorical outcomes) — a vector store would add an external dependency
    and embedding overhead for data that a simple SQL query already serves
    efficiently and deterministically.
    """
    conn = sqlite3.connect(get_db_path())
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS expense_decisions (
            request_id TEXT PRIMARY KEY,
            timestamp TEXT,
            vendor TEXT,
            amount REAL,
            description TEXT,
            builder_decision TEXT,
            auditor_verdict TEXT,
            final_outcome TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_decision(
    request_id: str,
    vendor: str,
    amount: float,
    description: str,
    builder_decision: dict | None,
    auditor_verdict: dict | None,
    final_outcome: str,
) -> None:
    """Inserts or replaces an expense decision record in the database.

    This is the single write path for every decision the system makes,
    called from every terminal node in the workflow (approved, rejected,
    and escalated). Both agents' full reasoning is persisted alongside the
    outcome, not just the final verdict — this is what makes every decision
    in the dashboard fully explainable after the fact, not just logged as a
    bare approve/deny.
    """
    init_db()
    conn = sqlite3.connect(get_db_path())
    cursor = conn.cursor()

    builder_json = json.dumps(builder_decision) if builder_decision else None
    auditor_json = json.dumps(auditor_verdict) if auditor_verdict else None
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

    cursor.execute(
        """
        INSERT OR REPLACE INTO expense_decisions
        (request_id, timestamp, vendor, amount, description, builder_decision, auditor_verdict, final_outcome)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            request_id,
            timestamp,
            vendor,
            amount,
            description,
            builder_json,
            auditor_json,
            final_outcome,
        ),
    )
    conn.commit()
    conn.close()


def get_vendor_history(vendor: str) -> dict:
    """Queries the database to return a short summary of a vendor's history.

    This is the core "agent skill": rather than handing the Auditor agent
    raw database rows (which would burn tokens and add noise the model has
    to parse), this function pre-aggregates the vendor's history into a
    single natural-language sentence the Auditor's prompt can use directly.
    The Auditor's instructions explicitly tell it to apply extra scrutiny
    when past_rejections >= 2 — meaning this summary actively changes agent
    behavior on repeat offenders, not just logs history for a human to read
    later. This is long-term memory functioning as a skill, not just
    storage.
    """
    init_db()
    conn = sqlite3.connect(get_db_path())
    cursor = conn.cursor()

    # Total past requests
    cursor.execute(
        "SELECT COUNT(*) FROM expense_decisions WHERE vendor = ?", (vendor,)
    )
    total_requests = cursor.fetchone()[0]

    # Past rejections
    cursor.execute(
        "SELECT COUNT(*) FROM expense_decisions WHERE vendor = ? AND final_outcome = 'rejected'",
        (vendor,),
    )
    past_rejections = cursor.fetchone()[0]

    # Most recent outcome
    cursor.execute(
        "SELECT final_outcome FROM expense_decisions WHERE vendor = ? ORDER BY timestamp DESC LIMIT 1",
        (vendor,),
    )
    row = cursor.fetchone()
    most_recent_outcome = row[0] if row else "None"

    conn.close()

    if total_requests == 0:
        summary = f"Vendor '{vendor}' has no past requests."
    else:
        summary = (
            f"Vendor '{vendor}' history: {total_requests} total requests, "
            f"{past_rejections} rejections. Most recent outcome: {most_recent_outcome}."
        )

    return {
        "total_requests": total_requests,
        "past_rejections": past_rejections,
        "most_recent_outcome": most_recent_outcome,
        "summary": summary,
    }
