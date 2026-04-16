#!/usr/bin/env python3
"""
Play Auditory Mask (Sham) Directly
==================================
A simple utility to play the sham audio file in an infinite loop without
running the full stimulation protocol.

Usage:
    python scripts/play_sham.py
    python scripts/play_sham.py path/to/custom_sham.wav
"""

import argparse
import sys
import time
from pathlib import Path

try:
    import pygame
except ImportError:
    print("[ERROR] pygame is required. Install it with `pip install pygame`")
    sys.exit(1)

def play_audio(wav_path: Path):
    if not wav_path.exists():
        print(f"[ERROR] File not found: {wav_path}")
        sys.exit(1)
        
    pygame.mixer.init()
    sound = pygame.mixer.Sound(str(wav_path))
    
    print(f"Playing: {wav_path.name}")
    print("Press Ctrl+C to stop.")
    
    sound.play(loops=-1)
    
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopping playback.")
    finally:
        sound.stop()
        pygame.mixer.quit()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Play an auditory mask (sham) continuously.")
    
    # Default path based on standard repo structure
    default_sham = Path(__file__).resolve().parent.parent / "sham_audio_static_crackle" / "sham_crackle_n1_hp2500Hz_00.wav"
    
    parser.add_argument(
        "file", 
        type=Path, 
        nargs="?", 
        default=default_sham,
        help="Path to the WAV file to play (defaults to the standard 2500Hz crackle)."
    )
    
    args = parser.parse_args()
    play_audio(args.file)
