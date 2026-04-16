"""
Ultrasound Stimulus Script
==========================
Drives the Siglent SDG33500B AWG, plays an auditory mask, and records
Brainsight tracking in parallel — all from a single invocation.

Usage
-----
Interactive (recommended — walks you through every choice):
    python scripts/run_stimulus.py

Scripted (all flags optional, defaults = 5 Hz / 10% / 297 mVpp / 265 kHz / 80 s):
    python scripts/run_stimulus.py --real
    python scripts/run_stimulus.py --real --prf 5 --vpp 297 --duration 80 --duty 0.1
    python scripts/run_stimulus.py --real --no-mask --brainsight-host 192.168.1.6

Burst width is computed from PRF and duty cycle:  burst_ms = (1000/PRF) * duty.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

# Add the src directory to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

try:
    import utils.sg33500B as sg  # Siglent SDG33500B control wrapper
except ModuleNotFoundError as e:
    sg = None
    _import_error = e
except Exception as e:
    sg = None
    _import_error = e

from utils.brainsight import BrainsightRecorder  # noqa: E402

###############################################################################
# Defaults
###############################################################################

# Single canonical stimulation protocol. Individual parameters can be
# overridden interactively or via CLI flags.
DEFAULT_PRF_HZ = 5
DEFAULT_DUTY_CYCLE = 0.1
DEFAULT_VPP_MV = 297
DEFAULT_FREQ_KHZ = 265
DEFAULT_DURATION_S = 80

DEFAULT_MASK_DIR = ROOT_DIR / "sham_audio_static_crackle"
DEFAULT_MASK_NAME = "sham_crackle_n1_hp2500Hz_00.wav"
DEFAULT_LOG_DIR = ROOT_DIR / "out"
DEFAULT_BRAINSIGHT_HOST = "192.168.1.6"
DEFAULT_BRAINSIGHT_PORT = 60000


def _default_mask_file() -> Path:
    """Return the canonical sham mask (``sham_crackle_n1_hp2500Hz_00.wav``).
    Falls back to the first available WAV in the sham-audio folder if the
    canonical file is missing, and to a stub path if the folder is empty
    (config validation will then emit a clear "run gen_sham_audio.py" error)."""
    canonical = DEFAULT_MASK_DIR / DEFAULT_MASK_NAME
    if canonical.exists():
        return canonical
    wavs = sorted(DEFAULT_MASK_DIR.glob("*.wav"))
    return wavs[0] if wavs else DEFAULT_MASK_DIR / "(no sham WAVs generated)"

###############################################################################
# Configuration & logging
###############################################################################

@dataclass
class Config:
    # Ultrasound parameters
    center_freq_khz: int      # carrier frequency in kHz
    input_vpp_mv: int         # driving voltage in mVpp
    duty_cycle: float         # duty cycle (0–1)
    prf_hz: int               # pulse repetition frequency (Hz)

    # Session control
    total_exposure_s: int     # total stimulus duration (s)
    audio_mask_file: Path     # path to WAV file for auditory mask
    no_mask: bool             # True = skip playing audio mask

    # Brainsight parallel recording
    brainsight_enabled: bool
    brainsight_host: str
    brainsight_port: int

    # Logging
    log_dir: Path             # directory to write TSV logs

    # Development / CI
    mock_hardware: bool       # True = stub serial calls


class Logger:
    """TSV logger for key timestamps and triggers."""
    _columns = ("time", "event", "details")

    def __init__(self, cfg: Config, stamp: Optional[str] = None):
        self.start_time = time.time()
        now = _dt.datetime.now()
        self.stamp = stamp or f"{now:%Y-%m-%d-%H.%M.%S}"
        fname = f"log-{self.stamp}.tsv"
        self.path = cfg.log_dir / fname
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(f"VERSION=2; DATE={now}\n")
            f.write("\t".join(self._columns) + "\n")
            f.write(f"# {asdict(cfg)}\n")

    def log(self, event: str, details: str = "") -> None:
        elapsed = time.time() - self.start_time
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"{elapsed:.3f}\t{event}\t{details}\n")

###############################################################################
# Hardware wrappers
###############################################################################

class AudioMask:
    """Looping audio masker using pygame.mixer."""

    def __init__(self, wav_path: Path, logger: Logger):
        try:
            import pygame  # only the mixer sub-module is used
        except ImportError as exc:
            raise RuntimeError(
                "pygame is required for audio masking. Install it with "
                "`pip install pygame` or run with --no-mask."
            ) from exc
        self._pygame = pygame
        self._pygame.mixer.init()
        if not wav_path.exists():
            raise FileNotFoundError(f"Audio mask not found: {wav_path}")
        self._sound = self._pygame.mixer.Sound(str(wav_path))
        self._logger = logger

    def start(self):
        print("[INFO] Audio mask starting...")
        self._sound.play(loops=-1)
        self._logger.log("mask_start")

    def stop(self):
        print("[INFO] Audio mask stopping...")
        self._sound.stop()
        self._pygame.mixer.quit()
        self._logger.log("mask_stop")

class Ultrasound:
    """Siglent SDG33500B control (open/upload/trigger/close)."""

    def __init__(self, cfg: Config, logger: Logger):
        self._cfg = cfg
        self._logger = logger
        self._serial: Optional[object] = None

    def open(self):
        if self._cfg.mock_hardware:
            print("[INFO] AWG mock open.")
            self._logger.log("awg_open_mock")
            return
        if sg is None:
            raise RuntimeError(
                f"Hardware driver 'sg33500B' could not be loaded.\n"
                f"Please ensure you have run 'pip install -r requirements.txt' and that "
                f"the drivers in 'src/utils/' are accessible."
            )
        print("[INFO] Opening AWG serial connection...")
        self._serial = sg.OpenSerial()
        print(f"[INFO] AWG serial opened: {self._serial}")
        self._logger.log("awg_open")

    def upload(self, burst_ms: int):
        if self._cfg.mock_hardware:
            print("[INFO] AWG mock upload parameters.")
            self._logger.log("awg_upload_mock")
            return
        cfg = self._cfg
        print(
            f"[INFO] Uploading US parameters to AWG:\n"
            f"  Carrier: {cfg.center_freq_khz} kHz, Voltage: {cfg.input_vpp_mv} mVpp,\n"
            f"  Burst:   {burst_ms} ms, PRF: {cfg.prf_hz} Hz, Duty: {cfg.duty_cycle*100:.1f}%"
        )
        ok = sg.uploadNewUSparameters(
            centerFreq_kHz=cfg.center_freq_khz,
            mode=1,
            inputmVpp=cfg.input_vpp_mv,
            stimDur_ms=burst_ms,
            PRF_kHz=cfg.prf_hz / 1000.0,
            dutyCycle=cfg.duty_cycle,
        )
        print(f"[INFO] AWG upload returned code {ok}")
        self._logger.log("awg_upload", f"success={ok>=0}")
        if ok < 0:
            raise RuntimeError("AWG parameter upload failed")

    def trigger(self):
        if self._cfg.mock_hardware:
            self._logger.log("ultrasound_trigger_mock")
            return
        t0 = time.time()
        sg.triggerFUS(self._serial)
        latency = (time.time() - t0) * 1e3
        self._logger.log("ultrasound_trigger", f"latency_ms={latency:.1f}")

    def close(self):
        if self._cfg.mock_hardware:
            print("[INFO] AWG mock close.")
            self._logger.log("awg_close_mock")
            return
        
        # Explicitly turn off the output because Internal Triggering keeps running
        print("[INFO] Sending command to turn off AWG output...")
        sg.turnOff()
        self._logger.log("awg_output_off")

        if self._serial is not None:
            print("[INFO] Closing AWG serial connection...")
            self._serial.close()
            self._logger.log("awg_close")

def _validate_config(cfg: Config) -> None:
    errors = []
    if cfg.prf_hz <= 0:
        errors.append("PRF must be > 0 Hz.")
    if not 0 < cfg.duty_cycle <= 1:
        errors.append("Duty cycle must be between 0 and 1.")
    if cfg.total_exposure_s <= 0:
        errors.append("Duration must be > 0 seconds.")
    if cfg.input_vpp_mv <= 0:
        errors.append("Vpp must be > 0 mVpp.")
    if cfg.center_freq_khz <= 0:
        errors.append("Carrier frequency must be > 0 kHz.")
    if (not cfg.no_mask) and (not cfg.audio_mask_file.exists()):
        errors.append(
            f"Audio mask file not found: {cfg.audio_mask_file}. "
            "Run `python scripts/gen_sham_audio.py` or pass --mask/--no-mask."
        )
    if cfg.brainsight_enabled and not (0 < cfg.brainsight_port < 65536):
        errors.append("Brainsight port must be in 1–65535.")
    if errors:
        joined = "\n- ".join(errors)
        raise ValueError(f"Invalid configuration:\n- {joined}")

###############################################################################
# Main protocol — continuous pulsing with fixed-rate bursts
###############################################################################

def run_stim(cfg: Config) -> None:
    _validate_config(cfg)
    logger = Logger(cfg)
    us = Ultrasound(cfg, logger)
    mask: Optional[AudioMask] = None
    if not cfg.no_mask:
        mask = AudioMask(cfg.audio_mask_file, logger)

    recorder: Optional[BrainsightRecorder] = None
    if cfg.brainsight_enabled:
        recorder = BrainsightRecorder(
            host=cfg.brainsight_host,
            port=cfg.brainsight_port,
            log_dir=cfg.log_dir,
            session_stamp=logger.stamp,
            logger=logger,
        )
        print(f"[INFO] Connecting to Brainsight at {cfg.brainsight_host}:{cfg.brainsight_port}...")
        if recorder.start():
            print(f"[INFO] Brainsight connected. Logging to:\n"
                  f"  {recorder.raw_path}\n  {recorder.polaris_path}")
            logger.log("brainsight_connected", f"{cfg.brainsight_host}:{cfg.brainsight_port}")
        else:
            print(
                f"[WARN] Brainsight unreachable at {cfg.brainsight_host}:{cfg.brainsight_port} "
                f"— continuing without tracking."
            )
            logger.log("brainsight_unavailable", f"{cfg.brainsight_host}:{cfg.brainsight_port}")
            recorder = None

    # Compute burst duration from PRF and duty cycle
    period_ms = 1000.0 / cfg.prf_hz
    burst_ms = int(period_ms * cfg.duty_cycle)
    interval_s = period_ms / 1000.0
    if burst_ms <= 0:
        raise ValueError("Duty cycle and PRF result in <1 ms burst. Increase duty or PRF.")

    print(f"[INFO] Starting continuous pulsing protocol ({cfg.total_exposure_s}s)")
    us.open()
    us.upload(burst_ms)
    if mask:
        mask.start()

    start_time = time.time()
    next_trigger = start_time
    end_time = start_time + cfg.total_exposure_s
    count = 0
    status_interval = max(1.0, cfg.total_exposure_s / 10)
    next_status = start_time + status_interval

    try:
        while time.time() < end_time:
            now = time.time()
            if now >= next_trigger:
                us.trigger()
                count += 1
                logger.log("trigger", f"#{count} @ {now - start_time:.3f}s")
                next_trigger += interval_s
            if now >= next_status:
                elapsed = now - start_time
                pct = elapsed / cfg.total_exposure_s * 100
                print(
                    f"[INFO] Progress: {elapsed:.1f}/{cfg.total_exposure_s}s "
                    f"({pct:.0f}%), {count} bursts"
                )
                next_status += status_interval
            time.sleep(0.001)

        print(f"[INFO] Completed {count} bursts in {cfg.total_exposure_s}s")
        logger.log("burst_count", str(count))
        print("[INFO] Protocol complete, exiting.")
    finally:
        if mask:
            mask.stop()
        us.close()
        if recorder is not None:
            print("[INFO] Stopping Brainsight recorder...")
            recorder.stop()
        logger.log("quit")

###############################################################################
# Interactive prompt flow
###############################################################################

def _prompt(text: str, default, caster=str, validator=None):
    """Prompt the user with a default shown in [brackets]. Blank = keep default."""
    shown = f"{text} [{default}]: "
    while True:
        raw = input(shown).strip()
        if raw == "":
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


def _prompt_choice(text: str, options: list[tuple[str, str]], default_idx: int = 0) -> str:
    """Prompt for one of several labeled options. Returns the chosen key."""
    print(text)
    for i, (_, label) in enumerate(options, start=1):
        marker = " (default)" if i - 1 == default_idx else ""
        print(f"  [{i}] {label}{marker}")
    while True:
        raw = input(f"Select 1-{len(options)}: ").strip()
        if raw == "":
            return options[default_idx][0]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print("  ! invalid choice; try again.")


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


def interactive_config() -> Config:
    print("=" * 60)
    print("  Ultrasound Stimulation — Interactive Setup")
    print("=" * 60)

    # 1. Hardware mode
    mode = _prompt_choice(
        "\nHardware mode:",
        [
            ("mock", "Mock (no hardware — safe for dry runs)"),
            ("real", "Real hardware (Siglent AWG via USB-VISA)"),
        ],
        default_idx=0,
    )
    mock_hardware = (mode == "mock")

    # 2. Stimulation parameters — blank to accept default, or enter a new value.
    print(
        f"\nStimulation parameters "
        f"(press Enter to accept default shown in [brackets]):"
    )
    prf_hz = _prompt("  PRF (Hz)", DEFAULT_PRF_HZ, int,
                     lambda v: None if v > 0 else "must be > 0")
    duty_cycle = _prompt("  Duty cycle (0-1)", DEFAULT_DUTY_CYCLE, float,
                         lambda v: None if 0 < v <= 1 else "must be in (0, 1]")
    input_vpp_mv = _prompt("  Voltage (mVpp)", DEFAULT_VPP_MV, int,
                           lambda v: None if v > 0 else "must be > 0")
    center_freq_khz = _prompt("  Carrier frequency (kHz)", DEFAULT_FREQ_KHZ, int,
                              lambda v: None if v > 0 else "must be > 0")
    total_exposure_s = _prompt("  Duration (s)", DEFAULT_DURATION_S, int,
                               lambda v: None if v > 0 else "must be > 0")

    # 3. Audio mask
    print()
    mask_on = _prompt_yes_no("Play auditory mask?", default=True)
    if mask_on:
        mask_path = _prompt("  Mask WAV file", _default_mask_file(), Path)
    else:
        mask_path = _default_mask_file()  # unused when no_mask=True

    # 4. Brainsight
    print()
    bs_on = _prompt_yes_no("Record Brainsight tracking in parallel?", default=True)
    if bs_on:
        bs_host = _prompt("  Brainsight host", DEFAULT_BRAINSIGHT_HOST, str)
        bs_port = _prompt("  Brainsight port", DEFAULT_BRAINSIGHT_PORT, int,
                          lambda v: None if 0 < v < 65536 else "must be 1-65535")
    else:
        bs_host = DEFAULT_BRAINSIGHT_HOST
        bs_port = DEFAULT_BRAINSIGHT_PORT

    cfg = Config(
        center_freq_khz=center_freq_khz,
        input_vpp_mv=input_vpp_mv,
        duty_cycle=duty_cycle,
        prf_hz=prf_hz,
        total_exposure_s=total_exposure_s,
        audio_mask_file=mask_path,
        no_mask=not mask_on,
        brainsight_enabled=bs_on,
        brainsight_host=bs_host,
        brainsight_port=bs_port,
        log_dir=DEFAULT_LOG_DIR,
        mock_hardware=mock_hardware,
    )

    # 5. Summary + confirmation
    print("\n" + "-" * 60)
    print("  Session summary")
    print("-" * 60)
    print(f"  Hardware    : {'MOCK' if mock_hardware else 'REAL (Siglent AWG)'}")
    print(f"  Carrier     : {center_freq_khz} kHz")
    print(f"  Voltage     : {input_vpp_mv} mVpp")
    print(f"  PRF         : {prf_hz} Hz")
    print(f"  Duty cycle  : {duty_cycle*100:.1f}%")
    print(f"  Duration    : {total_exposure_s} s")
    print(f"  Audio mask  : {'OFF' if not mask_on else mask_path}")
    print(f"  Brainsight  : {'OFF' if not bs_on else f'{bs_host}:{bs_port}'}")
    print(f"  Log dir     : {cfg.log_dir}")
    print("-" * 60)

    if not _prompt_yes_no("Proceed with these settings?", default=True):
        print("[INFO] Aborted by user at confirmation.")
        sys.exit(0)

    return cfg

###############################################################################
# CLI & entry point
###############################################################################

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Continuous ultrasound pulsing with optional audio mask + Brainsight recording"
    )
    parser.add_argument(
        "--interactive", action="store_true",
        help="Force interactive prompts even if other flags are given"
    )
    parser.add_argument(
        "--real", action="store_true",
        help="Connect to AWG hardware (default = mock mode)"
    )
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION_S,
                        help=f"Total stimulus duration (s) [default: {DEFAULT_DURATION_S}]")
    parser.add_argument("--freq", type=int, default=DEFAULT_FREQ_KHZ,
                        help=f"Carrier frequency (kHz) [default: {DEFAULT_FREQ_KHZ}]")
    parser.add_argument("--vpp", type=int, default=DEFAULT_VPP_MV,
                        help=f"Driving voltage (mVpp) [default: {DEFAULT_VPP_MV}]")
    parser.add_argument("--duty", type=float, default=DEFAULT_DUTY_CYCLE,
                        help=f"Duty cycle (0-1) [default: {DEFAULT_DUTY_CYCLE}]")
    parser.add_argument("--prf", type=int, default=DEFAULT_PRF_HZ,
                        help=f"Pulse repetition frequency (Hz) [default: {DEFAULT_PRF_HZ}]")
    parser.add_argument(
        "--no-mask", action="store_true",
        help="Do NOT play the audio mask"
    )
    parser.add_argument(
        "--mask", type=Path, default=_default_mask_file(),
        help="WAV file for audio mask"
    )
    parser.add_argument(
        "--no-brainsight", action="store_true",
        help="Do NOT record Brainsight tracking in parallel"
    )
    parser.add_argument(
        "--brainsight-host", default=DEFAULT_BRAINSIGHT_HOST,
        help=f"Brainsight network host [default: {DEFAULT_BRAINSIGHT_HOST}]"
    )
    parser.add_argument(
        "--brainsight-port", type=int, default=DEFAULT_BRAINSIGHT_PORT,
        help=f"Brainsight network port [default: {DEFAULT_BRAINSIGHT_PORT}]"
    )
    parser.add_argument(
        "--log-dir", type=Path, default=DEFAULT_LOG_DIR,
        help="Directory for TSV logs"
    )
    return parser


def _config_from_args(args) -> Config:
    return Config(
        center_freq_khz=args.freq,
        input_vpp_mv=args.vpp,
        duty_cycle=args.duty,
        prf_hz=args.prf,
        total_exposure_s=args.duration,
        audio_mask_file=args.mask,
        no_mask=args.no_mask,
        brainsight_enabled=not args.no_brainsight,
        brainsight_host=args.brainsight_host,
        brainsight_port=args.brainsight_port,
        log_dir=args.log_dir,
        mock_hardware=not args.real,
    )


def main() -> int:
    argv = sys.argv[1:]
    args = build_parser().parse_args(argv)

    if args.interactive or len(argv) == 0:
        cfg = interactive_config()
    else:
        cfg = _config_from_args(args)

    try:
        run_stim(cfg)
    except KeyboardInterrupt:
        print("\n[INFO] Aborted by user, cleaning up...")
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
