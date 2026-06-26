# Copyright 2026 Google LLC
import os
import json
from tests.eval.llm_judge import run_evaluation


def main():
    print("Starting TwoKeys Agent Evaluation Pipeline...")
    output = run_evaluation()
    results = output["results"]
    summary = output["summary"]

    # Save to eval_results.json
    results_path = os.path.join(os.path.dirname(__file__), "eval_results.json")
    with open(results_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved structured results to {results_path}")

    # Generate human-readable report
    report_path = os.path.join(os.path.dirname(__file__), "eval_report.txt")
    with open(report_path, "w") as f:
        f.write("="*80 + "\n")
        f.write("                      TWOKEYS AGENT EVALUATION REPORT\n")
        f.write("="*80 + "\n\n")
        
        f.write("METRICS SUMMARY\n")
        f.write("-" * 40 + "\n")
        f.write(f"Total Test Cases:            {summary['total_cases']}\n")
        f.write(f"Overall Pass Rate:           {summary['pass_rate']:.1f}%\n")
        f.write(f"Average Reasoning Score:     {summary['average_reasoning_score']:.2f}/5.00\n")
        f.write(f"Adversarial Catch Rate:      {summary['catch_rate_adversarial']:.1f}%\n")
        f.write("-" * 40 + "\n\n")

        f.write("TEST CASE DETAILS\n")
        f.write("="*80 + "\n")
        f.write(f"{'ID':<4} | {'Category':<28} | {'Expected':<10} | {'Actual':<10} | {'Correct':<8} | {'Reasoning':<9}\n")
        f.write("-" * 80 + "\n")
        for r in results:
            f.write(f"{r['id']:<4} | {r['category']:<28} | {r['expected_outcome']:<10} | {r['actual_outcome']:<10} | {r['correct']:<8} | {r['reasoning_score']:<9}\n")
        f.write("="*80 + "\n\n")

        f.write("DETAILED NOTES & EVALUATIONS\n")
        f.write("-" * 80 + "\n")
        for r in results:
            f.write(f"Case {r['id']} ({r['category']}):\n")
            f.write(f"  Input: {r['input']}\n")
            f.write(f"  Expected: {r['expected_outcome']} | Actual: {r['actual_outcome']}\n")
            f.write(f"  Correct: {r['correct']} | Reasoning Score: {r['reasoning_score']}/5\n")
            f.write(f"  Judge Notes: {r['notes']}\n")
            f.write("-" * 80 + "\n")

    print(f"Saved human-readable report to {report_path}")
    print("Evaluation completed successfully.")


if __name__ == "__main__":
    main()
