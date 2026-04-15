from __future__ import annotations

"""Generate sham audio masks for ultrasound experiments - static electricity crackling style."""

import argparse
from pathlib import Path

import numpy as np
from scipy.io.wavfile import write
from scipy.signal import butter, sosfilt

# Project root detection
ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT_DIR / "sham_audio_static_crackle"


def generate_static_crackle(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    n_files: int = 20,
    fs: int = 44100,
    total_duration: float = 80.0,
    pulse_duration: float = 0.020,  # 20ms to match FUS duty cycle
    total_pulses: int = 400,
    jitter_ms: float = 0,
    # --- Crackle characteristics within each 20ms pulse ---
    clicks_per_pulse: int = 3,  # Number of micro-clicks within each 20ms pulse (1-8)
    click_duration_ms: float = 1.5,  # Duration of each micro-click (0.5-5 ms)
    # --- Frequency characteristics ---
    highpass_cutoff_hz: float = 3500.0,  # High-pass filter for sharpness (2000-8000 Hz)
    # --- Randomness/variation ---
    amplitude_variation: float = 0.6,  # Variation in click amplitude (0-1)
    timing_jitter_within_pulse: float = 0.4,  # Timing randomness within pulse (0-1)
) -> None:
    """
    Generate static electricity crackling sounds for sham TUS audio.

    Maintains the FUS stimulation timing structure (20ms pulses, 5Hz, 10% duty cycle)
    but fills each pulse with static electricity-like crackling instead of tones.

    Key characteristics:
    - 20ms pulse duration (matches FUS stimulation design)
    - Multiple short, sharp clicks within each pulse
    - High-frequency filtered noise (static electricity quality)
    - Random micro-timing and amplitude variation
    - 400 pulses over 80 seconds (matching real stimulation)
    - No background hiss — silent gaps between pulses are truly silent

    Parameter space for exploration:
    - clicks_per_pulse: How many crackle events in each 20ms pulse (1-8)
    - click_duration_ms: Duration of each crackle (0.5-5 ms, shorter = sharper)
    - highpass_cutoff_hz: Higher values = sharper, more "electric" sound
    - amplitude_variation: More variation = more realistic
    - timing_jitter_within_pulse: Irregularity of clicks within pulse
    """

    if n_files <= 0:
        raise ValueError("n_files must be > 0")
    if total_pulses % 5 != 0:
        raise ValueError("total_pulses must be a multiple of 5")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate parameter variations across files
    clicks_per_pulse_range = np.linspace(
        max(1, clicks_per_pulse - 2),
        min(8, clicks_per_pulse + 2),
        n_files
    )
    highpass_range = np.linspace(
        max(2000, highpass_cutoff_hz - 1000),
        min(8000, highpass_cutoff_hz + 1500),
        n_files
    )

    # Pulse timing structure (same as original)
    n_pulse_group = 5
    target_group_duration_ms = 1000
    n_groups = total_pulses // n_pulse_group

    for file_idx in range(n_files):
        samples = []
        curr_clicks_per_pulse = int(clicks_per_pulse_range[file_idx])
        curr_highpass = highpass_range[file_idx]

        # Generate high-pass filter for sharp, high-frequency content
        sos = butter(4, curr_highpass, btype='high', fs=fs, output='sos')

        # Generate crackle-filled pulse
        t_pulse = np.linspace(0, pulse_duration, int(fs * pulse_duration), endpoint=False)
        pulse_samples = len(t_pulse)

        def create_crackle_pulse():
            """Create a single 20ms pulse filled with static crackle."""
            pulse_audio = np.zeros(pulse_samples)

            # Pre-calculate click sample count
            click_samples_count = int(fs * click_duration_ms / 1000.0)

            # Generate positions for clicks
            positions = []
            for click_idx in range(curr_clicks_per_pulse):
                # Base position evenly distributed
                base_position = (click_idx + 0.5) / curr_clicks_per_pulse
                jitter = (np.random.rand() - 0.5) * timing_jitter_within_pulse * 0.3
                position = np.clip(base_position + jitter, 0, 0.95)
                positions.append(position)

            # Distribute clicks across the 20ms pulse
            for position in positions:
                # Generate single click
                raw_click = np.random.randn(click_samples_count)

                # Apply high-pass filter
                filtered_click = sosfilt(sos, raw_click)

                # Softer exponential decay envelope to reduce sharpness
                envelope = np.exp(-np.linspace(0, 6, click_samples_count))
                click = filtered_click * envelope

                # Random amplitude variation (reduced per-click to avoid cumulative spikes)
                amplitude = 0.7 * (1.0 - amplitude_variation + np.random.rand() * amplitude_variation)
                click *= amplitude

                # Insert into pulse
                start_idx = int(position * pulse_samples)
                end_idx = min(start_idx + click_samples_count, pulse_samples)
                actual_length = end_idx - start_idx

                if actual_length > 0:
                    pulse_audio[start_idx:end_idx] += click[:actual_length]

            # Normalize each pulse to prevent explosive sounds
            pulse_max = np.max(np.abs(pulse_audio))
            if pulse_max > 0:
                # Normalize to consistent level (not full scale)
                pulse_audio = pulse_audio / pulse_max * 0.7

            return pulse_audio

        # Generate timing pattern (same as original)
        full_intervals_sec = []
        for _ in range(n_groups):
            jitters = np.random.uniform(-15, 15, n_pulse_group)
            jitters -= np.mean(jitters)
            group = np.round((target_group_duration_ms / n_pulse_group) + jitters)

            current_sum = np.sum(group)
            diff = target_group_duration_ms - current_sum
            group[-1] += diff

            if jitter_ms > 0:
                jitter = np.random.uniform(-jitter_ms, jitter_ms, size=n_pulse_group)
                group += jitter
                group = np.clip(group, pulse_duration * 1000 + 1, None)

            full_intervals_sec.extend(group / 1000.0)

        # Assemble full audio with crackle pulses
        for interval in full_intervals_sec:
            # Create new crackle pulse each time (variation)
            crackle_pulse = create_crackle_pulse()
            samples.append(crackle_pulse)

            # Silence between pulses
            silence_len = int(fs * (interval - pulse_duration))
            silence_len = max(0, silence_len)
            samples.append(np.zeros(silence_len))

        audio = np.concatenate(samples)

        # Trim/pad to exact duration
        expected_len = int(fs * total_duration)
        if len(audio) < expected_len:
            audio = np.concatenate([audio, np.zeros(expected_len - len(audio))])
        else:
            audio = audio[:expected_len]

        # Final normalization
        max_val = np.max(np.abs(audio))
        if max_val > 0:
            audio = audio / max_val * 0.9
        audio = np.clip(audio, -1.0, 1.0)

        # Save to WAV file with descriptive naming
        filename = output_dir / f"sham_crackle_n{curr_clicks_per_pulse}_hp{int(curr_highpass)}Hz_{file_idx:02d}.wav"
        write(filename, fs, np.int16(audio * 32767))
        print(f"Written: {filename.name} (clicks_per_pulse={curr_clicks_per_pulse}, highpass={int(curr_highpass)}Hz)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate static electricity crackling sham audio for TUS experiments.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default generation (moderate crackling within 20ms pulses)
  python gen_sham_audio.py

  # Very sharp, sparse crackling (fewer clicks per pulse, high frequency)
  python gen_sham_audio.py --clicks-per-pulse 2 --click-duration 0.8 --highpass-cutoff 5500

  # Dense crackling (more clicks per pulse)
  python gen_sham_audio.py --clicks-per-pulse 5 --timing-jitter 0.6

  # Ultra-sharp electric spark feel
  python gen_sham_audio.py --click-duration 0.5 --highpass-cutoff 6000 --amplitude-variation 0.8
        """
    )

    # Basic settings
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help="Directory where WAV files will be written"
    )
    parser.add_argument(
        "--n-files", type=int, default=20,
        help="Number of WAV files to generate (default: 20)"
    )
    parser.add_argument(
        "--fs", type=int, default=44100,
        help="Sample rate in Hz (default: 44100)"
    )
    parser.add_argument(
        "--total-duration", type=float, default=80.0,
        help="Total duration per file in seconds (default: 80.0)"
    )

    # Pulse timing (matches FUS stimulation)
    timing_group = parser.add_argument_group("Pulse Timing (FUS stimulation parameters)")
    timing_group.add_argument(
        "--pulse-duration", type=float, default=0.020,
        help="Duration of each pulse in seconds (default: 0.020 = 20ms, matches 10%% duty cycle)"
    )
    timing_group.add_argument(
        "--total-pulses", type=int, default=400,
        help="Total number of pulses (default: 400)"
    )
    timing_group.add_argument(
        "--jitter-ms", type=float, default=0,
        help="Extra per-pulse timing jitter in milliseconds (default: 0)"
    )

    # Crackle characteristics within each pulse
    crackle_group = parser.add_argument_group("Crackle Characteristics (within each 20ms pulse)")
    crackle_group.add_argument(
        "--clicks-per-pulse", type=int, default=3,
        help="Number of micro-clicks within each 20ms pulse (1-8, default: 3)"
    )
    crackle_group.add_argument(
        "--click-duration", type=float, default=1.5, dest="click_duration_ms",
        help="Duration of each micro-click in milliseconds (0.5-5 ms, default: 1.5). Shorter = sharper"
    )

    # Frequency characteristics
    freq_group = parser.add_argument_group("Frequency Characteristics")
    freq_group.add_argument(
        "--highpass-cutoff", type=float, default=3500.0, dest="highpass_cutoff_hz",
        help="High-pass filter cutoff in Hz (2000-8000, default: 3500). Higher = sharper/thinner sound"
    )

    # Randomness/variation
    variation_group = parser.add_argument_group("Variation Parameters")
    variation_group.add_argument(
        "--amplitude-variation", type=float, default=0.6,
        help="Click amplitude variation (0-1, default: 0.6). Higher = more natural variation"
    )
    variation_group.add_argument(
        "--timing-jitter", type=float, default=0.4, dest="timing_jitter_within_pulse",
        help="Timing irregularity within each pulse (0-1, default: 0.4). Higher = less regular pattern"
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()

    print("=" * 70)
    print("Static Electricity Crackling - Sham Audio Generator")
    print("=" * 70)
    print(f"Output directory: {args.output_dir}")
    print(f"Files to generate: {args.n_files}")
    print(f"\nPulse timing (matches FUS):")
    print(f"  Pulse duration: {args.pulse_duration * 1000:.1f} ms")
    print(f"  Total pulses: {args.total_pulses}")
    print(f"  Timing jitter: {args.jitter_ms} ms")
    print(f"\nCrackle parameters (within each pulse):")
    print(f"  Clicks per pulse: {args.clicks_per_pulse}")
    print(f"  Click duration: {args.click_duration_ms} ms")
    print(f"  High-pass cutoff: {args.highpass_cutoff_hz} Hz")
    print(f"  Amplitude variation: {args.amplitude_variation}")
    print(f"  Timing jitter: {args.timing_jitter_within_pulse}")
    print("=" * 70)
    print()

    generate_static_crackle(
        output_dir=args.output_dir,
        n_files=args.n_files,
        fs=args.fs,
        total_duration=args.total_duration,
        pulse_duration=args.pulse_duration,
        total_pulses=args.total_pulses,
        jitter_ms=args.jitter_ms,
        clicks_per_pulse=args.clicks_per_pulse,
        click_duration_ms=args.click_duration_ms,
        highpass_cutoff_hz=args.highpass_cutoff_hz,
        amplitude_variation=args.amplitude_variation,
        timing_jitter_within_pulse=args.timing_jitter_within_pulse,
    )

    print()
    print("=" * 70)
    print("Generation complete!")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
