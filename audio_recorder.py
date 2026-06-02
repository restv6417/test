#!/usr/bin/env python3
"""
Windows Meeting Audio Recorder
OS-level capture via WASAPI loopback — works with Teams, Zoom, and any app.
Saves as WAV 16 kHz mono — Whisper's native format, ~6x smaller than raw capture.
"""

import sys
import os
import wave
import time
import datetime
import argparse
from pathlib import Path

import numpy as np

try:
    import pyaudiowpatch as pyaudio
except ImportError:
    print("pyaudiowpatch not found. Install with:\n  pip install pyaudiowpatch")
    sys.exit(1)

CHUNK = 512
FORMAT = pyaudio.paInt16
SAMPLE_WIDTH = 2          # int16 = 2 bytes
TARGET_RATE = 16000       # Whisper's native sample rate

DEFAULT_OUTPUT = str(Path.home() / "Desktop" / "recordings")


# ---------------------------------------------------------------------------
# Device helpers
# ---------------------------------------------------------------------------

def find_default_loopback(p):
    """Return (device_info, error_message) for the default WASAPI loopback."""
    try:
        wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError:
        return None, "WASAPI is not available on this system (Windows only)"

    default_out = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])

    if default_out.get("isLoopbackDevice"):
        return default_out, None

    for loopback in p.get_loopback_device_info_generator():
        if default_out["name"] in loopback["name"]:
            return loopback, None

    # Fallback: first available loopback
    for loopback in p.get_loopback_device_info_generator():
        return loopback, None

    return None, "No loopback device found"


def list_devices(p):
    try:
        wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError:
        print("WASAPI not available. This tool requires Windows.")
        return

    default_out_idx = wasapi_info.get("defaultOutputDevice", -1)
    default_out = (p.get_device_info_by_index(default_out_idx)
                   if default_out_idx != -1 else None)

    print("\nLoopback Devices (system audio — use with --device):")
    print("-" * 62)
    found = False
    for lb in p.get_loopback_device_info_generator():
        found = True
        marker = ""
        if default_out and default_out["name"] in lb["name"]:
            marker = "  [default]"
        print(f"  [{lb['index']:2d}] {lb['name']}{marker}")
        print(f"       {int(lb['defaultSampleRate'])} Hz, "
              f"{lb['maxInputChannels']} ch")
    if not found:
        print("  (none found)")

    print("\nMicrophone Devices (use with --mic-device):")
    print("-" * 62)
    try:
        default_in_idx = p.get_default_input_device_info()["index"]
    except Exception:
        default_in_idx = -1

    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0 and not info.get("isLoopbackDevice"):
            marker = "  [default]" if i == default_in_idx else ""
            print(f"  [{i:2d}] {info['name']}{marker}")
    print()


# ---------------------------------------------------------------------------
# Audio processing
# ---------------------------------------------------------------------------

def mix_audio(system_frames, mic_frames, sys_channels, sys_rate, mic_rate):
    """Mix system loopback and microphone, returns float32 numpy array."""
    sys_audio = np.frombuffer(b"".join(system_frames), dtype=np.int16).astype(np.float32)
    mic_audio = np.frombuffer(b"".join(mic_frames), dtype=np.int16).astype(np.float32)

    # Resample mic to system rate if needed
    if mic_rate != sys_rate:
        new_len = int(len(mic_audio) * sys_rate / mic_rate)
        mic_audio = np.interp(
            np.linspace(0, len(mic_audio) - 1, new_len),
            np.arange(len(mic_audio)),
            mic_audio,
        )

    # Expand mono mic to match system channel count
    if sys_channels == 2:
        mic_audio = np.repeat(mic_audio.reshape(-1, 1), 2, axis=1).flatten()

    n = min(len(sys_audio), len(mic_audio))
    mixed = np.clip(sys_audio[:n] + mic_audio[:n], -32768, 32767)
    if len(sys_audio) > n:
        mixed = np.concatenate([mixed, sys_audio[n:]])
    return mixed  # float32


