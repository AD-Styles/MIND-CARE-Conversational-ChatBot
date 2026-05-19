"""llm_dialogue_node.py

/audio/transcripts를 구독하여 llama.cpp 서버(OpenAI 호환 HTTP API)로
대화를 생성하고 /llm/responses로 발행한다.

Phase 1 (현재):
  - 단일 사용자 대화, 시스템 프롬프트 = 노인 친화형 페르소나
  - 최근 N턴 메모리(ring buffer)

Phase 1.5 (RAG 추가):
  - rag_enabled=True 일 때 tools/build_rag_index.py 로 구축한 FAISS 인덱스에서
    top-k 문서를 검색해 system 메시지 뒤에 <참고자료> 블록으로 주입한다.

Phase 2 hook 파라미터 (미사용, 향후 활성화 예정):
  - sv_enabled          : 화자 검증 활성화 (pyannote/embedding)
  - guardrails_enabled  : NeMo Guardrails 활성화

발행 토픽
  /llm/responses (std_msgs/String)
    JSON: {"text": str, "timestamp_ns": int, "latency_ms": float,
           "input_text": str, "turn_id": int}
"""

import json
import threading
import time
from collections import deque

import rclpy
import requests
from rclpy.node import Node
from std_msgs.msg import String


DEFAULT_SYSTEM_PROMPT = (
    "당신은 혼자 사시는 70~80대 어르신을 돌보는 따뜻하고 다정한 말벗입니다. "
    "주체 높임을 자연스럽게 쓰는 경어체(-요/-세요)로 답하세요.\n\n"
    "가장 중요한 규칙 — 답변 길이:\n"
    "- 한 번에 1문장, 길어도 2문장으로만 답하세요. 절대 3문장을 넘기지 마세요.\n"
    "- 짧을수록 좋습니다. 인사나 간단한 말에는 한 문장으로만 답하세요.\n\n"
    "그 외 규칙:\n"
    "- 의학적 진단이나 약 복용 지시는 절대 하지 마세요. "
    "걱정되는 증상이 언급되면 '가까운 보건소나 보호자께 연락해 보시는 건 어떠세요?'라고 권유하세요.\n"
    "- 사용자의 말이 짧거나 불분명하면 의미를 임의로 확장하지 말고 따뜻하게 한 문장으로 되물어 주세요.\n"
    "- 어르신이 외로움·불안을 표현하시면 공감을 한 문장으로 짧게 표현하세요."
)


