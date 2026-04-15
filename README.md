# Ultrasound Stimulation Control

A comprehensive Python-based control system for Transcranial Ultrasound Stimulation (TUS) research. This repository provides interfaces for driving the Siglent SDG33500B Arbitrary Waveform Generator (AWG) and generating realistic auditory masking for sham conditions.

## 🚀 Features

- **Interactive runner:** Zero-argument launch walks you through hardware mode, stimulation parameters, audio, and Brainsight — no need to remember flags.
- **Hardware Interface:** Direct control of Siglent SDG33500B via PyVISA.
- **Brainsight Recording:** Parallel TCP capture of Polaris tracking data into the same output folder as the stimulation log, with matched timestamps.
- **Auditory Masking:** Generation of realistic "sham" audio files with square waves and noise to match hardware artifacts.
- **Flexible Protocols:** Sensible defaults with per-parameter overrides (PRF, Duty Cycle, Duration, Voltage, Carrier Frequency).
- **Hardware Mocking:** Simulation mode for validation and testing without physical devices.
- **Biosemi Integration:** Triggering support for EEG synchronization.

## 📋 Prerequisites

- **Python:** 3.8+
- **Hardware (Optional):** Siglent SDG33500B AWG
- **Drivers:** NI-VISA or equivalent for PyVISA communication.

## 🛠 Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/cantonsir/ultrasound_stimulation.git
   cd ultrasound_stimulation
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## 🏗 Project Structure

```text
├── scripts/                   # Experiment and utility execution scripts
│   ├── run_stimulus.py        # Main stimulation runner (interactive + scripted)
│   ├── run_psychophysics.py   # US vs sham-audio psychophysics experiment
│   └── gen_sham_audio.py      # Generator for auditory masking files
├── src/
│   └── utils/                 # Core drivers and utilities
│       ├── sg33500B.py        # Siglent AWG hardware interface
│       ├── SerialTrigger...   # Biosemi triggering logic
│       └── brainsight.py      # Brainsight client + parallel recorder
├── out/                       # (Generated) Session logs and outputs
└── sham_audio.../             # Auditory mask assets
```

## ⚡ Quick Start

### 1. Generate Auditory Masks (Optional)
Standard auditory masks are pre-provided. You only need to run this if you want to regenerate them with custom settings:
```bash
python scripts/gen_sham_audio.py
```

### 2. Run a Stimulation Session — Interactive (recommended)
Launch with no arguments to walk through an interactive setup that asks for hardware mode, preset, audio mask, and Brainsight connection:
```bash
python scripts/run_stimulus.py
```

### 3. Run a Stimulation Session — Scripted
Use explicit flags when you need reproducible, unattended runs. All parameters have sensible defaults (see table below), so you only need to pass the ones you want to override:
```bash
# Run with all defaults on real hardware
python scripts/run_stimulus.py --real

# Custom PRF and duration, everything else default
python scripts/run_stimulus.py --real --prf 10 --duration 120

# Fully explicit parameters
python scripts/run_stimulus.py --real --prf 5 --duty 0.1 --duration 80 --vpp 297 --freq 265
```

### 4. Brainsight Recording
Brainsight Polaris tracking is recorded **in parallel by default** when running a session. Connection defaults to `192.168.1.6:60000`. If Brainsight is unreachable, the script prints a warning and continues the ultrasound session without tracking.

```bash
# Point at a different Brainsight host
python scripts/run_stimulus.py --real --brainsight-host 192.168.1.10

# Disable Brainsight for a quick local test
python scripts/run_stimulus.py --real --no-brainsight
```

Each session produces up to three co-located files in `out/` with a shared timestamp:

```
out/log-YYYY-MM-DD-HH.MM.SS.tsv              # ultrasound events & triggers
out/brainsight_raw_....jsonl                 # every Brainsight packet (raw JSON)
out/brainsight_polaris_....tsv               # Polaris Stream-to-File-compatible TSV
```

## 🧪 Psychophysics Experiment

`scripts/run_psychophysics.py` presents randomized 5-second trials comparing **real ultrasound** against the **static-crackle sham audio** variants produced by `gen_sham_audio.py` (each file sweeps `clicks_per_pulse` and `highpass_cutoff_hz`), and records a 1–5 perceived-intensity rating for each trial. Use it to find which sham audio parameterization is subjectively closest to real US.

```bash
# Interactive setup — pick participant, conditions, trial count, ITI
python scripts/run_psychophysics.py

# Scripted — P01, mock hardware, every sham file as a condition, no real US
python scripts/run_psychophysics.py --participant P01 --all-sham --no-real --trials 5
```

Results are written to `out/psychophysics-<participant>-<timestamp>.tsv` with columns:
`trial`, `condition`, `stimulus_file`, `rating`, `response_time_s`, `timestamp`.

## ⚙️ Configuration Reference

### Command Line Arguments

| Argument | Default | Description |
| :--- | :--- | :--- |
| `--interactive` | `False` | Force the interactive prompt flow even if other flags are given. |
| `--real` | `False` | Enable hardware connection (Siglent SDG33500B). |
| `--prf` | `5` | Pulse Repetition Frequency (Hz). |
| `--duty` | `0.1` | Duty Cycle (0.0 - 1.0). |
| `--vpp` | `297` | Output Voltage (mVpp). |
| `--freq` | `265` | Carrier Frequency (kHz). |
| `--duration` | `80` | Session duration (seconds). |
| `--no-mask` | `False` | Disable auditory masking playback. |
| `--mask` | *(bundled WAV)* | Path to a custom auditory mask WAV file. |
| `--no-brainsight` | `False` | Do NOT record Brainsight tracking in parallel. |
| `--brainsight-host` | `192.168.1.6` | Brainsight network server hostname or IP. |
| `--brainsight-port` | `60000` | Brainsight network server port. |
| `--log-dir` | `out/` | Directory for all session logs. |

## 📄 License

[MIT](LICENSE)

---

**Note:** This software is for research purposes only. Ensure all hardware limits are respected to strictly adhere to safety protocols.
