"""
Psychophysics experiment — real ultrasound vs sham audio.
=========================================================

Each trial presents ONE 5-second stimulus drawn from the selected conditions
and asks the subject to rate perceived intensity (1 = none, 5 = very strong).
Trial order is randomized across conditions.

Conditions you can include:
  - `real_us`                      — actual ultrasound pulses (driven by the Siglent AWG)
  - `sham_crackle_n<N>_hp<F>Hz_..` — one of the pre-generated static-crackle
                                     sham WAVs in `sham_audio_static_crackle/`
                                     (varies clicks-per-pulse and high-pass
                                     cutoff across files, see gen_sham_audio.py)

Results are written to a per-participant TSV in `out/`:
    out/psychophysics-<participant>-<timestamp>.tsv

Usage
-----
Interactive (recommended):
    python scripts/run_psychophysics.py

Scripted (accept all defaults, mock hardware, all sham files as conditions):
    python scripts/run_psychophysics.py --participant P01 --all-sham --no-real
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

try:
    import utils.sg33500B as sg  # Siglent SDG33500B control wrapper
except Exception:
    sg = None

###############################################################################
# Defaults
###############################################################################

SHAM_DIR = ROOT_DIR / "sham_audio_static_crackle"
DEFAULT_LOG_DIR = ROOT_DIR / "out"

# Ultrasound parameters — match run_stimulus.py defaults
DEFAULT_PRF_HZ = 5
DEFAULT_DUTY_CYCLE = 0.1
DEFAULT_VPP_MV = 297
DEFAULT_FREQ_KHZ = 265

# Experiment parameters
DEFAULT_TRIAL_DURATION_S = 5
DEFAULT_TRIALS_PER_COND = 5
DEFAULT_ITI_S = 3.0

RATING_PROMPT = (
    "  Rate perceived intensity "
    "(1=none, 2=weak, 3=moderate, 4=strong, 5=very strong)"
)

###############################################################################
# Data structures
###############################################################################

@dataclass
class Trial:
    index: int
    condition: str                  # "real_us" or the WAV filename stem
    stimulus_path: Optional[Path]   # WAV path for sham; None for real_us


@dataclass
class ExperimentConfig:
    participant: str
    mock_hardware: bool
    sham_paths: List[Path]
    include_real: bool
    trials_per_cond: int
    trial_duration_s: int
    iti_s: float
    prf_hz: int
    duty_cycle: float
    vpp_mv: int
    freq_khz: int
    log_dir: Path = DEFAULT_LOG_DIR

###############################################################################
# Stimulators
###############################################################################

class FusStimulator:
    """Minimal ultrasound driver: open once, pulse for N seconds per trial."""

    def __init__(self, mock: bool, prf_hz: int, duty: float, vpp: int, freq_khz: int):
        self.mock = mock
        self.prf_hz = prf_hz
        self.duty = duty
        self.vpp = vpp
        self.freq_khz = freq_khz
        self._serial = None
        self._interval_s = 1.0 / prf_hz
        self._burst_ms = int((1000.0 / prf_hz) * duty)
        if self._burst_ms <= 0:
            raise ValueError("Duty cycle and PRF result in <1 ms burst. Increase duty or PRF.")

    def open(self) -> None:
        if self.mock:
            print("[INFO] FUS mock open.")
            return
        if sg is None:
            raise RuntimeError(
                "Hardware driver 'sg33500B' could not be loaded. "
                "Rerun with mock hardware or check pip install -r requirements.txt."
            )
        print("[INFO] Opening AWG serial connection...")
        self._serial = sg.OpenSerial()
        ok = sg.uploadNewUSparameters(
            centerFreq_kHz=self.freq_khz,
            mode=1,
            inputmVpp=self.vpp,
            stimDur_ms=self._burst_ms,
            PRF_kHz=self.prf_hz / 1000.0,
            dutyCycle=self.duty,
        )
        if ok < 0:
            raise RuntimeError("AWG parameter upload failed.")

    def play(self, duration_s: float) -> None:
        """Fire triggers at PRF for duration_s seconds. Blocks until done."""
        end = time.time() + duration_s
        next_t = time.time()
        while time.time() < end:
            now = time.time()
            if now >= next_t:
                if not self.mock:
                    sg.triggerFUS(self._serial)
                next_t += self._interval_s
            time.sleep(0.001)

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None


class ShamStimulator:
    """pygame.mixer wrapper that plays a WAV for a fixed duration."""

    def __init__(self):
        try:
            import pygame
        except ImportError as exc:
            raise RuntimeError(
                "pygame is required for sham audio playback. "
                "Install it with `pip install pygame`."
            ) from exc
        self._pg = pygame
        self._pg.mixer.init()
        self._cache: dict = {}

    def play(self, wav_path: Path, duration_s: float) -> None:
        if wav_path not in self._cache:
            self._cache[wav_path] = self._pg.mixer.Sound(str(wav_path))
        snd = self._cache[wav_path]
        snd.play(loops=-1)
        time.sleep(duration_s)
        snd.stop()

    def close(self) -> None:
        self._pg.mixer.quit()

###############################################################################
# Prompt helpers (match run_stimulus.py style)
###############################################################################

def _prompt(text: str, default, caster=str, validator=None):
    shown = f"{text} [{default}]: " if default is not None else f"{text}: "
    while True:
        raw = input(shown).strip()
        if raw == "":
            if default is None:
                print("  ! this field is required; try again.")
                continue
            return default
        try:
            val = caster(raw)
        except (ValueError, TypeError) as exc:
            print(f"  ! invalid value ({exc}); try again.")
            continue
        if validator is not None:
            err = validator(val)
            if err:
                print(f"  ! {err}; try again.")
                continue
        return val


def _prompt_yes_no(text: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input(f"{text} {suffix}: ").strip().lower()
        if raw == "":
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  ! please answer y or n.")


def _prompt_multiselect(text: str, options: List[str]) -> List[str]:
    """Pick a subset of labeled options. Returns the list of chosen labels."""
    print(text)
    for i, label in enumerate(options, start=1):
        print(f"  [{i:>2}] {label}")
    print("  [A] all    [enter] skip")
    while True:
        raw = input("Select (comma-separated indices, 'A' for all): ").strip().lower()
        if raw == "":
            return []
        if raw in ("a", "all"):
            return list(options)
        try:
            parts = [int(x.strip()) for x in raw.split(",") if x.strip()]
            if parts and all(1 <= p <= len(options) for p in parts):
                # Preserve user-typed order, drop duplicates
                seen = set()
                chosen = []
                for p in parts:
                    if p not in seen:
                        chosen.append(options[p - 1])
                        seen.add(p)
                return chosen
        except ValueError:
            pass
        print("  ! invalid; try again.")

###############################################################################
# Setup
###############################################################################

def discover_sham_files() -> List[Path]:
    """Return the list of available sham WAVs, sorted by filename."""
    if not SHAM_DIR.is_dir():
        return []
    return sorted(SHAM_DIR.glob("*.wav"))


def interactive_setup() -> ExperimentConfig:
    print("=" * 64)
    print("  Psychophysics: Ultrasound vs Sham Audio — Interactive Setup")
    print("=" * 64)

    participant = _prompt("\nParticipant ID", "P01", str,
                          lambda v: None if v else "cannot be empty")
    mock_hardware = not _prompt_yes_no("Use REAL ultrasound hardware?", default=False)

    sham_files = discover_sham_files()
    if not sham_files:
        print(f"[ERROR] No sham WAV files in {SHAM_DIR}.")
        print("        Run `python scripts/gen_sham_audio.py` to generate them first.")
        sys.exit(1)

    labels = [f.stem for f in sham_files]
    print()
    chosen_labels = _prompt_multiselect(
        "Select sham audio conditions to include "
        f"(each is a different carrier frequency — {len(labels)} available):",
        labels,
    )
    chosen_paths = [sham_files[labels.index(lbl)] for lbl in chosen_labels]

    print()
    include_real = _prompt_yes_no(
        "Include REAL ultrasound as a condition?", default=True
    )

    if not chosen_paths and not include_real:
        print("[ERROR] Must select at least one condition (real US or a sham).")
        sys.exit(1)

    print()
    trials_per_cond = _prompt("Trials per condition", DEFAULT_TRIALS_PER_COND, int,
                              lambda v: None if v > 0 else "must be > 0")
    trial_duration_s = _prompt("Trial duration (s)", DEFAULT_TRIAL_DURATION_S, int,
                               lambda v: None if v > 0 else "must be > 0")
    iti_s = _prompt("Inter-trial interval (s)", DEFAULT_ITI_S, float,
                    lambda v: None if v >= 0 else "must be >= 0")

    if include_real:
        print("\nUltrasound parameters for the `real_us` condition:")
        prf_hz = _prompt("  PRF (Hz)", DEFAULT_PRF_HZ, int,
                         lambda v: None if v > 0 else "must be > 0")
        duty_cycle = _prompt("  Duty cycle (0-1)", DEFAULT_DUTY_CYCLE, float,
                             lambda v: None if 0 < v <= 1 else "must be in (0, 1]")
        vpp_mv = _prompt("  Voltage (mVpp)", DEFAULT_VPP_MV, int,
                         lambda v: None if v > 0 else "must be > 0")
        freq_khz = _prompt("  Carrier frequency (kHz)", DEFAULT_FREQ_KHZ, int,
                           lambda v: None if v > 0 else "must be > 0")
    else:
        prf_hz, duty_cycle, vpp_mv, freq_khz = (
            DEFAULT_PRF_HZ, DEFAULT_DUTY_CYCLE, DEFAULT_VPP_MV, DEFAULT_FREQ_KHZ
        )

    cfg = ExperimentConfig(
        participant=participant,
        mock_hardware=mock_hardware,
        sham_paths=chosen_paths,
        include_real=include_real,
        trials_per_cond=trials_per_cond,
        trial_duration_s=trial_duration_s,
        iti_s=iti_s,
        prf_hz=prf_hz,
        duty_cycle=duty_cycle,
        vpp_mv=vpp_mv,
        freq_khz=freq_khz,
    )

    _print_summary(cfg)
    if not _prompt_yes_no("Proceed?", default=True):
        print("[INFO] Aborted by user at confirmation.")
        sys.exit(0)
    return cfg


def _print_summary(cfg: ExperimentConfig) -> None:
    n_conditions = len(cfg.sham_paths) + (1 if cfg.include_real else 0)
    total_trials = n_conditions * cfg.trials_per_cond
    est_minutes = total_trials * (cfg.trial_duration_s + cfg.iti_s) / 60.0

    print("\n" + "-" * 64)
    print("  Session summary")
    print("-" * 64)
    print(f"  Participant        : {cfg.participant}")
    print(f"  Hardware           : {'MOCK' if cfg.mock_hardware else 'REAL (Siglent AWG)'}")
    print(f"  Conditions         : {n_conditions}")
    if cfg.include_real:
        print("    - real_us")
    for p in cfg.sham_paths:
        print(f"    - {p.stem}")
    print(f"  Trials / condition : {cfg.trials_per_cond}")
    print(f"  Total trials       : {total_trials}")
    print(f"  Trial duration     : {cfg.trial_duration_s}s")
    print(f"  ITI                : {cfg.iti_s}s")
    print(f"  Estimated runtime  : ~{est_minutes:.1f} min")
    if cfg.include_real:
        print(f"  US parameters      : {cfg.prf_hz} Hz PRF, "
              f"{cfg.duty_cycle*100:.1f}% duty, {cfg.vpp_mv} mVpp, "
              f"{cfg.freq_khz} kHz carrier")
    print("-" * 64)

###############################################################################
# Experiment loop
###############################################################################

def build_trial_sequence(cfg: ExperimentConfig) -> List[Trial]:
    """Produce a randomized list of Trials across all conditions."""
    pairs: List[tuple[str, Optional[Path]]] = []
    if cfg.include_real:
        pairs.extend([("real_us", None)] * cfg.trials_per_cond)
    for p in cfg.sham_paths:
        pairs.extend([(p.stem, p)] * cfg.trials_per_cond)
    random.shuffle(pairs)
    return [Trial(index=i + 1, condition=cond, stimulus_path=path)
            for i, (cond, path) in enumerate(pairs)]


def _countdown(seconds: int = 3) -> None:
    for n in range(seconds, 0, -1):
        print(f"  {n}...", end=" ", flush=True)
        time.sleep(1)
    print("GO")


def run_experiment(cfg: ExperimentConfig) -> None:
    trials = build_trial_sequence(cfg)

    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d-%H.%M.%S")
    log_path = cfg.log_dir / f"psychophysics-{cfg.participant}-{stamp}.tsv"

    fus: Optional[FusStimulator] = None
    sham: Optional[ShamStimulator] = None
    if cfg.include_real:
        fus = FusStimulator(cfg.mock_hardware, cfg.prf_hz, cfg.duty_cycle,
                            cfg.vpp_mv, cfg.freq_khz)
    if cfg.sham_paths:
        sham = ShamStimulator()

    try:
        if fus is not None:
            fus.open()

        with log_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter="\t", lineterminator="\n")
            writer.writerow([
                "trial", "condition", "stimulus_file",
                "rating", "response_time_s", "timestamp",
            ])

            print(f"\n[INFO] Starting {len(trials)} trials.")
            print(f"[INFO] Log file: {log_path}")
            print("[INFO] Ctrl+C to abort (partial results are saved).\n")
            input("Press Enter when ready to begin...")

            for trial in trials:
                print(f"\n--- Trial {trial.index}/{len(trials)} ---")
                _countdown(3)

                if trial.condition == "real_us":
                    fus.play(cfg.trial_duration_s)
                else:
                    sham.play(trial.stimulus_path, cfg.trial_duration_s)

                print("  [stimulus ended]")
                ask_t0 = time.time()
                rating = _prompt(
                    RATING_PROMPT,
                    default=None,
                    caster=int,
                    validator=lambda v: None if 1 <= v <= 5 else "must be 1-5",
                )
                rt = time.time() - ask_t0

                writer.writerow([
                    trial.index,
                    trial.condition,
                    trial.stimulus_path.name if trial.stimulus_path else "-",
                    rating,
                    f"{rt:.3f}",
                    _dt.datetime.now().isoformat(timespec="seconds"),
                ])
                f.flush()

                if trial.index < len(trials):
                    time.sleep(cfg.iti_s)

        print(f"\n[INFO] Experiment complete — {len(trials)} trials recorded.")
        print(f"[INFO] Results: {log_path}")
    except KeyboardInterrupt:
        print(f"\n[INFO] Aborted. Partial results saved to {log_path}")
    finally:
        if fus is not None:
            fus.close()
        if sham is not None:
            sham.close()

###############################################################################
# CLI entry point
###############################################################################

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Psychophysics experiment: real ultrasound vs sham audio."
    )
    parser.add_argument(
        "--interactive", action="store_true",
        help="Force interactive prompts even if other flags are given"
    )
    parser.add_argument(
        "--participant", type=str,
        help="Participant ID (skips interactive setup when combined with other flags)"
    )
    parser.add_argument(
        "--real", action="store_true",
        help="Connect to AWG hardware (default = mock mode)"
    )
    parser.add_argument(
        "--no-real", action="store_true",
        help="Exclude the `real_us` condition"
    )
    parser.add_argument(
        "--all-sham", action="store_true",
        help="Include every WAV in sham_audio_static_crackle/ as a condition"
    )
    parser.add_argument(
        "--trials", type=int, default=DEFAULT_TRIALS_PER_COND,
        help=f"Trials per condition [default: {DEFAULT_TRIALS_PER_COND}]"
    )
    parser.add_argument(
        "--duration", type=int, default=DEFAULT_TRIAL_DURATION_S,
        help=f"Trial duration in seconds [default: {DEFAULT_TRIAL_DURATION_S}]"
    )
    parser.add_argument(
        "--iti", type=float, default=DEFAULT_ITI_S,
        help=f"Inter-trial interval in seconds [default: {DEFAULT_ITI_S}]"
    )
    return parser


def _config_from_args(args) -> ExperimentConfig:
    sham_paths = discover_sham_files() if args.all_sham else []
    include_real = not args.no_real
    if not sham_paths and not include_real:
        raise SystemExit("[ERROR] Must include at least one condition.")
    return ExperimentConfig(
        participant=args.participant,
        mock_hardware=not args.real,
        sham_paths=sham_paths,
        include_real=include_real,
        trials_per_cond=args.trials,
        trial_duration_s=args.duration,
        iti_s=args.iti,
        prf_hz=DEFAULT_PRF_HZ,
        duty_cycle=DEFAULT_DUTY_CYCLE,
        vpp_mv=DEFAULT_VPP_MV,
        freq_khz=DEFAULT_FREQ_KHZ,
    )


def main() -> int:
    argv = sys.argv[1:]
    args = build_parser().parse_args(argv)

    # Interactive unless the user passed --participant (scripted mode).
    if args.interactive or args.participant is None:
        cfg = interactive_setup()
    else:
        cfg = _config_from_args(args)
        _print_summary(cfg)

    try:
        run_experiment(cfg)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
