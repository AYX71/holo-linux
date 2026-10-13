#!/usr/bin/env python3
"""Holo for Linux: classify taps on the desk around a laptop into four zones.

Pipeline (mirrors the macOS Holo design):
  mic stream -> adaptive onset detector -> 90 ms window -> sustained/clip gate
  -> features (log-band spectrum + temporal envelope)
  -> ridge classifier + nearest-example novelty rejection -> action

Usage:
  python holo.py devices
  python holo.py calibrate [--device N]
  python holo.py eval      [--device N]
  python holo.py run       [--device N]
"""
import argparse
import json
import os
import queue
import random
import statistics
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np

SR = 48000
BLOCK = 480                      # 10 ms
WIN = int(0.090 * SR)            # 90 ms analysis window
ZONES = ["left_rear", "right_rear", "left_front", "right_front"]
CAL_PER_ZONE = 15
EVAL_PER_ZONE = 10

DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "holo"
CONF_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "holo"
PROFILE = DATA_DIR / "profile.json"
ACTIONS = CONF_DIR / "actions.json"

# Tunables
ONSET_RATIO = 6.0        # block RMS must exceed noise floor by this factor
ONSET_MIN_RMS = 0.003    # ...and this absolute level
CLIP_PEAK = 0.99
SUSTAIN_RATIO = 0.5      # late/early energy above this => speech-like, rejected
MIN_SCORE = 0.30         # classifier score needed for the top zone
MIN_MARGIN = 0.25        # gap between best and second-best score
NOVELTY_FACTOR = 2.5     # distance allowed relative to calibration spread
RIDGE = 3.0


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------
class Detector:
    """Streaming onset detector. feed() returns a 90 ms window or None."""

    def __init__(self):
        self.floor = None
        self.seen = 0
        self.learn_blocks = int(0.75 * SR / BLOCK)
        self.prev = deque(maxlen=1)
        self.collecting = None
        self.refractory = 0

    def feed(self, block):
        rms = float(np.sqrt(np.mean(block ** 2))) + 1e-9
        self.seen += 1

        if self.collecting is not None:
            self.collecting.append(block)
            if sum(len(b) for b in self.collecting) >= WIN:
                window = np.concatenate(self.collecting)[:WIN]
                self.collecting = None
                self.refractory = int(0.25 * SR / BLOCK)
                self.prev.clear()
                return window
            return None

        if self.floor is None:
            self.floor = rms
        if self.seen <= self.learn_blocks:           # room-learning period
            self.floor = 0.9 * self.floor + 0.1 * rms
            self.prev.append(block)
            return None
        if self.refractory > 0:
            self.refractory -= 1
            self.floor = 0.98 * self.floor + 0.02 * min(rms, self.floor * 3)
            self.prev.append(block)
            return None

        if rms > max(self.floor * ONSET_RATIO, ONSET_MIN_RMS):
            self.collecting = list(self.prev) + [block]
            return None

        self.floor = 0.995 * self.floor + 0.005 * rms
        self.prev.append(block)
        return None


def gate(window):
    """Return a human-readable rejection reason, or None if the window is OK."""
    if np.max(np.abs(window)) >= CLIP_PEAK:
        return "clipped: tap more lightly"
    e = window ** 2
    early = e[: int(0.3 * WIN)].sum() + 1e-12
    late = e[int(0.6 * WIN):].sum()
    if late > SUSTAIN_RATIO * early:
        return "sustained sound (speech/noise?), ignored"
    return None


# --------------------------------------------------------------------------
# Features
# --------------------------------------------------------------------------
_EDGES = np.geomspace(100, 16000, 33)
_FREQS = np.fft.rfftfreq(WIN, 1 / SR)
_MASKS = [(_FREQS >= lo) & (_FREQS < hi) for lo, hi in zip(_EDGES[:-1], _EDGES[1:])]
_HANN = np.hanning(WIN)


