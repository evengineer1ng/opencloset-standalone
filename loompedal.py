#!/usr/bin/env python3
# pedal.py
# Usage:
#   python pedal.py --carrier tts.mp3 --mod texture.mp3 --out crossed.wav --mode hybrid
#   python pedal.py -c tts.mp3 -m opera_heat.mp3 -o crossed.mp3 --mode vocoder --wet 0.75

import argparse, subprocess, tempfile, wave, os
import numpy as np

def run(cmd):
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def read_audio(path, sr=44100):
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    run(["ffmpeg", "-y", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "wav", tmp.name])
    with wave.open(tmp.name, "rb") as w:
        data = w.readframes(w.getnframes())
        x = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    os.unlink(tmp.name)
    return x

def write_audio(path, x, sr=44100):
    x = np.nan_to_num(x)
    x = x / max(1.0, np.max(np.abs(x)) * 1.02)
    if path.lower().endswith(".mp3"):
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        write_wav(tmp.name, x, sr)
        run(["ffmpeg", "-y", "-i", tmp.name, "-codec:a", "libmp3lame", "-b:a", "192k", path])
        os.unlink(tmp.name)
    else:
        write_wav(path, x, sr)

def write_wav(path, x, sr):
    y = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(y.tobytes())

def match_len(a, b):
    n = max(len(a), len(b))
    def pad(x):
        if len(x) >= n: return x[:n]
        reps = int(np.ceil(n / len(x)))
        return np.tile(x, reps)[:n]
    return pad(a), pad(b)

def smooth_env(x, attack=0.003, release=0.080, sr=44100):
    x = np.abs(x)
    out = np.zeros_like(x)
    a = np.exp(-1 / (attack * sr))
    r = np.exp(-1 / (release * sr))
    for i in range(1, len(x)):
        coef = a if x[i] > out[i-1] else r
        out[i] = coef * out[i-1] + (1 - coef) * x[i]
    out /= max(1e-9, np.percentile(out, 99))
    return np.clip(out, 0, 1.5)

def stft(x, n=2048, hop=512):
    win = np.hanning(n)
    frames = []
    for i in range(0, len(x) - n, hop):
        frames.append(np.fft.rfft(x[i:i+n] * win))
    return np.array(frames).T

def istft(S, n=2048, hop=512):
    win = np.hanning(n)
    length = (S.shape[1] - 1) * hop + n
    y = np.zeros(length)
    norm = np.zeros(length)
    for k in range(S.shape[1]):
        frame = np.fft.irfft(S[:, k], n)
        i = k * hop
        y[i:i+n] += frame * win
        norm[i:i+n] += win ** 2
    return y / np.maximum(norm, 1e-8)

def vocoder(carrier, mod, wet=0.7, n=2048, hop=512):
    C = stft(carrier, n, hop)
    M = stft(mod, n, hop)
    frames = min(C.shape[1], M.shape[1])
    C, M = C[:, :frames], M[:, :frames]

    c_mag = np.abs(C)
    m_mag = np.abs(M)
    phase = np.angle(C)

    # Normalize modulator spectrum per-frame so it acts as texture, not raw loudness.
    m_shape = m_mag / np.maximum(np.mean(m_mag, axis=0, keepdims=True), 1e-8)
    shaped = c_mag * (0.35 + 0.85 * m_shape)

    S = ((1 - wet) * c_mag + wet * shaped) * np.exp(1j * phase)
    return istft(S, n, hop)

def process(carrier, mod, mode="hybrid", wet=0.65, depth=0.85, sr=44100):
    carrier, mod = match_len(carrier, mod)

    env = smooth_env(mod, sr=sr)
    amp_mod = carrier * (1.0 - depth * 0.55 + env * depth * 0.9)

    mod_norm = mod / max(1e-9, np.max(np.abs(mod)))
    ring = carrier * (1 - wet) + (carrier * mod_norm) * wet

    voc = vocoder(carrier, mod, wet=wet)

    if mode == "amp":
        y = amp_mod
    elif mode == "ring":
        y = ring
    elif mode == "vocoder":
        y = voc
    elif mode == "hybrid":
        y = 0.55 * voc[:len(amp_mod)] + 0.30 * amp_mod[:len(voc)] + 0.15 * ring[:len(voc)]
    else:
        raise ValueError("mode must be amp, ring, vocoder, or hybrid")

    return y

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--carrier", required=True, help="TTS / spoken MP3")
    ap.add_argument("-m", "--mod", required=True, help="texture / opera heat MP3")
    ap.add_argument("-o", "--out", required=True, help="output .wav or .mp3")
    ap.add_argument("--mode", default="hybrid", choices=["amp", "ring", "vocoder", "hybrid"])
    ap.add_argument("--wet", type=float, default=0.70)
    ap.add_argument("--depth", type=float, default=0.85)
    ap.add_argument("--sr", type=int, default=44100)
    args = ap.parse_args()

    carrier = read_audio(args.carrier, args.sr)
    mod = read_audio(args.mod, args.sr)
    y = process(carrier, mod, args.mode, args.wet, args.depth, args.sr)
    write_audio(args.out, y, args.sr)
    print("wrote", args.out)

if __name__ == "__main__":
    main()