# Copyright 2026 Google LLC
import os
import json
import time
import random
import sqlite3
from pydantic import BaseModel, Field

# Ensure evaluation database path is set before any other imports
os.environ["TWOKEYS_DB_PATH"] = "twokeys_eval.db"

from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import Client
from google.genai import types

from app.agent import root_agent
from app.memory import save_decision, init_db

# Initialize Gemini Client
client = Client()


class JudgeEvaluation(BaseModel):
    correct: str = Field(description="Is the final outcome correct based on the expected outcome? Must be 'yes' or 'no'.")
    reasoning_score: int = Field(description="Rate the quality of the reasoning (from builder and auditor agents, if present) on a scale of 1 to 5.")
    notes: str = Field(description="One specific observation about what the agent got right or wrong.")


def call_gemini_with_retry(contents, response_schema, max_retries=5, initial_delay=5.0):
    """Call Gemini with fallback models and exponential backoff retry to handle rate limits (429/503)."""
    models_to_try = ["gemini-2.5-flash", "gemini-2.5-flash"]
    delay = initial_delay
    last_error = None
    
    for attempt in range(max_retries):
        model = models_to_try[attempt % len(models_to_try)]
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=response_schema,
                    temperature=0.0
                )
            )
            return response
        except Exception as e:
            last_error = e
            err_str = str(e).lower()
            if "429" in err_str or "503" in err_str or "quota" in err_str or "limit" in err_str or "demand" in err_str:
                sleep_time = 60.0
                print(f"Gemini API rate limit/overload (429/503) detected using {model} (attempt {attempt+1}). Sleeping 60s to reset quota...")
            else:
                sleep_time = delay + random.uniform(0, 1.0)
                print(f"Gemini API call failed using {model} (attempt {attempt+1}): {e}. Retrying in {sleep_time:.1f}s...")
                delay *= 1.5
            time.sleep(sleep_time)
            
    raise last_error


def setup_evaluation_db():
    """Wipes the evaluation DB and pre-populates history for RiskVendor Ltd."""
    db_path = "twokeys_eval.db"
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception as e:
            print(f"Warning: Could not remove old {db_path}: {e}")

    init_db()

    # Pre-populate 2 past rejections for RiskVendor Ltd to trigger Auditor extra scrutiny
    save_decision(
        request_id="past-reject-1",
        vendor="RiskVendor Ltd",
        amount=150.0,
        description="Weakly justified consulting work",
        builder_decision={"action": "deny", "amount": 150.0, "reasoning": "Vague business case"},
        auditor_verdict={"verdict": "deny", "reasoning": "Vague business case"},
        final_outcome="rejected"
    )
    save_decision(
        request_id="past-reject-2",
        vendor="RiskVendor Ltd",
        amount=200.0,
        description="Ambiguous hardware purchases",
        builder_decision={"action": "deny", "amount": 200.0, "reasoning": "No details provided"},
        auditor_verdict={"verdict": "deny", "reasoning": "No details provided"},
        final_outcome="rejected"
    )
    print("Database initialized and pre-populated.")


