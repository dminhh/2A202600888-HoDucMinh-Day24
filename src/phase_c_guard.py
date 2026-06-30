from __future__ import annotations

"""Phase C: Production Guardrails — Presidio PII + NeMo Guardrails + P95 Latency."""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import ADVERSARIAL_SET_PATH, GUARDRAILS_CONFIG_DIR, LATENCY_BUDGET_P95_MS, PRESIDIO_LANGUAGE


# ─── Task 9a: Presidio PII Detection ─────────────────────────────────────────

def setup_presidio():
    """Khởi tạo Presidio engine với custom Vietnamese PII recognizers. (Đã implement sẵn)"""
    from presidio_analyzer import AnalyzerEngine, RecognizerRegistry, Pattern, PatternRecognizer
    from presidio_anonymizer import AnonymizerEngine

    cccd_recognizer = PatternRecognizer(
        supported_entity="VN_CCCD",
        patterns=[
            Pattern("CCCD 12 digits", r"\b\d{12}\b", 0.9),
            Pattern("CMND 9 digits",  r"\b\d{9}\b",  0.7),
        ],
    )
    phone_recognizer = PatternRecognizer(
        supported_entity="VN_PHONE",
        patterns=[Pattern("VN mobile", r"\b0[3-9]\d{8}\b", 0.9)],
    )

    registry = RecognizerRegistry()
    registry.load_predefined_recognizers()
    registry.add_recognizer(cccd_recognizer)
    registry.add_recognizer(phone_recognizer)

    analyzer  = AnalyzerEngine(registry=registry)
    anonymizer = AnonymizerEngine()
    return analyzer, anonymizer


