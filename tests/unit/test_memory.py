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

import os
import pytest
from app.memory import init_db, save_decision, get_vendor_history


def test_memory_persistence(tmp_path, monkeypatch) -> None:
    # Set a temporary database path
    db_file = tmp_path / "test_memory.db"
    monkeypatch.setenv("TWOKEYS_DB_PATH", str(db_file))

    # 1. Test init_db
    init_db()
    assert db_file.exists()

    # 2. Test saving first decision (approved)
    save_decision(
        request_id="req-1",
        vendor="TestVendor",
        amount=150.0,
        description="First purchase",
        builder_decision={
            "action": "approve",
            "amount": 150.0,
            "reasoning": "Looks good",
        },
        auditor_verdict={"verdict": "approve", "reasoning": "Agreed"},
        final_outcome="approved",
    )

    # 3. Test get_vendor_history
    hist = get_vendor_history("TestVendor")
    assert hist["total_requests"] == 1
    assert hist["past_rejections"] == 0
    assert hist["most_recent_outcome"] == "approved"
    assert "1 total requests" in hist["summary"]
    assert "0 rejections" in hist["summary"]

    # 4. Save a second decision (rejected)
    save_decision(
        request_id="req-2",
        vendor="TestVendor",
        amount=250.0,
        description="Second purchase",
        builder_decision={
            "action": "deny",
            "amount": 250.0,
            "reasoning": "Policy violation",
        },
        auditor_verdict={"verdict": "deny", "reasoning": "Agreed"},
        final_outcome="rejected",
    )

    hist = get_vendor_history("TestVendor")
    assert hist["total_requests"] == 2
    assert hist["past_rejections"] == 1
    assert hist["most_recent_outcome"] == "rejected"
    assert "2 total requests" in hist["summary"]
    assert "1 rejections" in hist["summary"]

    # 5. Overwrite the first one to test replacement/updates
    save_decision(
        request_id="req-1",
        vendor="TestVendor",
        amount=150.0,
        description="First purchase",
        builder_decision={
            "action": "approve",
            "amount": 150.0,
            "reasoning": "Looks good",
        },
        auditor_verdict={"verdict": "approve", "reasoning": "Agreed"},
        final_outcome="rejected",  # Change to rejected
    )

    hist = get_vendor_history("TestVendor")
    # Total count should still be 2 (since req-1 was overwritten/replaced)
    assert hist["total_requests"] == 2
    # But rejections should now be 2
    assert hist["past_rejections"] == 2
