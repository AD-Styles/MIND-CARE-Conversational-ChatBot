"""audio_bridge_node.py

마이크 입력을 캡처하여 WebRTC VAD로 발화 구간을 검출하고,
faster-whisper로 한국어 ASR을 수행한 뒤 ROS 2 토픽으로 발행한다.

발행 토픽
  /audio/transcripts (std_msgs/String)
    JSON 페이로드: {"text": str, "timestamp_ns": int,
                    "duration_s": float, "latency_ms": float}

파라미터
  model_size         : faster-whisper 모델 크기 (tiny/base/small/medium/large-v3)
  device             : "cuda" 또는 "cpu"
  compute_type       : "float16"(GPU) / "int8"(CPU) 등
  language           : ASR 언어 코드 (기본 "ko")
  sample_rate        : 마이크 샘플레이트 (VAD는 8/16/32/48kHz만 지원)
  vad_aggressiveness : 0~3, 클수록 비음성 제거 공격적
  min_silence_ms     : 발화 종료 판정용 무음 길이
  min_speech_ms      : 너무 짧은 발화 무시
  max_segment_s      : 최대 발화 길이(강제 flush)
  input_device       : sounddevice 입력 장치 인덱스 (-1이면 기본)
"""

import collections
import json
import queue
import threading
import time

import numpy as np
import rclpy
import sounddevice as sd
import webrtcvad
from faster_whisper import WhisperModel
from rclpy.node import Node
from std_msgs.msg import String

# 화자 검증 (선택) — resemblyzer 미설치 환경에서도 노드 자체는 기동
try:
    from .speaker_verifier import SpeakerVerifier as _SV
    _SV_AVAILABLE = True
except ImportError:
    _SV_AVAILABLE = False