class LlmDialogueNode(Node):
    def __init__(self):
        super().__init__("llm_dialogue_node")

        self.declare_parameter("llm_endpoint", "http://127.0.0.1:8080/v1/chat/completions")
        self.declare_parameter("model_name", "local")
        self.declare_parameter("system_prompt", DEFAULT_SYSTEM_PROMPT)
        self.declare_parameter("max_tokens", 200)
        self.declare_parameter("temperature", 0.7)
        self.declare_parameter("history_turns", 6)
        self.declare_parameter("request_timeout_s", 30.0)
        self.declare_parameter("min_input_chars", 2)
        # 응답 시작 시점에 이보다 오래 지연된 발화는 폐기 (밀림 누적 차단)
        self.declare_parameter("stale_transcript_s", 12.0)

        # RAG / 기타 훅 파라미터
        self.declare_parameter("sv_enabled", False)
        self.declare_parameter("rag_enabled", False)
        self.declare_parameter("guardrails_enabled", False)
        # RAG 설정
        import os
        default_index = os.path.expanduser("~/마음돌봄/med_data/chroma_db")
        self.declare_parameter("rag_index_dir", default_index)
        # 다중 컬렉션 지원. YAML 에서 list 로 지정, 하위호환 위해 rag_collection(단일)도 허용.
        self.declare_parameter("rag_collections", ["med_disease", "med_blog"])
        self.declare_parameter("rag_collection", "")
        self.declare_parameter(
            "rag_embed_model",
            # Xavier 이전 후 "BAAI/bge-m3" 로 교체 예정
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        )
        self.declare_parameter("rag_top_k", 3)
        self.declare_parameter("rag_min_score", 0.25)

        self.endpoint = self.get_parameter("llm_endpoint").value
        self.model_name = self.get_parameter("model_name").value
        self.system_prompt = self.get_parameter("system_prompt").value
        self.max_tokens = int(self.get_parameter("max_tokens").value)
        self.temperature = float(self.get_parameter("temperature").value)
        self.history_turns = int(self.get_parameter("history_turns").value)
        self.timeout_s = float(self.get_parameter("request_timeout_s").value)
        self.min_input_chars = int(self.get_parameter("min_input_chars").value)
        self.stale_transcript_s = float(
            self.get_parameter("stale_transcript_s").value)

        # --- RAG retriever (lazy init) ---
        self._rag = None
        if self.get_parameter("rag_enabled").value:
            try:
                from .rag import RagRetriever
                single = self.get_parameter("rag_collection").value
                multi = list(self.get_parameter("rag_collections").value or [])
                coll = [single] if single else multi
                self._rag = RagRetriever(
                    index_dir=self.get_parameter("rag_index_dir").value,
                    collection=coll,
                    model_name=self.get_parameter("rag_embed_model").value,
                    device="cpu",
                )
                # 즉시 로드해서 실패 시 기동 단계에서 감지
                self._rag.retrieve("테스트", k=1)
                self.get_logger().info(
                    f"RAG ready. index={self.get_parameter('rag_index_dir').value}, "
                    f"top_k={self.get_parameter('rag_top_k').value}"
                )
            except Exception as exc:
                self.get_logger().error(
                    f"RAG init failed — RAG 비활성화: {exc}"
                )
                self._rag = None

        self._history: "deque[dict]" = deque(maxlen=self.history_turns * 2)
        self._turn_id = 0
        # 발화 순번 — 응답 직전 자신이 최신인지 확인해 오래된 발화를 폐기
        self._latest_seq = 0
        self._lock = threading.Lock()

        self.sub = self.create_subscription(
            String, "/audio/transcripts", self._on_transcript, 10
        )
        self.pub = self.create_publisher(String, "/llm/responses", 10)

        # Phase 5 — emergency_decider_node 가 능동 발화 ("괜찮으세요?") 를 시킴.
        # 일반 대화 흐름과 별개로 즉시 TTS 로 보냄.
        self.create_subscription(
            String, "/dialogue/proactive_speech",
            self._on_proactive_speech, 10,
        )

        self.get_logger().info(
            f"LLM dialogue ready. endpoint={self.endpoint}, "
            f"history_turns={self.history_turns}, "
            f"hooks(sv={self.get_parameter('sv_enabled').value}, "
            f"rag={self.get_parameter('rag_enabled').value}, "
            f"guard={self.get_parameter('guardrails_enabled').value})"
        )

    def _on_transcript(self, msg: String):
        try:
            payload = json.loads(msg.data)
            text = (payload.get("text") or "").strip()
        except Exception as exc:
            self.get_logger().warn(f"Bad transcript payload: {exc}")
            return

        if len(text) < self.min_input_chars:
            return

        # 화자 검증 — audio_bridge_node 가 payload 에 결과를 실어 보냄
        if self.get_parameter("sv_enabled").value:
            verified = payload.get("speaker_verified", True)
            score = payload.get("speaker_score", 1.0)
            if not verified:
                self.get_logger().info(
                    f"[SV] 미등록 화자 — 응답 생략 (score={score})"
                )
                return

        # 새 발화는 대기 중인 이전 발화들을 무효화 — 가장 최근 것만 응답
        self._latest_seq += 1
        seq = self._latest_seq
        threading.Thread(target=self._respond,
                         args=(text, seq, time.monotonic()),
                         daemon=True).start()

    def _respond(self, user_text: str, seq: int, recv_t: float):
        rag_info = ""
        with self._lock:
            # 락 획득 시점 — 더 최신 발화가 왔거나 너무 오래 지연됐으면 폐기.
            # (CPU 경합으로 LLM/ASR 가 밀릴 때 옛 발화 응답이 쌓이는 것 차단)
            if seq != self._latest_seq:
                self.get_logger().info(
                    f"[stale] 발화 폐기 — 더 최신 발화 있음: {user_text!r}")
                return
            age = time.monotonic() - recv_t
            if age > self.stale_transcript_s:
                self.get_logger().info(
                    f"[stale] 발화 폐기 — {age:.1f}s 지연: {user_text!r}")
                return
            self._turn_id += 1
            turn_id = self._turn_id

            messages = [{"role": "system", "content": self.system_prompt}]

            # --- RAG 컨텍스트 주입 ---
            if self._rag is not None:
                try:
                    k = int(self.get_parameter("rag_top_k").value)
                    min_score = float(self.get_parameter("rag_min_score").value)
                    t_rag = time.time()
                    hits = self._rag.retrieve(user_text, k=k)
                    hits = [h for h in hits if h.get("score", 0.0) >= min_score]
                    rag_latency_ms = (time.time() - t_rag) * 1000.0
                    if hits:
                        block = self._rag.format_for_prompt(hits)
                        messages.append({"role": "system", "content": block})
                        rag_info = (
                            f" rag=[{len(hits)} hits, top={hits[0]['category']}/"
                            f"{hits[0]['score']:.2f}, {rag_latency_ms:.0f}ms]"
                        )
                    else:
                        rag_info = f" rag=[no hits, {rag_latency_ms:.0f}ms]"
                except Exception as exc:
                    self.get_logger().warn(f"RAG retrieve failed: {exc}")

            messages.extend(list(self._history))
            messages.append({"role": "user", "content": user_text})

        body = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": False,
        }

        t0 = time.time()
        try:
            resp = requests.post(self.endpoint, json=body, timeout=self.timeout_s)
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            self.get_logger().error(f"LLM call failed: {exc}")
            return

        latency_ms = (time.time() - t0) * 1000.0

        # Phase 2: Guardrails 훅 — 현재 no-op
        if self.get_parameter("guardrails_enabled").value:
            # TODO: NeMo Guardrails 안전 필터
            pass

        if not reply:
            self.get_logger().debug("Empty LLM reply")
            return

        with self._lock:
            self._history.append({"role": "user", "content": user_text})
            self._history.append({"role": "assistant", "content": reply})

        out = {
            "text": reply,
            "timestamp_ns": time.time_ns(),
            "latency_ms": round(latency_ms, 1),
            "input_text": user_text,
            "turn_id": turn_id,
        }
        out_msg = String()
        out_msg.data = json.dumps(out, ensure_ascii=False)
        self.pub.publish(out_msg)
        self.get_logger().info(
            f"[LLM turn#{turn_id} {latency_ms:.0f}ms{rag_info}] "
            f"USER={user_text!r} -> REPLY={reply!r}"
        )

    # ------------------------------------------------------------------
    # Phase 5 — 능동 발화 (emergency_decider_node 가 트리거)
    # ------------------------------------------------------------------
    def _on_proactive_speech(self, msg: String):
        """`/dialogue/proactive_speech` 텍스트를 곧장 `/llm/responses` 로 forward.

        LLM 추론을 거치지 않고 즉시 TTS 발화 — 응급 상황에서 latency 최소화.
        history 에도 assistant 발화로 누적 → 다음 사용자 응답이 이어진 대화로 흐름.
        """
        text = (msg.data or "").strip()
        if not text:
            return
        self.get_logger().info(f"[proactive] {text!r}")
        out = {
            "text": text,
            "timestamp_ns": time.time_ns(),
            "latency_ms": 0.0,
            "input_text": "",
            "turn_id": -1,    # -1 = proactive (사용자 입력 X)
            "proactive": True,
        }
        out_msg = String()
        out_msg.data = json.dumps(out, ensure_ascii=False)
        self.pub.publish(out_msg)
        with self._lock:
            self._history.append({"role": "assistant", "content": text})


def main():
    rclpy.init()
    node = LlmDialogueNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
