"""tts_node.py

/llm/responses를 구독하여 TTS로 합성하고 스피커로 재생한다.

백엔드는 파라미터로 선택:
  tts_backend = "edge"   : Microsoft Edge TTS (온라인, 고품질, 무료) ★ 기본
  tts_backend = "melo"   : MeloTTS-Korean (오프라인, 고품질, torch 필요)
  tts_backend = "coqui"  : Coqui TTS (VITS 한국어, 오프라인)
  tts_backend = "espeak" : espeak-ng 폴백 (오프라인, 저품질)

모델은 최초 호출 시 lazy-load.

발행 토픽
  /tts/status (std_msgs/String)
    JSON: {"state": "start"|"end", "turn_id": int, "latency_ms": float}
"""

import json
import queue
import re
import threading
import time

import numpy as np
import rclpy
import sounddevice as sd
from rclpy.node import Node
from std_msgs.msg import String


class TtsNode(Node):
    def __init__(self):
        super().__init__("tts_node")

        self.declare_parameter("tts_backend", "edge")
        self.declare_parameter(
            "coqui_model", "tts_models/ko/kss/tacotron2-DDC"
        )  # TODO: VITS-KR 모델로 교체
        # Edge TTS voice. 추천:
        #   ko-KR-SunHiNeural    (여성, 따뜻하고 차분)
        #   ko-KR-InJoonNeural   (남성, 안정적)
        #   ko-KR-BongJinNeural  (남성, 활기)
        #   ko-KR-GookMinNeural  (남성, 중후)
        #   ko-KR-JiMinNeural    (여성, 밝음)
        self.declare_parameter("edge_voice", "ko-KR-SunHiNeural")
        self.declare_parameter("edge_rate", "+0%")     # 예: "-10%" 느리게
        self.declare_parameter("edge_pitch", "+0Hz")
        # MeloTTS 설정
        self.declare_parameter("melo_language", "KR")   # KR / EN / JP / ZH / ES / FR
        self.declare_parameter("melo_speaker", "KR")
        self.declare_parameter("melo_speed", 1.0)
        self.declare_parameter("device", "cpu")
        self.declare_parameter("output_device", -1)
        self.declare_parameter("sample_rate_hint", 22050)
        self.declare_parameter("speaking_rate", 1.0)
        self.declare_parameter("drop_if_busy", True)

        self.backend = self.get_parameter("tts_backend").value
        self.out_device = int(self.get_parameter("output_device").value)
        self.out_device_arg = None if self.out_device < 0 else self.out_device
        self.drop_if_busy = bool(self.get_parameter("drop_if_busy").value)

        self._engine = None
        self._engine_sr = int(self.get_parameter("sample_rate_hint").value)
        self._play_lock = threading.Lock()

        self._queue: "queue.Queue[dict]" = queue.Queue(maxsize=4)
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

        self.sub = self.create_subscription(
            String, "/llm/responses", self._on_response, 10
        )
        self.pub_status = self.create_publisher(String, "/tts/status", 10)

        self.get_logger().info(
            f"TTS node ready. backend={self.backend}, "
            f"output_device={self.out_device_arg}"
        )

    def _lazy_load(self):
        if self._engine is not None:
            return

        if self.backend == "edge":
            import edge_tts  # noqa: F401
            self._engine = "edge"
            # Edge TTS는 24kHz mp3/wav를 반환 (soundfile로 디코드)
            self._engine_sr = 24000
            self.get_logger().info(
                f"Edge TTS ready. voice={self.get_parameter('edge_voice').value}, "
                f"rate={self.get_parameter('edge_rate').value}"
            )

        elif self.backend == "melo":
            from melo.api import TTS as MeloAPI

            language = self.get_parameter("melo_language").value
            device = self.get_parameter("device").value
            self.get_logger().info(
                f"Loading MeloTTS: language={language}, device={device} "
                f"(최초 실행 시 ~300MB 체크포인트 다운로드)"
            )
            self._engine = MeloAPI(language=language, device=device)
            self._melo_spk_id = self._engine.hps.data.spk2id[
                self.get_parameter("melo_speaker").value
            ]
            self._engine_sr = int(self._engine.hps.data.sampling_rate)
            self.get_logger().info(
                f"MeloTTS ready. speaker_id={self._melo_spk_id}, sr={self._engine_sr}"
            )

        elif self.backend == "coqui":
            from TTS.api import TTS

            model_name = self.get_parameter("coqui_model").value
            device = self.get_parameter("device").value
            self.get_logger().info(f"Loading Coqui TTS: {model_name} on {device}")
            self._engine = TTS(model_name=model_name).to(device)
            self._engine_sr = int(self._engine.synthesizer.output_sample_rate)

        elif self.backend == "espeak":
            import subprocess  # noqa: F401

            self._engine = "espeak"
            self._engine_sr = 22050

        else:
            raise ValueError(f"Unknown tts_backend: {self.backend}")

    def _synthesize(self, text: str) -> np.ndarray:
        self._lazy_load()

        if self.backend == "edge":
            return self._synth_edge(text)

        if self.backend == "melo":
            return self._synth_melo(text)

        if self.backend == "coqui":
            wav = self._engine.tts(text)
            return np.asarray(wav, dtype=np.float32)

        if self.backend == "espeak":
            import subprocess
            proc = subprocess.run(
                ["espeak-ng", "-v", "ko", "--stdout", text],
                capture_output=True, check=True,
            )
            # WAV 헤더 44바이트 제거 후 int16 → float32
            raw = proc.stdout[44:]
            pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            return pcm

        raise RuntimeError(f"no synth path for {self.backend}")

    def _synth_edge(self, text: str) -> np.ndarray:
        """edge-tts로 mp3 받아 PCM float32 복원."""
        import asyncio
        import io

        import edge_tts
        import soundfile as sf

        voice = self.get_parameter("edge_voice").value
        rate = self.get_parameter("edge_rate").value
        pitch = self.get_parameter("edge_pitch").value

        async def _collect() -> bytes:
            communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
            buf = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.extend(chunk["data"])
            return bytes(buf)

        mp3_bytes = asyncio.run(_collect())
        if not mp3_bytes:
            raise RuntimeError("Edge TTS returned empty audio")

        data, sr = sf.read(io.BytesIO(mp3_bytes), dtype="float32")
        if data.ndim > 1:  # 스테레오면 모노 평균
            data = data.mean(axis=1)
        self._engine_sr = int(sr)
        return data

    def _synth_melo(self, text: str) -> np.ndarray:
        """MeloTTS로 WAV 생성 후 PCM float32 반환."""
        import os
        import tempfile

        import soundfile as sf

        speed = float(self.get_parameter("melo_speed").value)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            out_path = tf.name
        try:
            self._engine.tts_to_file(
                text,
                self._melo_spk_id,
                out_path,
                speed=speed,
            )
            data, sr = sf.read(out_path, dtype="float32")
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

        if data.ndim > 1:
            data = data.mean(axis=1)
        self._engine_sr = int(sr)
        return data

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """LLM 응답의 마크다운 기호 제거 — TTS 가 '별표별표' 처럼 읽지 않게."""
        text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)  # 코드블록
        text = re.sub(r"`([^`]*)`", r"\1", text)                  # 인라인 코드
        text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)      # **굵게** *기울임*
        text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)        # __밑줄__
        text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)  # 헤더
        text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)       # 불릿
        text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)       # 번호목록
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)      # [링크](url)
        text = re.sub(r"[ \t]*\n[ \t]*", " ", text)              # 줄바꿈 → 공백
        return re.sub(r"\s{2,}", " ", text).strip()

    def _on_response(self, msg: String):
        try:
            payload = json.loads(msg.data)
            text = self._strip_markdown((payload.get("text") or "").strip())
            turn_id = int(payload.get("turn_id", -1))
        except Exception as exc:
            self.get_logger().warn(f"Bad LLM payload: {exc}")
            return

        if not text:
            return

        item = {"text": text, "turn_id": turn_id}
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            if self.drop_if_busy:
                self.get_logger().warn("TTS queue full; dropping")
            else:
                self._queue.put(item)

    def _worker_loop(self):
        while not self._stop.is_set() and rclpy.ok():
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            text = item["text"]
            turn_id = item["turn_id"]
            t0 = time.time()
            try:
                pcm = self._synthesize(text)
            except Exception as exc:
                self.get_logger().error(f"TTS synth failed: {exc}")
                continue

            synth_ms = (time.time() - t0) * 1000.0
            self._publish_status("start", turn_id, synth_ms)
            try:
                with self._play_lock:
                    self._play_pcm(pcm)
            except Exception as exc:
                self.get_logger().error(f"Audio playback failed: {exc}")
            finally:
                self._publish_status("end", turn_id, synth_ms)
                self.get_logger().info(
                    f"[TTS turn#{turn_id} synth={synth_ms:.0f}ms "
                    f"len={len(pcm)/self._engine_sr:.2f}s] {text}"
                )

    def _play_pcm(self, pcm: np.ndarray):
        """WSL/PulseAudio underrun 방지:
        - 명시적 OutputStream + latency='high' (더 큰 버퍼)
        - 앞뒤 50ms 무음 패딩 (클릭·초기 underrun 완화)
        - blocksize 단위 쓰기로 백프레셔 활용
        """
        if pcm.dtype != np.float32:
            pcm = pcm.astype(np.float32)
        if pcm.ndim > 1:
            pcm = pcm.mean(axis=1).astype(np.float32)

        pad = np.zeros(int(self._engine_sr * 0.05), dtype=np.float32)
        padded = np.concatenate([pad, pcm, pad])

        blocksize = int(self._engine_sr * 0.04)  # 40ms
        stream = sd.OutputStream(
            samplerate=self._engine_sr,
            channels=1,
            dtype="float32",
            device=self.out_device_arg,
            latency="high",
            blocksize=blocksize,
        )
        stream.start()
        try:
            # 조각 단위로 쓰기 — PulseAudio 브리지의 작은 버퍼 대응
            n = len(padded)
            i = 0
            while i < n:
                chunk = padded[i:i + blocksize]
                stream.write(chunk)
                i += blocksize
        finally:
            stream.stop()
            stream.close()

    def _publish_status(self, state: str, turn_id: int, latency_ms: float):
        msg = String()
        msg.data = json.dumps({
            "state": state,
            "turn_id": turn_id,
            "latency_ms": round(latency_ms, 1),
            "timestamp_ns": time.time_ns(),
        }, ensure_ascii=False)
        self.pub_status.publish(msg)

    def destroy_node(self):
        self._stop.set()
        return super().destroy_node()


def main():
    rclpy.init()
    node = TtsNode()
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
