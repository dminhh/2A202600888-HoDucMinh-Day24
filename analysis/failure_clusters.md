# Failure Cluster Analysis — Phase A

**Sinh viên:** Hồ Đức Minh
**Ngày:** 2026-06-30

---

## 1. Aggregate RAGAS Scores theo Distribution

| Metric | factual | multi_hop | adversarial |
|---|---|---|---|
| faithfulness | 0.933 | 0.383 | 0.650 |
| answer_relevancy | 0.801 | 0.635 | 0.606 |
| context_precision | 0.975 | 0.933 | 0.950 |
| context_recall | 0.875 | 0.821 | 0.683 |
| **avg_score** | **0.896** | **0.693** | **0.722** |

---

## 2. Bottom 10 Questions

| Rank | Distribution | Question | avg_score | worst_metric |
|---|---|---|---|---|
| 1 | multi_hop | So sánh yêu cầu mật khẩu giữa policy v1.0 và v2.0 về độ dài tối thiểu, thời hạn đổi và MFA. | 0.2500 | faithfulness |
| 2 | multi_hop | Một nhân viên Senior có 9 năm thâm niên được nghỉ bao nhiêu ngày phép năm và lương trong khoảng nào? | 0.3750 | answer_relevancy |
| 3 | adversarial | Nhân viên thử việc có được nghỉ phép năm không? | 0.3750 | faithfulness |
| 4 | multi_hop | Nhân viên Manager có thâm niên 12 năm: tổng phụ cấp hàng tháng và số ngày phép năm theo v2024 là bao nhiêu? | 0.3750 | faithfulness |
| 5 | adversarial | Nhân viên thử việc có được hưởng bảo hiểm sức khỏe PVI không? | 0.4167 | faithfulness |
| 6 | adversarial | Nhân viên Manager có thể dùng VPN cá nhân (như NordVPN) khi WFH để tăng bảo mật thêm không? | 0.4167 | faithfulness |
| 7 | factual | Nam nhân viên được nghỉ bao nhiêu ngày khi vợ sinh con? | 0.5000 | faithfulness |
| 8 | multi_hop | Nhân viên tạm ứng 8 triệu, chưa thanh toán sau 30 ngày (quá hạn 15 ngày). Ai phê duyệt khoản này và phí phạt là bao nhiêu? | 0.5000 | faithfulness |
| 9 | multi_hop | So sánh quyền lợi bảo hiểm giữa nhân viên thử việc và nhân viên chính thức. | 0.5000 | faithfulness |
| 10 | multi_hop | Nhân viên tạm ứng 4 triệu và một nhân viên khác tạm ứng 7 triệu: quy trình phê duyệt khác nhau thế nào? | 0.6275 | faithfulness |

---

## 3. Failure Cluster Matrix

*(Mỗi ô = số câu có worst_metric = row, thuộc distribution = col)*

| worst_metric | factual | multi_hop | adversarial | Total |
|---|---|---|---|---|
| faithfulness | 2 | 16 | 4 | **22** |
| answer_relevancy | 11 | 2 | 0 | **13** |
| context_recall | 4 | 2 | 6 | **12** |
| context_precision | 3 | 0 | 0 | **3** |

---

## 4. Dominant Failure Analysis

**Dominant distribution:** factual (20 questions, nhưng vì nhiều metric failure nhất tổng hợp)
**Dominant metric:** faithfulness (22/50 câu — chiếm 44% toàn bộ dataset)

**Lý do phân tích:**

> Faithfulness là điểm yếu nghiêm trọng nhất, đặc biệt trong multi_hop (16/20 câu). Khi phải tổng hợp thông tin từ nhiều policy document (v2023 vs v2024, employee handbook + IT policy + HR policy), mô hình GPT-4o-mini có xu hướng "hallucinate" — tạo ra câu trả lời hợp lý về mặt ngôn ngữ nhưng không khớp với nội dung chunk được retrieve. Multi_hop failure cao vì mỗi câu yêu cầu kết hợp ít nhất 2-3 chunks từ nhiều policy khác nhau, tăng nguy cơ mô hình pha trộn thông tin sai. Adversarial questions (về nhân viên thử việc, VPN cá nhân) cũng dẫn đến faithfulness thấp vì pipeline retrieve chunk không liên quan rồi mô hình tự suy diễn câu trả lời "có vẻ đúng". Context precision (0.975 factual) cao cho thấy retrieval tốt — vấn đề nằm ở generation, không phải retrieval.

---

## 5. Suggested Fixes

| Metric yếu | Root cause | Suggested fix |
|---|---|---|
| faithfulness | LLM hallucinating khi tổng hợp nhiều chunk | Tighten system prompt: thêm "ONLY answer from provided context. If not in context, say so.", lower temperature từ 0.7 → 0.1 |
| context_recall | Missing relevant chunks trong adversarial questions | Tăng top_k từ 5 → 8; thêm BM25 cho exact keyword matching trên policy version numbers |
| context_precision | Too many irrelevant chunks (chủ yếu factual) | Add metadata filter theo document_type; xem xét reranking mạnh hơn (cross-encoder) |
| answer_relevancy | Answer không match question (multi_hop #2) | Improve prompt template: thêm instruction "Answer must directly address the specific question asked" |

---

## 6. Nhận xét về Adversarial Distribution

> Adversarial avg_score = 0.722, cao hơn multi_hop (0.693) nhưng thấp hơn factual (0.896). Pipeline không bị "nhầm" nhiều bởi version conflicts: context_precision adversarial = 0.950 (rất cao), chứng tỏ retrieval vẫn tìm đúng chunks. Điểm yếu của adversarial là context_recall = 0.683 (thấp nhất trong 3 distributions) và faithfulness = 0.650 — khi câu hỏi hỏi về thông tin không có trong policy (ví dụ: nhân viên thử việc hưởng PVI, dùng VPN cá nhân), mô hình tend to hallucinate thay vì nói "thông tin không có trong tài liệu". Trong bottom 10: 3/10 câu thuộc adversarial (rank 3, 5, 6), đều có worst_metric=faithfulness. Đây là signal rõ ràng rằng cần thêm "out-of-scope" handling trong system prompt.
