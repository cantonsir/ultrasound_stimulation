from __future__ import annotations

"""Generate sham audio masks for ultrasound experiments."""

import argparse
from pathlib import Path

import numpy as np
from scipy.io.wavfile import write
from scipy.signal import square, windows

# Project root detection
ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT_DIR / "sham_audio_realistic_with_noise"

def generate_sham_audio_with_noise(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    n_files: int = 20,
    freq_range: tuple[int, int] = (1000, 2000),  # Hz
    pulse_duration: float = 0.020,  # seconds
    total_pulses: int = 400,
    fs: int = 44100,
    total_duration: float = 80.0,
    jitter_ms: float = 0,
    snr_ratio: float = 50,  # 50:1 signal to noise
) -> None:
    """
    Generate realistic sham audio simulating auditory Transcranial Ultrasound (TUS).
    
    This version uses Square Wave pulses (richer in harmonics than sine waves) 
    and adds a layer of white noise to better mimic device circuit sound.
    
    Key Features:
    - Square Wave: Mimics electromagnetic/mechanical 'buzz' better than pure tones.
    - SNR 50:1: Adds subtle background hiss for realism.
    - Hann Window: Smoothes start/stop of each pulse to avoid digital clipping.
    """

    if n_files <= 0:
        raise ValueError("n_files must be > 0")
    if freq_range[0] >= freq_range[1]:
        raise ValueError("freq_range must be (min, max) with min < max")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    freqs = np.linspace(freq_range[0], freq_range[1], n_files)

    # Dynamic generation settings
    n_pulse_group = 5
    target_group_duration_ms = 1000
    if total_pulses % n_pulse_group != 0:
        raise ValueError("total_pulses must be a multiple of 5")
    n_groups = total_pulses // n_pulse_group

    for i, freq in enumerate(freqs):
        samples = []

        # Generate pulse shape: Square wave carrier with a Hann window envelope
        t_pulse = np.linspace(0, pulse_duration, int(fs * pulse_duration), endpoint=False)
        tone = square(2 * np.pi * freq * t_pulse)
        window = windows.hann(len(t_pulse))
        pulse = 0.9 * tone * window

        # Construction of the shuffled timing pattern
        full_intervals_sec = []
        for _ in range(n_groups):
            # Generate 5 pulses that sum exactly to 1000ms with small random variations
            # range roughly +/- 20ms to avoid jagged feel but keep variety
            jitters = np.random.uniform(-15, 15, n_pulse_group)
            jitters -= np.mean(jitters)  # Center to sum to 0
            group = np.round((target_group_duration_ms / n_pulse_group) + jitters)
            
            # Fix any small integer rounding error to ensure exact sum
            current_sum = np.sum(group)
            diff = target_group_duration_ms - current_sum
            group[-1] += diff
            
            if jitter_ms > 0:
                jitter = np.random.uniform(-jitter_ms, jitter_ms, size=n_pulse_group)
                group += jitter
                group = np.clip(group, pulse_duration * 1000 + 1, None)
            full_intervals_sec.extend(group / 1000.0)

        # Assemble the full audio waveform sequence
        for interval in full_intervals_sec:
            silence_len = int(fs * (interval - pulse_duration))
            silence_len = max(0, silence_len)
            samples.append(pulse)
            samples.append(np.zeros(silence_len))

        audio = np.concatenate(samples)

        # Normalize signal energy and add background white noise based on target SNR
        rms_signal = np.sqrt(np.mean(audio**2))
        noise = np.random.randn(len(audio))
        rms_noise = np.sqrt(np.mean(noise**2))
        target_noise_rms = rms_signal / snr_ratio
        noise *= target_noise_rms / rms_noise

        noisy_audio = audio + noise

        # Trim/pad to ensure exact total duration
        expected_len = int(fs * total_duration)
        if len(noisy_audio) < expected_len:
            noisy_audio = np.concatenate([noisy_audio, np.zeros(expected_len - len(noisy_audio))])
        else:
            noisy_audio = noisy_audio[:expected_len]

        # Save to WAV file
        filename = output_dir / f"sham_replica_{int(freq)}Hz.wav"
        write(filename, fs, np.int16(noisy_audio * 32767))
        print(f"Written: {filename}")

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate sham audio WAV files with square-wave pulses and noise."
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help="Directory where WAV files will be written"
    )
    parser.add_argument("--n-files", type=int, default=20,
                        help="Number of WAV files to generate")
    parser.add_argument("--freq-min", type=int, default=1000,
                        help="Minimum carrier frequency (Hz)")
    parser.add_argument("--freq-max", type=int, default=2000,
                        help="Maximum carrier frequency (Hz)")
    parser.add_argument("--pulse-duration", type=float, default=0.020,
                        help="Pulse duration (s)")
    parser.add_argument("--total-pulses", type=int, default=400,
                        help="Total pulses per file")
    parser.add_argument("--fs", type=int, default=44100,
                        help="Sample rate (Hz)")
    parser.add_argument("--total-duration", type=float, default=80.0,
                        help="Total duration per file (s)")
    parser.add_argument("--jitter-ms", type=float, default=0,
                        help="Extra per-pulse jitter (ms)")
    parser.add_argument("--snr-ratio", type=float, default=50,
                        help="Signal-to-noise ratio")
    return parser

def main() -> int:
    args = build_parser().parse_args()
    generate_sham_audio_with_noise(
        output_dir=args.output_dir,
        n_files=args.n_files,
        freq_range=(args.freq_min, args.freq_max),
        pulse_duration=args.pulse_duration,
        total_pulses=args.total_pulses,
        fs=args.fs,
        total_duration=args.total_duration,
        jitter_ms=args.jitter_ms,
        snr_ratio=args.snr_ratio,
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
