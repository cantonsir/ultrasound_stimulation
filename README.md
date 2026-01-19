# Ultrasound Stimulation Control

A comprehensive Python-based control system for Transcranial Ultrasound Stimulation (TUS) research. This repository provides interfaces for driving the Siglent SDG33500B Arbitrary Waveform Generator (AWG) and generating realistic auditory masking for sham conditions.

## 🚀 Features

- **Hardware Interface:** Direct control of Siglent SDG33500B via PyVISA.
- **Auditory Masking:** Generation of realistic "sham" audio files with square waves and noise to match hardware artifacts.
- **Flexible Protocols:** Customizable stimulation parameters including PRF, Duty Cycle, Duration, and Intensity.
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
├── scripts/                  # Experiment and utility execution scripts
│   ├── run_stimulus.py       # Main entry point for stimulation experiments
│   └── gen_sham_audio.py     # Generator for auditory masking files
├── src/
│   └── utils/                # Core drivers and utilities
│       ├── sg33500B.py       # Siglent AWG hardware interface
│       └── SerialTrigger...  # Biosemi triggering logic
├── out/                      # (Generated) Session logs and outputs
└── sham_audio.../            # Auditory mask assets
```

## ⚡ Quick Start

### 1. Generate Auditory Masks (Optional)
Standard auditory masks are pre-provided. You only need to run this if you want to regenerate them with custom settings:
```bash
python scripts/gen_sham_audio.py
```

### 2. Run a Stimulation Session
Execute the main script. By default, it runs in **Mock Mode** (no hardware required).
```bash
python scripts/run_stimulus.py --duration 10
```

### 3. Hardware Execution
To continuously stimulate at 5Hz with 10% duty cycle for 80 seconds (Standard Protocol):
```bash
python scripts/run_stimulus.py --real --prf 5 --duty 0.1 --duration 80
```

## ⚙️ Configuration Examples

The `run_stimulus.py` script supports extensive CLI arguments.

**High-Frequency Stimulation (50Hz PRF):**
```bash
python scripts/run_stimulus.py --real --prf 50
```

**Custom Intensity (300 mVpp):**
```bash
python scripts/run_stimulus.py --real --vpp 300
```

**Ultrasound Only (Silent/No Mask):**
```bash
python scripts/run_stimulus.py --real --no-mask
```

### Command Line Arguments

| Argument | Default | Description |
| :--- | :--- | :--- |
| `--real` | `False` | Enable hardware connection (Siglent SDG33500B). |
| `--prf` | `5` | Pulse Repetition Frequency (Hz). |
| `--duty` | `0.1` | Duty Cycle (0.0 - 1.0). |
| `--vpp` | `250` | Output Voltage (mVpp). |
| `--freq` | `265` | Carrier Frequency (kHz). |
| `--duration`| `80` | Session duration (seconds). |
| `--no-mask` | `False` | Disable auditory masking playback. |

## 📄 License

[MIT](LICENSE)

---

**Note:** This software is for research purposes only. Ensure all hardware limits are respected to strictly adhere to safety protocols.
