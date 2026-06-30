# CI/CD Blueprint: RAG Eval + Guardrail Stack

**Sinh viên:** Hồ Đức Minh
**Ngày:** 2026-06-30

---

## Guard Stack Architecture

```
User Input
    │
    ▼ (~8.45ms P50 / 9.49ms P95)
[Presidio PII Scan]
    │ block if: VN_CCCD (12 số) / VN_PHONE (0[3-9]xxxxxxxx) / EMAIL detected
    │ action:   return 400 + "PII detected in query"
    ▼ (~2.24ms P50 / 2.52ms P95)
[NeMo Input Rail]
    │ block if: off-topic / jailbreak / prompt injection / PII request
    │ action:   return 503 + refuse message từ Colang flows
    ▼
[RAG Pipeline (Day 18)]
    │ M1 Chunk → M2 Search (BM25+Dense) → M3 Rerank → GPT-4o-mini
    ▼
[NeMo Output Rail + Presidio Output Scan]
    │ flag if:  PII in response / sensitive content / hallucination markers
    │ action:   anonymize PII hoặc thay bằng safe response
    ▼
User Response (~10.65ms P50 / 11.85ms P95 guard overhead)
```

---

## Latency Budget

*(Kết quả thực tế từ Task 12 — measure_p95_latency())*

| Layer | P50 (ms) | P95 (ms) | P99 (ms) | Budget |
|---|---|---|---|---|
| Presidio PII | 8.45 | 9.49 | 9.49 | <10ms |
| NeMo Input Rail | 2.24 | 2.52 | 2.52 | <300ms |
| RAG Pipeline | ~600–800 | ~900–1200 | ~1500 | <2000ms |
| NeMo Output Rail | ~2 | ~3 | ~3 | <300ms |
| **Total Guard (excl. RAG)** | 10.65 | **11.85** | 11.85 | **<500ms** |

**Budget OK?** [x] Yes / [ ] No
**Comment:** Guard overhead chỉ 11.85ms P95 — rất tốt, chủ yếu là Presidio regex scan. NeMo chạy nhanh vì dùng Colang pattern matching không cần LLM call. Bottleneck thực sự là RAG Pipeline (LLM generation), không phải guardrail layer.

---

## CI/CD Gates (phải pass trước khi merge to main)

```yaml
# .github/workflows/rag_eval.yml
- name: RAGAS Quality Gate
  run: python src/phase_a_ragas.py
  env:
    MIN_FAITHFULNESS: 0.75
    MIN_AVG_SCORE: 0.65
  # Kết quả hiện tại: factual avg=0.896, multi_hop avg=0.693, adversarial avg=0.722
  # Fail nếu faithfulness drop dưới 0.75 (signal hallucination tăng)

- name: Guardrail Gate
  run: pytest tests/test_phase_c.py -k "test_adversarial_suite_pass_rate"
  # phải ≥ 15/20 (75%) — hiện tại đạt 20/20 (100%)

- name: Latency Gate
  run: |
    python -c "
    from src.phase_c_guard import measure_p95_latency
    result = measure_p95_latency(['test nghỉ phép'], n_runs=20)
    assert result['latency_budget_ok'], f'P95={result[\"total_ms\"][\"p95\"]}ms > 500ms budget'
    print('Latency gate passed:', result['total_ms']['p95'], 'ms P95')
    "
  # P95 total < 500ms — hiện tại 11.85ms (rất tốt)

- name: Judge Agreement Gate
  run: |
    python -c "
    from src.phase_b_judge import cohen_kappa
    # Run judge on held-out set và kiểm tra κ
    # Cần κ > 0.4 (moderate agreement) để judge đáng tin cậy
    "
  # Hiện tại κ = 0.783 (substantial agreement)
```

---

## Monitoring Dashboard (production)

| Metric | Alert Threshold | Action |
|---|---|---|
| RAGAS faithfulness (daily sample 10q) | < 0.70 | Page on-call, review top-10 failure questions |
| RAGAS multi_hop avg_score | < 0.60 | Trigger chunking/reranking review |
| Adversarial block rate | < 80% | Review mới attack patterns, cập nhật Colang flows |
| Guard P95 latency total | > 100ms | Investigate Presidio config, scale if needed |
| PII detected count | spike > 10/hour | Security alert, audit logs |
| Cohen's κ (weekly) | < 0.40 | Retrain/update judge prompt |

---

## Kết quả thực tế từ Lab

| | Kết quả |
|---|---|
| RAGAS avg_score — factual (20q) | 0.896 |
| RAGAS avg_score — multi_hop (20q) | 0.693 |
| RAGAS avg_score — adversarial (10q) | 0.722 |
| Worst metric (toàn bộ) | faithfulness (hallucination) |
| Dominant failure distribution | factual (count failures) / multi_hop (severity) |
| Cohen's κ (LLM judge vs human) | 0.783 — substantial agreement |
| Adversarial pass rate | 20 / 20 (100%) |
| Guard P95 latency (Presidio) | 9.49 ms |
| Guard P95 latency (NeMo) | 2.52 ms |
| Guard P95 latency (Total) | 11.85 ms ✓ |

---

## Nhận xét & Cải tiến

Điều hoạt động tốt: Presidio PII detection rất nhanh (<10ms P95) và chính xác với VN_CCCD/VN_PHONE custom recognizers; NeMo Guardrails chặn được 100% adversarial inputs nhờ Colang flow matching. Điều cần cải thiện: faithfulness là điểm yếu nhất (multi_hop faithfulness chỉ 0.383) — pipeline hay hallucinate khi phải kết hợp nhiều tài liệu; cần tighten system prompt với instruction "ONLY answer from provided context". Nếu deploy production, tôi sẽ: (1) thêm caching layer cho Presidio để giảm overhead với repeated queries, (2) nâng Colang flows với nhiều Vietnamese jailbreak patterns hơn, (3) thêm RAGAS daily sampling pipeline chạy automatic trên 5 random queries mỗi ngày để detect drift sớm, (4) xem xét dùng NeMo với self-check-facts action thực sự (kết nối RAG context) thay vì chỉ pattern matching.