def pii_scan(text: str, analyzer=None, anonymizer=None) -> dict:
    """Task 9a: Quét PII trong văn bản bằng Presidio.

    Returns:
        {
          "has_pii":    bool,
          "entities":   [{"type": str, "text": str, "score": float, "start": int, "end": int}],
          "anonymized": str,
        }
    """
    if analyzer is None or anonymizer is None:
        analyzer, anonymizer = setup_presidio()

    # Chỉ detect các entity types cần thiết — tránh false positive PERSON/LOCATION
    # trên văn bản tiếng Việt khi dùng en_core_web_lg
    target_entities = ["VN_CCCD", "VN_PHONE", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD"]
    results = analyzer.analyze(text=text, language=PRESIDIO_LANGUAGE, entities=target_entities)
    if not results:
        return {"has_pii": False, "entities": [], "anonymized": text}

    anonymized = anonymizer.anonymize(text=text, analyzer_results=results).text
    entities = [
        {
            "type":  r.entity_type,
            "text":  text[r.start:r.end],
            "score": round(r.score, 3),
            "start": r.start,
            "end":   r.end,
        }
        for r in results
    ]
    return {"has_pii": True, "entities": entities, "anonymized": anonymized}


# ─── Task 9b + 11: NeMo Guardrails ───────────────────────────────────────────

def setup_nemo_rails():
    """Khởi tạo NeMo Guardrails từ guardrails/config.yml. (Đã implement sẵn)"""
    from nemoguardrails import RailsConfig, LLMRails
    config = RailsConfig.from_path(GUARDRAILS_CONFIG_DIR)
    rails  = LLMRails(config)
    return rails


async def check_input_rail(text: str, rails=None) -> dict:
    """Task 9b: Kiểm tra input qua NeMo input rails (topic guard + jailbreak guard).

    Sử dụng NeMo's check_async để phát hiện nếu input bị block bởi input rails.

    Returns:
        {
          "allowed":        bool,
          "blocked_reason": str | None,
          "response":       str,
        }
    """
    if rails is None:
        rails = setup_nemo_rails()

    try:
        from nemoguardrails.rails.llm.options import RailType, RailStatus
        result = await rails.check_async(
            messages=[{"role": "user", "content": text}],
            rail_types=[RailType.INPUT],
        )
        blocked = result.status == RailStatus.BLOCKED
        blocked_reason = f"nemo_input_rail:{result.rail}" if blocked else None
        return {
            "allowed":        not blocked,
            "blocked_reason": blocked_reason,
            "response":       result.content or "",
        }
    except Exception as e:
        print(f"  ⚠️  NeMo input rail check failed: {e}")
        # Fallback: treat as allowed
        return {"allowed": True, "blocked_reason": None, "response": ""}


async def check_output_rail(question: str, answer: str, rails=None) -> dict:
    """Task 11: Kiểm tra LLM output qua NeMo output rails trước khi trả về user.

    Dùng Presidio để scan output PII, đồng thời kiểm tra NeMo output rail.

    Returns:
        {
          "safe":           bool,
          "flagged_reason": str | None,
          "final_answer":   str,
        }
    """
    if rails is None:
        rails = setup_nemo_rails()

    # Check output for PII using Presidio (fast, reliable)
    try:
        pii_result = pii_scan(answer)
        if pii_result["has_pii"]:
            return {
                "safe":           False,
                "flagged_reason": "output_contains_pii",
                "final_answer":   pii_result["anonymized"],
            }
    except Exception as e:
        print(f"  ⚠️  Presidio output scan failed: {e}")

    # Check output with NeMo output rail
    try:
        from nemoguardrails.rails.llm.options import RailType, RailStatus
        result = await rails.check_async(
            messages=[
                {"role": "user",      "content": question},
                {"role": "assistant", "content": answer},
            ],
            rail_types=[RailType.OUTPUT],
        )
        flagged = result.status == RailStatus.BLOCKED
        return {
            "safe":           not flagged,
            "flagged_reason": "nemo_output_rail" if flagged else None,
            "final_answer":   result.content if (flagged and result.content) else answer,
        }
    except Exception as e:
        print(f"  ⚠️  NeMo output rail check failed: {e}")
        return {"safe": True, "flagged_reason": None, "final_answer": answer}


# ─── Task 10: Adversarial Test Suite ─────────────────────────────────────────

def run_adversarial_suite(adversarial_set: list[dict], rails=None,
                           analyzer=None, anonymizer=None) -> list[dict]:
    """Task 10: Chạy 20 adversarial inputs qua full guard stack, so sánh với expected.

    Guard stack order:
        1. pii_scan()         → block nếu has_pii
        2. check_input_rail() → block nếu jailbreak / off-topic / prompt injection

    Returns:
        list of {"id", "category", "input", "expected", "actual", "blocked_by", "passed"}
    """
    if analyzer is None or anonymizer is None:
        analyzer, anonymizer = setup_presidio()
    if rails is None:
        rails = setup_nemo_rails()

    async def _run_all() -> list[dict]:
        results = []
        for item in adversarial_set:
            blocked_by = None

            # Layer 1: Presidio PII (synchronous, fast)
            try:
                pii_result = pii_scan(item["input"], analyzer, anonymizer)
                if pii_result["has_pii"]:
                    blocked_by = "presidio"
            except Exception as e:
                print(f"  ⚠️  Presidio error on item {item['id']}: {e}")

            # Layer 2: NeMo input rail (async)
            if blocked_by is None:
                try:
                    rail_result = await check_input_rail(item["input"], rails)
                    if not rail_result["allowed"]:
                        blocked_by = "nemo_input"
                except Exception as e:
                    print(f"  ⚠️  NeMo error on item {item['id']}: {e}")

            actual = "blocked" if blocked_by else "allowed"
            results.append({
                "id":         item["id"],
                "category":   item["category"],
                "input":      item["input"][:80] + ("..." if len(item["input"]) > 80 else ""),
                "expected":   item["expected"],
                "actual":     actual,
                "blocked_by": blocked_by,
                "passed":     actual == item["expected"],
            })
        return results

    results = asyncio.run(_run_all())
    passed = sum(1 for r in results if r["passed"])
    print(f"Adversarial suite: {passed}/{len(results)} passed")
    return results


# ─── Task 12: P95 Latency Measurement ────────────────────────────────────────

def measure_p95_latency(test_inputs: list[str], n_runs: int = 20,
                         rails=None, analyzer=None, anonymizer=None) -> dict:
    """Task 12: Đo P50/P95/P99 latency cho từng layer trong guard stack.

    Mục tiêu production: P95 total < LATENCY_BUDGET_P95_MS (500ms).

    Returns:
        {
          "presidio_ms":  {"p50": float, "p95": float, "p99": float},
          "nemo_ms":      {"p50": float, "p95": float, "p99": float},
          "total_ms":     {"p50": float, "p95": float, "p99": float},
          "latency_budget_ok": bool,
          "budget_ms": int,
        }
    """
    if analyzer is None or anonymizer is None:
        analyzer, anonymizer = setup_presidio()
    if rails is None:
        rails = setup_nemo_rails()

    presidio_times: list[float] = []
    nemo_times: list[float] = []
    total_times: list[float] = []

    async def _measure() -> None:
        inputs = (test_inputs * ((n_runs // len(test_inputs)) + 1))[:n_runs]
        for text in inputs:
            # Presidio (synchronous) — wrap in executor for timing
            t0 = time.perf_counter()
            pii_scan(text, analyzer, anonymizer)
            presidio_ms = (time.perf_counter() - t0) * 1000

            # NeMo input rail (async)
            t1 = time.perf_counter()
            await check_input_rail(text, rails)
            nemo_ms = (time.perf_counter() - t1) * 1000

            presidio_times.append(presidio_ms)
            nemo_times.append(nemo_ms)
            total_times.append(presidio_ms + nemo_ms)

    asyncio.run(_measure())

    def percentiles(times: list[float]) -> dict:
        s = sorted(times)
        n = len(s)
        return {
            "p50": round(s[int(n * 0.50)], 2),
            "p95": round(s[int(n * 0.95)], 2),
            "p99": round(s[min(int(n * 0.99), n - 1)], 2),
        }

    total_p = percentiles(total_times)
    return {
        "presidio_ms":       percentiles(presidio_times),
        "nemo_ms":           percentiles(nemo_times),
        "total_ms":          total_p,
        "latency_budget_ok": total_p["p95"] < LATENCY_BUDGET_P95_MS,
        "budget_ms":         LATENCY_BUDGET_P95_MS,
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Setup shared instances
    print("Setting up Presidio...")
    analyzer, anonymizer = setup_presidio()

    print("Setting up NeMo Guardrails...")
    rails = setup_nemo_rails()

    # Task 9a: PII scan demo
    print("\n[Task 9a] PII scan demo:")
    test_pii = "Nhân viên Nguyễn Văn A, CCCD 034095001234, SĐT 0987654321 hỏi về nghỉ phép."
    result = pii_scan(test_pii, analyzer, anonymizer)
    print(f"  PII detected: {result['has_pii']}")
    print(f"  Entities: {result['entities']}")
    print(f"  Anonymized: {result['anonymized']}")

    # Task 10: Adversarial suite
    print("\n[Task 10] Running adversarial suite...")
    with open(ADVERSARIAL_SET_PATH, encoding="utf-8") as f:
        adversarial_set = json.load(f)
    print(f"Loaded {len(adversarial_set)} adversarial inputs")

    adv_results = run_adversarial_suite(adversarial_set, rails, analyzer, anonymizer)
    passed = sum(1 for r in adv_results if r["passed"])
    pass_rate = passed / len(adv_results) if adv_results else 0
    print(f"Pass rate: {pass_rate:.0%} ({passed}/{len(adv_results)})")
    if pass_rate >= 0.9:
        print("✓ BONUS: ≥90% pass rate (18/20+)")

    # Per-category breakdown
    by_cat: dict[str, list[dict]] = {}
    for r in adv_results:
        by_cat.setdefault(r["category"], []).append(r)
    for cat, items in by_cat.items():
        cat_passed = sum(1 for i in items if i["passed"])
        print(f"  [{cat}] {cat_passed}/{len(items)}")

    # Task 12: P95 latency
    print("\n[Task 12] Measuring P95 latency...")
    sample_inputs = [item["input"] for item in adversarial_set[:10]]
    latency = measure_p95_latency(sample_inputs, n_runs=min(20, len(sample_inputs) * 2),
                                   rails=rails, analyzer=analyzer, anonymizer=anonymizer)
    print(f"  Presidio P95: {latency['presidio_ms']['p95']}ms")
    print(f"  NeMo P95:     {latency['nemo_ms']['p95']}ms")
    print(f"  Total P95:    {latency['total_ms']['p95']}ms")
    print(f"  Budget ({latency['budget_ms']}ms): {'✓ OK' if latency['latency_budget_ok'] else '✗ EXCEEDED'}")

    # Save report
    os.makedirs("reports", exist_ok=True)
    report = {
        "adversarial_suite": {
            "total": len(adv_results),
            "passed": passed,
            "pass_rate": round(pass_rate, 4),
            "results": adv_results,
        },
        "latency": latency,
    }
    with open("reports/guard_results.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\nPhase C report saved → reports/guard_results.json")