def features(window):
    w = window - window.mean()
    spec = np.abs(np.fft.rfft(w * _HANN)) ** 2
    bands = np.array([spec[m].sum() for m in _MASKS]) + 1e-12
    logb = np.log(bands)
    logb -= logb.mean()                     # remove overall loudness
    seg = np.array_split(w, 12)
    env = np.log(np.array([np.mean(s ** 2) for s in seg]) + 1e-12)
    env -= env.max()
    return np.concatenate([logb, env])


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------
class Model:
    def __init__(self):
        self.med = self.scale = self.W = None
        self.train = {}       # zone -> normalized training vectors
        self.radius = {}      # zone -> allowed nearest-neighbor distance

    def _norm(self, X):
        return (np.asarray(X) - self.med) / self.scale

    def fit(self, X, y):
        X = np.asarray(X, float)
        self.med = np.median(X, axis=0)
        iqr = np.subtract(*np.percentile(X, [75, 25], axis=0))
        self.scale = np.where(iqr < 1e-3, 1.0, iqr)
        Z = self._norm(X)
        idx = np.array([ZONES.index(z) for z in y])
        T = -np.ones((len(y), len(ZONES)))
        T[np.arange(len(y)), idx] = 1.0
        A = np.hstack([Z, np.ones((len(Z), 1))])
        reg = RIDGE * np.eye(A.shape[1])
        reg[-1, -1] = 0                      # don't penalize the bias
        self.W = np.linalg.solve(A.T @ A + reg, A.T @ T)

        self.train, self.radius = {}, {}
        for i, zone in enumerate(ZONES):
            Zi = Z[idx == i]
            self.train[zone] = Zi
            d = np.linalg.norm(Zi[:, None] - Zi[None], axis=2)
            np.fill_diagonal(d, np.inf)
            nn = d.min(axis=1)
            self.radius[zone] = float(np.max(nn))   # raw; factor applied at predict
        return self

    def predict(self, x):
        """Return (zone or None, reason, scores)."""
        z = self._norm(x)
        scores = np.append(z, 1.0) @ self.W
        order = np.argsort(scores)[::-1]
        best, second = order[0], order[1]
        zone = ZONES[best]
        if scores[best] < MIN_SCORE:
            return None, "low confidence", scores
        if scores[best] - scores[second] < MIN_MARGIN:
            return None, "ambiguous", scores
        dist = float(np.min(np.linalg.norm(self.train[zone] - z, axis=1)))
        if dist > self.radius[zone] * NOVELTY_FACTOR:
            return None, "unlike calibration taps", scores
        return zone, "ok", scores

    def to_json(self):
        return {
            "med": self.med.tolist(), "scale": self.scale.tolist(),
            "W": self.W.tolist(),
            "train": {k: v.tolist() for k, v in self.train.items()},
            "radius": self.radius,
        }

    @classmethod
    def from_json(cls, d):
        m = cls()
        m.med, m.scale = np.array(d["med"]), np.array(d["scale"])
        m.W = np.array(d["W"])
        m.train = {k: np.array(v) for k, v in d["train"].items()}
        m.radius = d["radius"]
        return m


def leave_one_out(X, y):
    X = np.asarray(X)
    hits = per = 0
    per_zone = {z: [0, 0] for z in ZONES}
    for i in range(len(X)):
        keep = np.arange(len(X)) != i
        m = Model().fit(X[keep], [y[j] for j in range(len(y)) if keep[j]])
        scores = np.append(m._norm(X[i]), 1.0) @ m.W
        pred = ZONES[int(np.argmax(scores))]
        per_zone[y[i]][1] += 1
        if pred == y[i]:
            per_zone[y[i]][0] += 1
            hits += 1
    return hits / len(X), {z: a / max(b, 1) for z, (a, b) in per_zone.items()}


# --------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------
class Listener:
    """Owns the input stream; yields detected tap windows."""

    def __init__(self, device=None):
        import sounddevice as sd
        self.q = queue.Queue()
        self.detector = Detector()
        self.stream = sd.InputStream(
            samplerate=SR, blocksize=BLOCK, channels=1, dtype="float32",
            device=device, callback=self._cb)

    def _cb(self, indata, frames, t, status):
        self.q.put((time.monotonic(), indata[:, 0].copy()))

    def __enter__(self):
        self.stream.start()
        return self

    def __exit__(self, *a):
        self.stream.stop()
        self.stream.close()

    def drain(self):
        while not self.q.empty():
            self.q.get_nowait()
        self.detector = Detector()
        time.sleep(0.9)          # let the detector relearn the room
        while not self.q.empty():
            self.q.get_nowait()

    def next_window(self, timeout=None):
        """Block until a window is detected. Returns (window, t_done)."""
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            try:
                t, block = self.q.get(timeout=0.2)
            except queue.Empty:
                if end and time.monotonic() > end:
                    return None, None
                continue
            w = self.detector.feed(block)
            if w is not None:
                return w, t