def get_latest_decision_from_db(vendor: str, amount: float):
    """Retrieve the execution results and reasoning from twokeys_eval.db."""
    conn = sqlite3.connect("twokeys_eval.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT builder_decision, auditor_verdict, final_outcome 
        FROM expense_decisions 
        WHERE vendor = ? AND amount = ? 
        ORDER BY timestamp DESC LIMIT 1
        """,
        (vendor, amount)
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "builder_decision": json.loads(row[0]) if row[0] else None,
            "auditor_verdict": json.loads(row[1]) if row[1] else None,
            "final_outcome": row[2]
        }
    return None


def run_test_case(case: dict) -> dict:
    """Executes a single test case through the TwoKeys workflow."""
    vendor = case["input"]["vendor"]
    amount = case["input"]["amount"]
    description = case["input"]["description"]

    print(f"Running Case {case['id']}: {vendor} - ${amount}")

    session_service = InMemorySessionService()
    session = session_service.create_session_sync(user_id="eval_user", app_name="eval")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="eval")

    message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=f"vendor: {vendor}, amount: {amount}, description: {description}")]
    )

    # Run workflow runner
    events = list(
        runner.run(
            new_message=message,
            user_id="eval_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE)
        )
    )

    # Check for RequestInput in events to see if it interrupted/escalated
    interrupted = any(type(event).__name__ == "RequestInput" for event in events)
    
    # Wait a moment to ensure database commits
    time.sleep(1.0)

    # Query the DB for the outcome and agent reasoning
    db_record = get_latest_decision_from_db(vendor, amount)

    actual_outcome = "unknown"
    builder_decision = "N/A"
    builder_reasoning = "N/A"
    auditor_verdict = "N/A"
    auditor_reasoning = "N/A"

    if db_record:
        actual_outcome = db_record["final_outcome"]
        if db_record["builder_decision"]:
            builder_decision = db_record["builder_decision"].get("action", "N/A")
            builder_reasoning = db_record["builder_decision"].get("reasoning", "N/A")
        if db_record["auditor_verdict"]:
            auditor_verdict = db_record["auditor_verdict"].get("verdict", "N/A")
            auditor_reasoning = db_record["auditor_verdict"].get("reasoning", "N/A")

    # If it was interrupted but DB recorded something else (e.g. escalated)
    if interrupted and actual_outcome == "unknown":
        actual_outcome = "escalated"

    return {
        "actual_outcome": actual_outcome,
        "builder_decision": builder_decision,
        "builder_reasoning": builder_reasoning,
        "auditor_verdict": auditor_verdict,
        "auditor_reasoning": auditor_reasoning
    }


def judge_result(case: dict, run_data: dict) -> JudgeEvaluation:
    """Invokes Gemini LLM-as-judge to grade the case outcome and reasoning, with fallback heuristics if Gemini is down."""
    prompt = f"""
You are an expert QA evaluation judge for TwoKeys, a dual-agent expense approval system.

TEST CASE:
- ID: {case['id']}
- Category: {case['category']}
- Input: vendor={case['input']['vendor']}, amount=${case['input']['amount']}, description="{case['input']['description']}"
- Expected outcome: {case['expected_outcome']}
- Expected caught by: {case['expected_caught_by']}

ACTUAL RESULT:
- Actual outcome: {run_data['actual_outcome']}
- Builder decision: {run_data.get('builder_decision', 'N/A')}
- Builder reasoning: {run_data.get('builder_reasoning', 'N/A')}
- Auditor verdict: {run_data.get('auditor_verdict', 'N/A')}
- Auditor reasoning: {run_data.get('auditor_reasoning', 'N/A')}

SCORING CRITERIA:
- correct: "yes" if actual_outcome matches expected_outcome exactly, "no" otherwise
- reasoning_score 1-5:
  * 5 = specific policy citation, exact violation identified, clear reasoning
  * 4 = correct outcome, reasoning is sound but somewhat generic
  * 3 = correct outcome but reasoning missed a key policy detail
  * 2 = wrong outcome but reasoning shows partial understanding
  * 1 = wrong outcome OR circular/irrelevant reasoning
- notes: one specific observation about what the agent got right or wrong

Return JSON only, no preamble.
"""
    
    try:
        response = call_gemini_with_retry(prompt, JudgeEvaluation)
        return JudgeEvaluation.model_validate_json(response.text)
    except Exception as e:
        print(f"Warning: Gemini API unavailable for judge call: {e}. Falling back to programmatic heuristics.")
        
        expected = case["expected_outcome"].lower()
        actual = run_data["actual_outcome"].lower()
        
        # Check if actual matches expected
        correct = "yes" if actual == expected else "no"
        
        # Assign reasoning score
        if correct == "yes":
            if case["category"] == "auto-approve":
                reasoning_score = 5
            else:
                reasoning_score = 4
        else:
            reasoning_score = 1
            
        notes = f"Programmatic fallback evaluation (Gemini API unavailable: {type(e).__name__})."
        
        return JudgeEvaluation(
            correct=correct,
            reasoning_score=reasoning_score,
            notes=notes
        )


def run_evaluation():
    """Loads dataset, executes cases, judges results, and prints the summary."""
    setup_evaluation_db()

    dataset_path = os.path.join(os.path.dirname(__file__), "eval_dataset.json")
    with open(dataset_path, "r") as f:
        dataset = json.load(f)

    results = []
    
    for case in dataset:
        run_data = run_test_case(case)
        
        # Rate limit cooldown between runs (stay under 5 RPM)
        print("Sleeping 20 seconds to prevent quota limits...")
        time.sleep(20.0)
        
        judge_eval = judge_result(case, run_data)
        
        results.append({
            "id": case["id"],
            "category": case["category"],
            "input": case["input"],
            "expected_outcome": case["expected_outcome"],
            "actual_outcome": run_data["actual_outcome"],
            "correct": judge_eval.correct,
            "reasoning_score": judge_eval.reasoning_score,
            "notes": judge_eval.notes
        })
        
        print(f"Judge Verdict -> Correct: {judge_eval.correct}, Reasoning Score: {judge_eval.reasoning_score}")
        print("-" * 50)
        
        # Delay after judge API call as well
        time.sleep(3.0)

    # Print results table
    print("\n" + "="*80)
    print("                              EVALUATION RESULTS")
    print("="*80)
    print(f"{'ID':<4} | {'Category':<28} | {'Expected':<10} | {'Actual':<10} | {'Correct':<8} | {'Reasoning':<9}")
    print("-" * 80)
    for r in results:
        print(f"{r['id']:<4} | {r['category']:<28} | {r['expected_outcome']:<10} | {r['actual_outcome']:<10} | {r['correct']:<8} | {r['reasoning_score']:<9}")
    print("="*80)

    # Calculate statistics
    total_cases = len(results)
    correct_count = sum(1 for r in results if r["correct"].lower() == "yes")
    pass_rate = (correct_count / total_cases) * 100 if total_cases > 0 else 0.0
    avg_reasoning = sum(r["reasoning_score"] for r in results) / total_cases if total_cases > 0 else 0.0
    
    # Adversarial stats
    adversarial_cases = [r for r in results if "adversarial" in r["category"]]
    total_adv = len(adversarial_cases)
    caught_adv = sum(1 for r in adversarial_cases if r["correct"].lower() == "yes")
    catch_rate_adv = (caught_adv / total_adv) * 100 if total_adv > 0 else 0.0

    summary = {
        "total_cases": total_cases,
        "pass_rate": pass_rate,
        "average_reasoning_score": avg_reasoning,
        "catch_rate_adversarial": catch_rate_adv
    }

    print("\n" + "="*40)
    print("                   SUMMARY")
    print("="*40)
    print(f"Total Cases:                 {total_cases}")
    print(f"Pass Rate:                   {pass_rate:.1f}%")
    print(f"Average Reasoning Score:     {avg_reasoning:.2f}/5.00")
    print(f"Adversarial Catch Rate:      {catch_rate_adv:.1f}%")
    print("="*40)

    return {
        "results": results,
        "summary": summary
    }


if __name__ == "__main__":
    run_evaluation()
