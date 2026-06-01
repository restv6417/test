#!/usr/bin/env python3
"""
Windows Meeting Audio Recorder
OS-level capture via WASAPI loopback — works with Teams, Zoom, and any app.
Saves as WAV (PCM 16-bit) optimised for speech-to-text tools like Whisper.
"""

import sys
import os
import wave
import time
import datetime
import argparse

import numpy as np

try:
    import pyaudiowpatch as pyaudio
except ImportError:
    print("pyaudiowpatch not found. Install with:\n  pip install pyaudiowpatch")
    sys.exit(1)

CHUNK = 512
FORMAT = pyaudio.paInt16
SAMPLE_WIDTH = 2  # int16 = 2 bytes


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

    # Find the loopback mirror of the default output device
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
# Audio mixing
# ---------------------------------------------------------------------------

def mix_audio(system_frames, mic_frames, sys_channels, sys_rate, mic_rate):
    """Mix system loopback and microphone streams into a single buffer."""
    sys_audio = np.frombuffer(b"".join(system_frames), dtype=np.int16).astype(np.float32)
    mic_audio = np.frombuffer(b"".join(mic_frames), dtype=np.int16).astype(np.float32)

    # Resample mic to system rate if needed (nearest-neighbour is fine for speech)
    if mic_rate != sys_rate:
        new_len = int(len(mic_audio) * sys_rate / mic_rate)
        indices = np.clip(
            np.round(np.linspace(0, len(mic_audio) - 1, new_len)).astype(int),
            0, len(mic_audio) - 1,
        )
        mic_audio = mic_audio[indices]

    # Expand mono mic to stereo if the system stream is stereo
    if sys_channels == 2:
        mic_audio = np.repeat(mic_audio.reshape(-1, 1), 2, axis=1).flatten()

    n = min(len(sys_audio), len(mic_audio))
    mixed = np.clip(sys_audio[:n] + mic_audio[:n], -32768, 32767).astype(np.int16)
    # Append any remaining system audio after mic ends
    if len(sys_audio) > n:
        mixed = np.concatenate([mixed, sys_audio[n:].astype(np.int16)])
    return mixed.tobytes()


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

        print(f"\nDevice  : {device_info['name']}")
        print(f"Rate    : {rate} Hz")
        print(f"Channels: {channels} ({'stereo' if channels >= 2 else 'mono'})")
        print(f"Output  : {outfile}")
        if args.mic:
            print("Mic mix : enabled")
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
            size_mb = (len(system_frames) * CHUNK * channels * SAMPLE_WIDTH) / (1024 * 1024)
            print(f"\r  {m:02d}:{s:02d}  {size_mb:5.1f} MB", end="", flush=True)
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

        if args.mic:
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

        # --- Build final audio data ---
        if args.mic and mic_frames:
            audio_data = mix_audio(system_frames, mic_frames, channels, rate, mic_rate)
        else:
            audio_data = b"".join(system_frames)

        # --- Write WAV ---
        with wave.open(outfile, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(rate)
            wf.writeframes(audio_data)

        size_mb = os.path.getsize(outfile) / (1024 * 1024)
        print(f"Saved : {outfile}  ({size_mb:.1f} MB)")
        print()
        print("Transcription examples:")
        print(f'  whisper "{outfile}" --language ja --model large-v3')
        print(f'  whisper "{outfile}" --language ja --model medium --task transcribe')
        print()
        print("Format: WAV PCM 16-bit  →  natively supported by Whisper, Azure Speech,")
        print("        Google Speech-to-Text, Amazon Transcribe, AssemblyAI, etc.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Record Windows system audio (WASAPI loopback) for meeting transcription",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python audio_recorder.py                     # Record system audio (default device)
  python audio_recorder.py --mic               # System audio + microphone mixed
  python audio_recorder.py --list              # Show available devices
  python audio_recorder.py --device 5          # Use specific loopback device
  python audio_recorder.py --output meetings   # Save to custom folder
        """,
    )
    parser.add_argument("--list", "-l", action="store_true",
                        help="List available audio devices and exit")
    parser.add_argument("--device", "-d", type=int, default=None, metavar="INDEX",
                        help="Loopback device index (see --list; default: system default output)")
    parser.add_argument("--mic", "-m", action="store_true",
                        help="Mix microphone input into the recording")
    parser.add_argument("--mic-device", type=int, default=None, metavar="INDEX",
                        help="Microphone device index (see --list; default: system default mic)")
    parser.add_argument("--output", "-o", default="recordings", metavar="DIR",
                        help="Output directory for WAV files (default: recordings/)")

    args = parser.parse_args()

    with pyaudio.PyAudio() as p:
        if args.list:
            list_devices(p)
            return

    record(args)


if __name__ == "__main__":
    main()