def collect(listener, n, label):
    feats = []
    listener.detector = Detector()
    listener.drain()
    print(f"  Listening... tap {n} times around the {label} area, pausing between taps.")
    while len(feats) < n:
        w, _ = listener.next_window()
        reason = gate(w)
        if reason:
            print(f"    skipped: {reason}")
            continue
        feats.append(features(w))
        print(f"    accepted {len(feats)}/{n}")
    return feats


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_devices(_):
    import sounddevice as sd
    print(sd.query_devices())


def cmd_calibrate(args):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print("Keep the laptop exactly where it will stay. Moving it invalidates the profile.\n")
    X, y = [], []
    with Listener(args.device) as ls:
        for zone in ZONES:
            input(f"Zone '{zone}': press Enter, then start tapping. ")
            for f in collect(ls, CAL_PER_ZONE, zone):
                X.append(f)
                y.append(zone)
            time.sleep(0.5)
    acc, per = leave_one_out(X, y)
    print(f"\nLeave-one-out agreement: {acc:.0%}")
    for z, a in per.items():
        print(f"  {z:12s} {a:.0%}")
    weakest = min(per, key=per.get)
    if per[weakest] < 0.8:
        print(f"Weak zone: {weakest}. Consider recalibrating before relying on it.")
    model = Model().fit(X, y)
    PROFILE.write_text(json.dumps(model.to_json()))
    print(f"Saved profile to {PROFILE}")


def load_model():
    if not PROFILE.exists():
        sys.exit("No profile. Run: python holo.py calibrate")
    return Model.from_json(json.loads(PROFILE.read_text()))


def cmd_eval(args):
    model = load_model()
    order = [z for z in ZONES for _ in range(EVAL_PER_ZONE)]
    random.shuffle(order)
    confusion = {z: {p: 0 for p in ZONES + ["rejected"]} for z in ZONES}
    latencies = []
    with Listener(args.device) as ls:
        ls.drain()
        for i, zone in enumerate(order, 1):
            print(f"[{i}/{len(order)}] tap: {zone}")
            while True:
                w, t_block = ls.next_window()
                t0 = time.monotonic()
                if gate(w):
                    continue                      # not a clean tap, try again
                pred, why, _ = model.predict(features(w))
                if pred is None:
                    print(f"    rejected: {why}")
                latencies.append(
                    (time.monotonic() - t_block) * 1000)   # includes 90 ms window fill
                confusion[zone][pred or "rejected"] += 1
                break
    correct = sum(confusion[z][z] for z in ZONES)
    total = len(order)
    print(f"\nOverall accuracy: {correct / total:.0%}  (target >= 80%)")
    for z in ZONES:
        n = sum(confusion[z].values())
        print(f"  {z:12s} {confusion[z][z] / n:.0%}   {confusion[z]}")
    med = statistics.median(latencies)
    print(f"Median latency (window start of last block -> decision): {med:.0f} ms  (target < 200)")
    out = DATA_DIR / f"eval-{int(time.time())}.json"
    out.write_text(json.dumps({"accuracy": correct / total, "confusion": confusion,
                               "latencies_ms": latencies}))
    print(f"Saved {out}")


_last_fired = {}
COOLDOWN = 1.5   # seconds before the same zone can fire again


def run_action(zone):
    if not ACTIONS.exists():
        return
    now = time.monotonic()
    if now - _last_fired.get(zone, -1e9) < COOLDOWN:
        return
    _last_fired[zone] = now
    cmd = json.loads(ACTIONS.read_text()).get(zone)
    if cmd:
        subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cmd_run(args):
    model = load_model()
    print("Running. Ctrl+C to stop. Actions come from", ACTIONS)
    with Listener(args.device) as ls:
        ls.drain()
        try:
            while True:
                w, _ = ls.next_window()
                reason = gate(w)
                if reason:
                    continue
                zone, why, scores = model.predict(features(w))
                if zone:
                    print(f"{zone}  (score {scores[ZONES.index(zone)]:.2f})")
                    run_action(zone)
                else:
                    print(f"rejected: {why}")
        except KeyboardInterrupt:
            print("\nStopped.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in [("devices", cmd_devices), ("calibrate", cmd_calibrate),
                     ("eval", cmd_eval), ("run", cmd_run)]:
        sp = sub.add_parser(name)
        sp.add_argument("--device", type=int, default=None)
        sp.set_defaults(fn=fn)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