def to_16k_mono(audio_f32, orig_channels, orig_rate):
    """Downsample to 16 kHz mono (Whisper's native input format)."""
    # Stereo → mono
    if orig_channels == 2:
        audio_f32 = audio_f32.reshape(-1, 2).mean(axis=1)

    # Resample to TARGET_RATE via linear interpolation
    if orig_rate != TARGET_RATE:
        new_len = int(len(audio_f32) * TARGET_RATE / orig_rate)
        audio_f32 = np.interp(
            np.linspace(0, len(audio_f32) - 1, new_len),
            np.arange(len(audio_f32)),
            audio_f32,
        )

    return np.clip(audio_f32, -32768, 32767).astype(np.int16)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def record(args):
    os.makedirs(args.output, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    outfile = os.path.join(args.output, f"meeting_{timestamp}.wav")

    with pyaudio.PyAudio() as p:
        # --- Select loopback device ---
        if args.device is not None:
            device_info = p.get_device_info_by_index(args.device)
        else:
            device_info, err = find_default_loopback(p)
            if device_info is None:
                print(f"Error: {err}")
                print("Run with --list to see available devices.")
                sys.exit(1)

        rate = int(device_info["defaultSampleRate"])
        channels = int(device_info["maxInputChannels"])
        use_mic = not args.no_mic

        print(f"\nDevice  : {device_info['name']}")
        print(f"Capture : {rate} Hz {channels}ch  →  saved as {TARGET_RATE} Hz mono")
        print(f"Output  : {outfile}")
        print(f"Mic mix : {'enabled' if use_mic else 'disabled (--no-mic)'}")
        print()
        print("Recording... Press Ctrl+C to stop.\n")

        system_frames = []
        mic_frames = []
        start_time = time.time()
        running = [True]

        # --- Loopback stream callback ---
        def sys_callback(in_data, frame_count, time_info, status):
            if running[0]:
                system_frames.append(in_data)
            elapsed = time.time() - start_time
            m, s = divmod(int(elapsed), 60)
            # Estimate output file size (16kHz mono)
            raw_bytes = len(system_frames) * CHUNK * channels * SAMPLE_WIDTH
            est_mb = raw_bytes * TARGET_RATE / rate / channels / (1024 * 1024)
            print(f"\r  {m:02d}:{s:02d}  ~{est_mb:.1f} MB", end="", flush=True)
            return (in_data, pyaudio.paContinue)

        sys_stream = p.open(
            format=FORMAT,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=device_info["index"],
            frames_per_buffer=CHUNK,
            stream_callback=sys_callback,
        )

        # --- Optional microphone stream ---
        mic_stream = None
        mic_rate = rate

        if use_mic:
            try:
                mic_info = (
                    p.get_device_info_by_index(args.mic_device)
                    if args.mic_device is not None
                    else p.get_default_input_device_info()
                )
                mic_rate = int(mic_info["defaultSampleRate"])

                def mic_callback(in_data, frame_count, time_info, status):
                    if running[0]:
                        mic_frames.append(in_data)
                    return (in_data, pyaudio.paContinue)

                mic_stream = p.open(
                    format=FORMAT,
                    channels=1,
                    rate=mic_rate,
                    input=True,
                    input_device_index=mic_info["index"],
                    frames_per_buffer=CHUNK,
                    stream_callback=mic_callback,
                )
                print(f"  Microphone: {mic_info['name']}")
                mic_stream.start_stream()
            except Exception as e:
                print(f"  Warning: Microphone unavailable ({e})")

        sys_stream.start_stream()

        # --- Wait for Ctrl+C ---
        try:
            while sys_stream.is_active():
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            running[0] = False
            sys_stream.stop_stream()
            sys_stream.close()
            if mic_stream:
                mic_stream.stop_stream()
                mic_stream.close()

        elapsed = time.time() - start_time
        m, s = divmod(int(elapsed), 60)
        print(f"\n\nStopped. Duration: {m:02d}:{s:02d}")

        if not system_frames:
            print("No audio was captured.")
            return

        # --- Build and convert audio ---
        print("Converting to 16 kHz mono...", end="", flush=True)

        if use_mic and mic_frames:
            audio_f32 = mix_audio(system_frames, mic_frames, channels, rate, mic_rate)
        else:
            audio_f32 = np.frombuffer(
                b"".join(system_frames), dtype=np.int16
            ).astype(np.float32)

        audio_out = to_16k_mono(audio_f32, channels, rate)

        # --- Write WAV at 16 kHz mono ---
        with wave.open(outfile, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(TARGET_RATE)
            wf.writeframes(audio_out.tobytes())

        size_mb = os.path.getsize(outfile) / (1024 * 1024)
        print(f" done")
        print(f"Saved : {outfile}  ({size_mb:.1f} MB)")
        print()
        print("Transcription:")
        print(f'  whisper "{outfile}" --language ja --model large-v3')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Record Windows system audio (WASAPI loopback) for meeting transcription",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  python audio_recorder.py                     # Record system audio + mic (default)
  python audio_recorder.py --no-mic            # System audio only (no microphone)
  python audio_recorder.py --list              # Show available devices
  python audio_recorder.py --device 5          # Use specific loopback device
  python audio_recorder.py --output C:\\meetings

Default save location: {DEFAULT_OUTPUT}
        """,
    )
    parser.add_argument("--list", "-l", action="store_true",
                        help="List available audio devices and exit")
    parser.add_argument("--device", "-d", type=int, default=None, metavar="INDEX",
                        help="Loopback device index (see --list; default: system default output)")
    parser.add_argument("--no-mic", action="store_true",
                        help="Disable microphone mixing (mic is on by default)")
    parser.add_argument("--mic-device", type=int, default=None, metavar="INDEX",
                        help="Microphone device index (see --list; default: system default mic)")
    parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT, metavar="DIR",
                        help=f"Output directory (default: Desktop\\recordings)")

    args = parser.parse_args()

    with pyaudio.PyAudio() as p:
        if args.list:
            list_devices(p)
            return

    record(args)


if __name__ == "__main__":
    main()
