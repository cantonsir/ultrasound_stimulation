"""
Ultrasound Stimulus Script
==========================
Drives the Siglent SDG33500B AWG and plays auditory masks.

Usage Examples:
---------------
1. Standard Run (5Hz, Real Hardware):
   python scripts/run_stimulus.py --real

2. High-Freq Run (50Hz, Real Hardware):
   python scripts/run_stimulus.py --real --prf 50

3. High Intensity (300mVpp):
   python scripts/run_stimulus.py --real --vpp 300

4. Complex Combined Run (Many parameters at once):
   python scripts/run_stimulus.py --real --prf 5 --vpp 250 --duration 80 --duty 0.1 --no-mask

Logic:
------
The script calculates the burst duration dynamically based on:
    Burst = (1/PRF) * DutyCycle
The default PRF is 5Hz.
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

    # Logging
    log_dir: Path             # directory to write TSV logs

    # Development / CI
    mock_hardware: bool       # True = stub serial calls

class Logger:
    """TSV logger for key timestamps and triggers."""
    _columns = ("time", "event", "details")

    def __init__(self, cfg: Config):
        self.start_time = time.time()
        now = _dt.datetime.now()
        fname = f"log-{now:%Y-%m-%d-%H.%M.%S}.tsv"
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
        logger.log("quit")

###############################################################################
# CLI & entry point
###############################################################################

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Continuous ultrasound pulsing with optional audio mask"
    )
    parser.add_argument(
        "--real", action="store_true",
        help="Connect to AWG hardware (default = mock mode)"
    )
    parser.add_argument(
        "--no-mask", action="store_true",
        help="Do NOT play the audio mask"
    )
    parser.add_argument(
        "--mask", type=Path,
        default=ROOT_DIR / "sham_audio_realistic_with_noise" / "sham_replica_2000Hz.wav",
        help="WAV file for audio mask"
    )
    parser.add_argument(
        "--log-dir", type=Path,
        default=ROOT_DIR / "out",
        help="Directory for TSV logs"
    )
    parser.add_argument("--duration", type=int, default=80,
                        help="Total stimulus duration (s)")
    parser.add_argument("--freq", type=int, default=265,
                        help="Carrier frequency (kHz)")
    parser.add_argument("--vpp", type=int, default=250,
                        help="Driving voltage (mVpp)")
    parser.add_argument("--duty", type=float, default=0.1,
                        help="Duty cycle (0–1)")
    parser.add_argument("--prf", type=int, default=5,
                        help="Pulse repetition frequency (Hz)")
    return parser

def main() -> int:
    args = build_parser().parse_args()

    cfg = Config(
        center_freq_khz=args.freq,
        input_vpp_mv=args.vpp,
        duty_cycle=args.duty,
        prf_hz=args.prf,
        total_exposure_s=args.duration,
        audio_mask_file=args.mask,
        no_mask=args.no_mask,
        log_dir=args.log_dir,
        mock_hardware=not args.real,
    )

    try:
        run_stim(cfg)
    except KeyboardInterrupt:
        print("[INFO] Aborted by user, cleaning up...")
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