class AudioBridgeNode(Node):
    def __init__(self):
        super().__init__("audio_bridge_node")

        self.declare_parameter("model_size", "small")
        self.declare_parameter("device", "cuda")
        self.declare_parameter("compute_type", "float16")
        self.declare_parameter("language", "ko")
        self.declare_parameter("sample_rate", 16000)
        self.declare_parameter("vad_aggressiveness", 2)
        self.declare_parameter("min_silence_ms", 600)
        self.declare_parameter("min_speech_ms", 300)
        self.declare_parameter("max_segment_s", 15.0)
        self.declare_parameter("input_device", -1)
        self.declare_parameter("tts_guard_tail_s", 1.5)

        # 화자 검증 파라미터
        self.declare_parameter("sv_enabled", False)
        self.declare_parameter(
            "sv_embedding_path",
            str(__import__("pathlib").Path.home() / "models/speaker.npy"),
        )
        self.declare_parameter("sv_threshold", 0.75)

        self._sv = None
        if _SV_AVAILABLE and self.get_parameter("sv_enabled").value:
            try:
                self._sv = _SV(
                    embedding_path=self.get_parameter("sv_embedding_path").value,
                    threshold=float(self.get_parameter("sv_threshold").value),
                )
                self.get_logger().info("SpeakerVerifier loaded.")
            except Exception as exc:
                self.get_logger().warn(f"SpeakerVerifier init failed: {exc}")
        elif self.get_parameter("sv_enabled").value and not _SV_AVAILABLE:
            self.get_logger().warn(
                "sv_enabled=true 이지만 resemblyzer import 실패 — 검증 비활성화"
            )

        self.sr = int(self.get_parameter("sample_rate").value)
        if self.sr not in (8000, 16000, 32000, 48000):
            raise ValueError(f"sample_rate must be 8/16/32/48 kHz, got {self.sr}")

        self.language = self.get_parameter("language").value
        self.min_silence_ms = int(self.get_parameter("min_silence_ms").value)
        self.min_speech_ms = int(self.get_parameter("min_speech_ms").value)
        self.max_segment_samples = int(
            float(self.get_parameter("max_segment_s").value) * self.sr
        )

        self.pub = self.create_publisher(String, "/audio/transcripts", 10)

        self.vad = webrtcvad.Vad(int(self.get_parameter("vad_aggressiveness").value))

        self.get_logger().info(
            f"Loading faster-whisper: size={self.get_parameter('model_size').value}, "
            f"device={self.get_parameter('device').value}, "
            f"compute_type={self.get_parameter('compute_type').value}"
        )
        self.asr = WhisperModel(
            self.get_parameter("model_size").value,
            device=self.get_parameter("device").value,
            compute_type=self.get_parameter("compute_type").value,
        )
        self.get_logger().info("ASR model loaded.")

        self.audio_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=200)
        # 세그먼터 → ASR 워커로 완성 세그먼트를 넘기는 큐 (동기 whisper 호출을
        # 별도 스레드로 분리해 audio_q가 블로킹 되지 않게 한다)
        # maxsize 작게 — CPU 경합으로 ASR 가 밀릴 때 오래된 세그먼트가
        # 쌓이지 않게. 큐가 차면 가장 오래된 것을 버리고 최신을 넣음.
        self.asr_q: "queue.Queue[bytes]" = queue.Queue(maxsize=2)
        self._stop = threading.Event()

        # TTS 재생 중 마이크 게이트 (에코 루프 방지)
        self._tts_speaking = False
        self._tts_guard_until = 0.0  # TTS 종료 후 short tail guard (seconds-monotonic)
        self._tts_guard_tail_s = float(self.get_parameter("tts_guard_tail_s").value)
        self.sub_tts_status = self.create_subscription(
            String, "/tts/status", self._on_tts_status, 10
        )

        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._segmenter_thread = threading.Thread(target=self._segmenter_loop, daemon=True)
        self._asr_thread = threading.Thread(target=self._asr_worker_loop, daemon=True)
        self._capture_thread.start()
        self._segmenter_thread.start()
        self._asr_thread.start()

    def _capture_loop(self):
        device = int(self.get_parameter("input_device").value)
        device_arg = None if device < 0 else device
        blocksize = int(self.sr * 0.03)  # 30ms blocks

        def _callback(indata, frames, time_info, status):
            if status:
                self.get_logger().warn(f"Input stream status: {status}")
            # TTS 재생 중이거나 tail-guard 시간 내에는 마이크 입력 무시 (에코 차단)
            if self._tts_speaking or time.monotonic() < self._tts_guard_until:
                return
            try:
                self.audio_q.put_nowait(indata.copy())
            except queue.Full:
                self.get_logger().warn("audio_q full; dropping frame")

        try:
            with sd.InputStream(
                samplerate=self.sr,
                channels=1,
                dtype="int16",
                blocksize=blocksize,
                device=device_arg,
                callback=_callback,
            ):
                self.get_logger().info(
                    f"Microphone capture started (device={device_arg}, sr={self.sr})"
                )
                while not self._stop.is_set() and rclpy.ok():
                    time.sleep(0.1)
        except Exception as exc:
            self.get_logger().error(f"Capture loop failed: {exc}")

    def _segmenter_loop(self):
        frame_ms = 30
        frame_samples = int(self.sr * frame_ms / 1000)
        frame_bytes = frame_samples * 2  # int16

        ring_cap = max(1, self.min_silence_ms // frame_ms)
        ring = collections.deque(maxlen=ring_cap)
        triggered = False
        segment = bytearray()
        carry = b""

        min_speech_frames = max(1, self.min_speech_ms // frame_ms)
        speech_frame_count = 0

        while not self._stop.is_set() and rclpy.ok():
            try:
                block = self.audio_q.get(timeout=0.2)
            except queue.Empty:
                continue

            raw = carry + block.tobytes()
            n_full = len(raw) // frame_bytes
            carry = raw[n_full * frame_bytes:]

            for i in range(n_full):
                frame = raw[i * frame_bytes:(i + 1) * frame_bytes]
                try:
                    is_speech = self.vad.is_speech(frame, self.sr)
                except Exception:
                    is_speech = False

                if not triggered:
                    ring.append((frame, is_speech))
                    voiced = sum(1 for _, s in ring if s)
                    if voiced > 0.9 * ring.maxlen:
                        triggered = True
                        for f, _ in ring:
                            segment.extend(f)
                        speech_frame_count = len(ring)
                        ring.clear()
                else:
                    segment.extend(frame)
                    ring.append((frame, is_speech))
                    if is_speech:
                        speech_frame_count += 1

                    silent = sum(1 for _, s in ring if not s)
                    too_long = len(segment) // 2 >= self.max_segment_samples

                    if silent > 0.9 * ring.maxlen or too_long:
                        if speech_frame_count >= min_speech_frames:
                            # ASR는 워커 스레드에 위임 — 세그먼터는 즉시 다음 프레임 처리
                            try:
                                self.asr_q.put_nowait(bytes(segment))
                            except queue.Full:
                                # 큐가 차면 가장 오래된 세그먼트를 버리고 최신을 넣음
                                try:
                                    self.asr_q.get_nowait()
                                except queue.Empty:
                                    pass
                                try:
                                    self.asr_q.put_nowait(bytes(segment))
                                except queue.Full:
                                    pass
                                self.get_logger().warn(
                                    "asr_q full; 오래된 세그먼트 폐기")
                        else:
                            self.get_logger().debug(
                                f"Segment too short ({speech_frame_count} frames); dropped"
                            )
                        triggered = False
                        segment = bytearray()
                        ring.clear()
                        speech_frame_count = 0

    def _asr_worker_loop(self):
        while not self._stop.is_set() and rclpy.ok():
            try:
                raw = self.asr_q.get(timeout=0.2)
            except queue.Empty:
                continue
            self._flush(raw)

    def _on_tts_status(self, msg: String):
        try:
            payload = json.loads(msg.data)
            state = payload.get("state")
        except Exception:
            return
        if state == "start":
            self._tts_speaking = True
        elif state == "end":
            self._tts_speaking = False
            self._tts_guard_until = time.monotonic() + self._tts_guard_tail_s
            # 게이트 해제 시점에 세그먼터가 직전 세그먼트를 이어받지 않도록
            # audio_q/세그먼트 잔재는 그대로 두되, 이후 프레임부터 정상 처리

    def _flush(self, raw: bytes):
        pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        duration_s = len(pcm) / self.sr
        t0 = time.time()
        try:
            segments, _info = self.asr.transcribe(
                pcm,
                language=self.language,
                vad_filter=False,
                beam_size=1,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
        except Exception as exc:
            self.get_logger().error(f"ASR failed: {exc}")
            return

        latency_ms = (time.time() - t0) * 1000.0
        if not text:
            self.get_logger().debug(
                f"Empty transcription ({duration_s:.2f}s, {latency_ms:.0f}ms)"
            )
            return

        # 화자 검증 — 결과는 payload 에 실어 보내고, 거부는 llm_dialogue_node 에서 처리
        speaker_verified = True
        speaker_score = 1.0
        if self._sv is not None:
            speaker_verified, speaker_score = self._sv.verify(pcm, self.sr)
            if not speaker_verified:
                self.get_logger().info(
                    f"[SV REJECT] score={speaker_score} text={text!r}"
                )

        payload = {
            "text": text,
            "timestamp_ns": time.time_ns(),
            "duration_s": round(duration_s, 3),
            "latency_ms": round(latency_ms, 1),
            "speaker_verified": speaker_verified,
            "speaker_score": speaker_score,
        }
        msg = String()
        msg.data = json.dumps(payload, ensure_ascii=False)
        self.pub.publish(msg)
        self.get_logger().info(f"[ASR {duration_s:.2f}s / {latency_ms:.0f}ms] {text}")

    def destroy_node(self):
        self._stop.set()
        return super().destroy_node()


def main():
    rclpy.init()
    node = AudioBridgeNode()
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
