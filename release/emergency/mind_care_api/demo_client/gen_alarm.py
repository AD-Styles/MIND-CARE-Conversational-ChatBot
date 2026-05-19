"""사이렌 WAV 생성 — 두 주파수 교대 (유럽식 응급 사이렌)."""
import math
import struct
import sys
import wave

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/alarm.wav"
SR = 22050                  # sample rate
DUR = 2.0                   # seconds
F_HI, F_LO = 900.0, 600.0   # Hz
PERIOD = 0.4                # one hi+lo cycle ≈ 0.4s → 5 hi/lo per 2s
ATTACK = 0.005              # 5ms attack/decay to prevent clicks
AMP = 0.7                   # 0..1

n = int(SR * DUR)
samples = []
for i in range(n):
    t = i / SR
    # 0..PERIOD/2 → HI, PERIOD/2..PERIOD → LO
    phase = (t % PERIOD) / PERIOD
    f = F_HI if phase < 0.5 else F_LO
    s = math.sin(2 * math.pi * f * t)
    # envelope to avoid pop at boundary
    env = 1.0
    if t < ATTACK:
        env = t / ATTACK
    if t > DUR - ATTACK:
        env = (DUR - t) / ATTACK
    samples.append(int(s * env * AMP * 32767))

with wave.open(OUT, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(b"".join(struct.pack("<h", s) for s in samples))

print(f"wrote {OUT}: {DUR}s, {SR}Hz, ~{n*2/1024:.1f} KB")
