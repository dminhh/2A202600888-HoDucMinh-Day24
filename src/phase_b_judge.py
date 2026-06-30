from __future__ import annotations

"""Phase B: LLM-as-Judge — pairwise, swap-and-average, Cohen κ, bias analysis."""

import json
import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import OPENAI_API_KEY, JUDGE_MODEL, HUMAN_LABELS_PATH, TEST_SET_PATH


@dataclass
class JudgeResult:
    question: str
    answer_a: str
    answer_b: str
    winner_pass1: str       # "A" | "B" | "tie"  (original order)
    winner_pass2: str       # "A" | "B" | "tie"  (after swap, ALREADY converted back)
    final_winner: str       # consensus after swap-and-average
    reasoning_pass1: str
    reasoning_pass2: str
    position_consistent: bool  # True if both passes agree on same answer
    scores_pass1: dict = field(default_factory=dict)  # {"A": float, "B": float}
    scores_pass2: dict = field(default_factory=dict)


# ─── Task 5: Pairwise Judge ───────────────────────────────────────────────────

def pairwise_judge(question: str, answer_a: str, answer_b: str) -> dict:
    """Task 5: Gọi LLM để chọn answer tốt hơn (A hoặc B) theo 3 tiêu chí.

    Tiêu chí đánh giá:
        - Độ chính xác (accuracy): có khớp với thực tế chính sách không?
        - Độ đầy đủ (completeness): có trả lời đủ câu hỏi không?
        - Tính súc tích (conciseness): có thừa / thiếu thông tin không?

    Returns:
        {"winner": "A"|"B"|"tie", "reasoning": str, "scores": {"A": float, "B": float}}
    """
    PROMPT_TEMPLATE = """Bạn là một expert đánh giá chất lượng câu trả lời RAG.

Câu hỏi: {question}

Answer A:
{answer_a}

Answer B:
{answer_b}

Đánh giá dựa trên 3 tiêu chí: độ chính xác (accuracy), đầy đủ (completeness), súc tích (conciseness).
Trả lời JSON (chỉ JSON, không text khác):
{{"winner": "A" hoặc "B" hoặc "tie", "reasoning": "giải thích ngắn gọn", "scores": {{"A": 0.0-1.0, "B": 0.0-1.0}}}}
"""
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=JUDGE_MODEL,
        messages=[
            {"role": "system", "content": "Bạn là expert đánh giá RAG. Chỉ trả lời JSON."},
            {"role": "user",   "content": PROMPT_TEMPLATE.format(
                question=question, answer_a=answer_a, answer_b=answer_b)},
        ],
        response_format={"type": "json_object"},
    )
    result = json.loads(resp.choices[0].message.content)

    # Normalize winner value
    winner_raw = str(result.get("winner", "tie")).strip().upper()
    if winner_raw == "TIE":
        winner = "tie"
    elif winner_raw in ("A", "B"):
        winner = winner_raw
    else:
        winner = "tie"
    result["winner"] = winner

    # Ensure scores are floats in [0, 1]
    scores = result.get("scores", {})
    result["scores"] = {
        "A": max(0.0, min(1.0, float(scores.get("A", 0.5)))),
        "B": max(0.0, min(1.0, float(scores.get("B", 0.5)))),
    }

    # Ensure reasoning is a string
    result["reasoning"] = str(result.get("reasoning", ""))
    return result


# ─── Task 6: Swap-and-Average ─────────────────────────────────────────────────

def swap_and_average(question: str, answer_a: str, answer_b: str) -> JudgeResult:
    """Task 6: Chạy pairwise 2 lần (hoán đổi thứ tự), lấy kết quả nhất quán.

    Pass 1: judge(q, A, B) → winner_1
    Pass 2: judge(q, B, A) → winner_2_raw → convert back to A/B space
    Final:  đồng thuận → winner_1, khác nhau → "tie"
    """
    pass1 = pairwise_judge(question, answer_a, answer_b)
    pass2_raw = pairwise_judge(question, answer_b, answer_a)  # SWAP!

    # Convert pass2 result back to original A/B space
    swap_map = {"A": "B", "B": "A", "tie": "tie"}
    winner_pass2 = swap_map[pass2_raw["winner"]]

    # Final consensus: agree → winner_1, disagree → "tie"
    final = pass1["winner"] if pass1["winner"] == winner_pass2 else "tie"
    position_consistent = (pass1["winner"] == winner_pass2)

    # scores_pass2: swap A/B scores back to original space
    scores_pass2 = {
        "A": pass2_raw["scores"]["B"],
        "B": pass2_raw["scores"]["A"],
    }

    return JudgeResult(
        question=question,
        answer_a=answer_a,
        answer_b=answer_b,
        winner_pass1=pass1["winner"],
        winner_pass2=winner_pass2,
        final_winner=final,
        reasoning_pass1=pass1["reasoning"],
        reasoning_pass2=pass2_raw["reasoning"],
        position_consistent=position_consistent,
        scores_pass1=pass1["scores"],
        scores_pass2=scores_pass2,
    )


# ─── Task 7: Cohen's κ ────────────────────────────────────────────────────────

def cohen_kappa(judge_labels: list[int], human_labels: list[int]) -> float:
    """Task 7: Tính Cohen's κ giữa LLM judge và human labels.

    Returns:
        κ ∈ [-1, 1].  Perfect agreement → 1.0
    """
    from sklearn.metrics import cohen_kappa_score
    return float(cohen_kappa_score(human_labels, judge_labels))


