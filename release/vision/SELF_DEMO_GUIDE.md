# 자체 시연 영상 — 녹화 가이드

> 결과보고서 §4.3 KPI 표 갱신용. URFDD 외부 데이터셋의 한계 (촬영 각도, 조명) 를
> 보완하기 위해 우리가 실제 운영할 환경 (집/사무실 카메라 각도) 에서 직접 녹화.

---

## 1. 사전 준비

- [ ] 카메라 거치 — 어르신 키 기준 1.2~1.5 m 높이, 거실 중앙 향함
- [ ] 조명 — 일반 실내등 + 자연광 모두 1 회씩
- [ ] 녹화 도구: 스마트폰 30 fps 1080p, 또는 WebCam + OBS
- [ ] 측정 환경: WSL HRI 풀 스택 가동 (`bash scripts/start_hri.sh`)

## 2. 녹화 시나리오 (총 35 건)

### 2.1 낙상 시연 (10 건)
| # | 동작 | 비고 |
|---|---|---|
| F-01~03 | 정면 낙상 (앞으로 무릎 꿇기 → 엎드림) | 얼굴 인식이 마지막에 사라짐 |
| F-04~05 | 옆 낙상 (왼/오른쪽) | aspect ratio 변화 큼 |
| F-06~07 | 뒤 낙상 (의자 ↔ 누워) | confirm_idle 5 s 통과 |
| F-08 | 갑작스러운 주저앉기 | 짧은 ratio 변화 |
| F-09~10 | 부분 낙상 — 일어서다 옆으로 미끄러짐 | 회복 시나리오 |

→ 각 영상은 **fall 시작 5 s 전 + 낙상 동작 + 8 s 정지** 로 18 s 길이로 자르기.

### 2.2 정상 활동 — 거짓 양성 검증 (10 건)
| # | 동작 | 비고 |
|---|---|---|
| N-01~02 | 의자에 앉기 | aspect 변화 작음, 부동 후 다시 움직임 |
| N-03~04 | 침대에 눕기 | aspect 1.6 ↑ 가능, 그러나 의도적 |
| N-05~06 | 허리 굽히기 (신발 끈, 물건 줍기) | 1-2 s 짧은 자세 |
| N-07~08 | 운동/스트레칭 | 다양한 자세 |
| N-09~10 | 바닥에 양반다리 → 다시 일어남 | confirm_idle 시간 내 회복 |

### 2.3 화자 검증 (10 건)
| # | 발화 | 화자 | 기대 |
|---|---|---|---|
| S-01~05 | "오늘 날씨 어때요" 등 일상 발화 | **본인** | LLM 응답 ✓ |
| S-06~08 | "안녕하세요" 등 | **다른 사람 (가족 1명)** | SV REJECT |
| S-09 | TV 음성 (뉴스 30 s) | TV | SV REJECT |
| S-10 | "도와줘" | **다른 사람** | **SV REJECT 되더라도 emergency_decider 는 트리거** |

### 2.4 응급 시나리오 (5 건)
| # | 시나리오 | 기대 |
|---|---|---|
| E-01 | 본인 "도와줘" → 즉시 alert | < 1 s |
| E-02 | 본인 "넘어졌어요" → 즉시 alert | < 1 s |
| E-03 | 정면 낙상 (F-01) → "괜찮으세요?" → "괜찮아요" | NORMAL 복귀 |
| E-04 | 정면 낙상 → "괜찮으세요?" → 무응답 30 s | EMERGENCY |
| E-05 | 정면 낙상 → "괜찮으세요?" → "도와줘" | EMERGENCY 즉시 |

## 3. 측정·기록

각 시나리오마다 다음을 함께 기록 (스크린샷 또는 stdout):
- ROS 토픽 echo `/llm/responses`, `/emergency/alert`, `/dialogue/proactive_speech`
- 시작 timestamp + 검출 timestamp → latency 계산
- 영상 파일명: `videos/{F|N|S|E}-NN_brief.mp4`

## 4. 평가 자동화

녹화 후 다음 스크립트로 일괄 평가 (이미 있는 `eval_fall.py` + 추가 작성):

```bash
# 낙상 — F + N 영상 → recall/precision (기존 eval_fall.py 재사용)
python release/vision/eval_fall.py \
    --videos ~/eval/self_demo/videos \
    --gt ~/eval/self_demo/labels.csv \
    --out ~/eval/self_demo/results

# 화자 검증 — S 영상 → ASR 후 SV 통과율
python release/emergency/scripts/eval_sv.py \
    --videos ~/eval/self_demo/videos/S-* \
    --truth registered

# 응급 — E 영상 → e2e latency p50/p95
python release/emergency/scripts/eval_emergency.py \
    --videos ~/eval/self_demo/videos/E-*
```

## 5. 결과 정리 → 결과보고서 §4.3 갱신

| 항목 | URFDD (외부) | 자체 시연 |
|---|---|---|
| 낙상 Recall | 0.767 | (record) |
| 낙상 Precision | 0.676 | (record) |
| SV TPR (본인 통과) | — | (record) |
| SV TNR (외부 차단) | — | (record) |
| 응급 e2e p95 | 3.69 s | (record) |
