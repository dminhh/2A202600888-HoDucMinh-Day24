# LLM Judge Bias Report — Phase B

**Sinh viên:** Hồ Đức Minh
**Ngày:** 2026-06-30
**Judge model:** gpt-4o-mini

---

## 1. Pairwise Judge Results

*(Chạy pairwise_judge() trên 10 cặp answers — model_answer (A) vs ground_truth (B))*

| # | Question (tóm tắt) | Winner | Reasoning tóm tắt |
|---|---|---|---|
| 1 | Nghỉ bao nhiêu ngày khi kết hôn? | B | B đầy đủ hơn: đề cập nghỉ không trừ phép năm, A chỉ nêu số ngày |
| 2 | Mua thiết bị 55 triệu cần ai phê duyệt? | B | B xác định ngưỡng >50 triệu chính xác; A chỉ nói "Giám đốc phòng ban" chung chung |
| 3 | Thưởng Tết tối thiểu cho nhân viên ≥6 tháng? | B | B bao gồm cả quy định dưới 6 tháng; A chỉ trả lời một chiều |
| 4 | Senior 9 năm: bao nhiêu ngày phép và lương? | B | B giải thích công thức tính; A chỉ đưa kết quả |
| 5 | Tài trợ 25 triệu, nghỉ việc sau 8 tháng — hoàn trả bao nhiêu? | B | B cung cấp cam kết và quy định hoàn trả đầy đủ; A thiếu ngữ cảnh |
| 6 | Tạm ứng 8 triệu, quá hạn 30 ngày — phê duyệt và phí phạt? | B | B nêu người phê duyệt và cách tính phí cụ thể; A thiếu phép tính |
| 7 | Manager 12 năm: tổng phụ cấp và ngày phép? | B | B chi tiết từng loại phụ cấp và công thức tính phép; A chỉ kết quả |
| 8 | Nhân viên được nghỉ bao nhiêu ngày phép năm? | B | B đề cập cả policy cũ và mới; A chỉ nêu policy cũ |
| 9 | Nhân viên thử việc có được nghỉ phép không? | tie | Pass 1 → B; Pass 2 → A (sau swap): không nhất quán → tie |
| 10 | Manager có thể dùng VPN cá nhân (NordVPN) khi WFH? | B | B trích dẫn chính sách cấm VPN cá nhân; A chỉ ý kiến chung |

---

## 2. Swap-and-Average Results

*(Chạy swap_and_average() trên 10 cặp)*

| # | Pass 1 Winner | Pass 2 Winner (converted) | Final | Position Consistent? |
|---|---|---|---|---|
| 1 | B | B | B | Yes |
| 2 | B | B | B | Yes |
| 3 | B | B | B | Yes |
| 4 | B | B | B | Yes |
| 5 | B | B | B | Yes |
| 6 | B | B | B | Yes |
| 7 | B | B | B | Yes |
| 8 | B | B | B | Yes |
| 9 | B | A | tie | **No** |
| 10 | B | B | B | Yes |

**Position bias rate:** 10% (= 1 case NOT consistent / 10 tổng)

---

## 3. Cohen's κ Analysis

**Human labels:** `human_labels_10q.json` (10 câu, 5 label=1, 5 label=0)
**Judge labels:** dựa trên avg_score_A > 0.5 (model_answer đủ chất lượng → label=1)

| Question ID | Human Label | Judge Label | Agree? |
|---|---|---|---|
| Q1 (kết hôn) | 1 | 1 | Yes |
| Q2 (thiết bị 55tr) | 0 | 0 | Yes |
| Q3 (thưởng Tết) | 1 | 1 | Yes |
| Q21 (Senior 9 năm) | 1 | 1 | Yes |
| Q23 (tài trợ 25tr) | 1 | 1 | Yes |
| Q29 (tạm ứng 8tr) | 0 | 1 | **No** |
| Q33 (Manager 12 năm) | 1 | 1 | Yes |
| Q41 (ngày phép năm) | 0 | 0 | Yes |
| Q46 (thử việc + phép) | 1 | 1 | Yes |
| Q50 (VPN cá nhân) | 0 | 0 | Yes |

**Cohen's κ:** 0.783
**Interpretation:** Substantial agreement — judge đáng tin cậy (κ > 0.6 ✓)

---

## 4. Verbosity Bias

Trong các case có winner rõ ràng (không phải tie): 9/10 cases decisive

- A thắng + A dài hơn B: 0 / 9 cases
- B thắng + B dài hơn A: 9 / 9 cases
- **Verbosity bias rate: 100%** (= (0+9)/9)

**Kết luận:** LLM judge (gpt-4o-mini) có xu hướng rất rõ ràng chọn answer dài hơn — trong trường hợp này ground_truth (B) luôn dài và chi tiết hơn model_answer (A) nên judge chọn B 100%. Đây là vấn đề trong production vì một câu trả lời dài có thể chứa nhiều thông tin sai hơn mà vẫn được judge đánh giá cao hơn câu ngắn nhưng chính xác. Cần cân nhắc thêm tiêu chí "accuracy per token" hoặc penalize verbosity trong prompt.

---

## 5. Nhận xét chung

> Cohen's κ = 0.783 (substantial agreement) cho thấy LLM judge đáng tin cậy làm proxy cho human judgment — judge đồng ý với human 9/10 lần, chỉ bất đồng ở Q29 (tạm ứng 8tr) nơi human label=0 nhưng judge đánh giá model_answer đủ chất lượng (score_A > 0.5). Position bias rate = 10% (1/10 case) rất thấp — dưới ngưỡng đáng lo ngại 30%, cho thấy swap-and-average là cơ chế hiệu quả: case Q46 (thử việc) được phát hiện inconsistent và chuyển thành tie thay vì trả về kết quả sai. Swap-and-average thực sự giúp ích: nếu chỉ dùng pass 1, ta sẽ có 0% tie; sau swap, Q46 được xử lý đúng. Verbosity bias 100% là quan sát đáng lo ngại nhất — trong môi trường production, nên điều chỉnh judge prompt để penalize verbosity ("conciseness is a virtue") và thêm tiêu chí "prefer the answer that contains no unnecessary information".