# ─── Task 8: Bias Report ──────────────────────────────────────────────────────

def bias_report(judge_results: list[JudgeResult]) -> dict:
    """Task 8: Đo lường position bias và verbosity bias.

    Returns:
        {
          "total_judged": int,
          "position_bias_rate": float,
          "position_bias_count": int,
          "verbosity_bias": float,
          "verbosity_details": {"a_wins_a_longer": int, "b_wins_b_longer": int, "total_decisive": int},
          "interpretation": str,
        }
    """
    total = len(judge_results)
    if total == 0:
        return {
            "total_judged": 0,
            "position_bias_rate": 0.0,
            "position_bias_count": 0,
            "verbosity_bias": 0.0,
            "verbosity_details": {"a_wins_a_longer": 0, "b_wins_b_longer": 0, "total_decisive": 0},
            "interpretation": "Không có dữ liệu.",
        }

    position_bias_count = sum(1 for r in judge_results if not r.position_consistent)
    position_bias_rate  = position_bias_count / total

    a_wins_a_longer = sum(
        1 for r in judge_results
        if r.final_winner == "A" and len(r.answer_a) > len(r.answer_b)
    )
    b_wins_b_longer = sum(
        1 for r in judge_results
        if r.final_winner == "B" and len(r.answer_b) > len(r.answer_a)
    )
    decisive = sum(1 for r in judge_results if r.final_winner != "tie")
    verbosity_bias = (a_wins_a_longer + b_wins_b_longer) / decisive if decisive > 0 else 0.0

    interpretation = (
        "Position bias cao — nên dùng swap-and-average."
        if position_bias_rate > 0.3
        else "Position bias thấp — judge ổn định."
    )

    return {
        "total_judged": total,
        "position_bias_rate": round(position_bias_rate, 3),
        "position_bias_count": position_bias_count,
        "verbosity_bias": round(verbosity_bias, 3),
        "verbosity_details": {
            "a_wins_a_longer": a_wins_a_longer,
            "b_wins_b_longer": b_wins_b_longer,
            "total_decisive": decisive,
        },
        "interpretation": interpretation,
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Load human labels (10 questions)
    with open(HUMAN_LABELS_PATH, encoding="utf-8") as f:
        human_data = json.load(f)
    human_labels = [item["human_label"] for item in human_data]
    print(f"Loaded {len(human_labels)} human-labeled questions")

    # Load ground_truths from test_set_50q for comparison
    with open(TEST_SET_PATH, encoding="utf-8") as f:
        test_set = json.load(f)
    gt_by_id = {item["id"]: item["ground_truth"] for item in test_set}

    # Run swap-and-average for each human-labeled question
    # Compare model_answer (A) vs ground_truth (B).
    # Judge label dùng score_A >= 0.5 (không phải winner) vì ground_truth
    # luôn "thắng" về hình thức nhưng model_answer vẫn có thể chất lượng tốt.
    # score_A cao (>=0.5) → model answer đủ chất lượng → judge_label=1
    judge_results: list[JudgeResult] = []
    judge_labels: list[int] = []

    print("\nRunning swap-and-average judge on 10 human-labeled questions...")
    for item in human_data:
        q    = item["question"]
        a_a  = item["model_answer"]
        a_b  = gt_by_id.get(item["question_id"], "Không tìm thấy thông tin.")

        print(f"  Q{item['question_id']}: {q[:60]}...")
        result = swap_and_average(q, a_a, a_b)
        judge_results.append(result)

        # Dùng score_A (averaged from both passes) để đánh giá chất lượng model answer
        avg_score_a = (
            result.scores_pass1.get("A", 0.5) + result.scores_pass2.get("A", 0.5)
        ) / 2
        judge_label = 1 if avg_score_a > 0.5 else 0
        judge_labels.append(judge_label)
        print(f"    Pass1={result.winner_pass1} Pass2={result.winner_pass2} "
              f"Final={result.final_winner} score_A={avg_score_a:.2f} "
              f"→ judge_label={judge_label} human_label={item['human_label']}")

    # Cohen's κ
    kappa = cohen_kappa(judge_labels, human_labels)
    print(f"\nCohen's κ = {kappa:.3f}")
    if kappa > 0.6:
        print("✓ BONUS: κ > 0.6 (substantial agreement)")

    # Bias report
    bias = bias_report(judge_results)
    print(f"\nBias report:")
    print(f"  Position bias rate: {bias['position_bias_rate']:.1%}")
    print(f"  Verbosity bias:     {bias['verbosity_bias']:.1%}")
    print(f"  {bias['interpretation']}")

    # Save report
    os.makedirs("reports", exist_ok=True)
    report = {
        "num_judged": len(judge_results),
        "cohen_kappa": round(kappa, 4),
        "judge_labels": judge_labels,
        "human_labels": human_labels,
        "bias_report": bias,
        "judge_results": [
            {
                "question": r.question,
                "winner_pass1": r.winner_pass1,
                "winner_pass2": r.winner_pass2,
                "final_winner": r.final_winner,
                "position_consistent": r.position_consistent,
                "reasoning_pass1": r.reasoning_pass1,
            }
            for r in judge_results
        ],
    }
    with open("reports/judge_results.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\nPhase B report saved → reports/judge_results.json")
