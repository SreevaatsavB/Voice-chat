import logging
from RealtimeSTT import AudioToTextRecorder
import sys

LOG_FILENAME = "transcription_log.txt"

def realtime_print(text):
  """Minimal real-time update on the console."""
  sys.stdout.write(f"\r{text}   ")
  sys.stdout.flush()

def text_detected(text):
    """Writes the finalized transcription text to the log file."""
    try:
        sys.stdout.write("\r" + " " * 80 + "\r")
        sys.stdout.flush()

        with open(LOG_FILENAME, "a", encoding="utf-8") as f:
            f.write(f"{text}\n")
    except Exception as e:
        print(f"\nError writing to log file: {e}", file=sys.stderr)

if __name__ == '__main__':
    recorder = AudioToTextRecorder(
        model="base.en",              
        language="en",                
        enable_realtime_transcription=True,
        realtime_processing_pause=0.2,
        on_realtime_transcription_update=realtime_print, 
        spinner=False,                
        level=logging.ERROR          
    )

    print(f"Transcribing audio. Final text logged to: {LOG_FILENAME}")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            recorder.text(text_detected)
    except KeyboardInterrupt:
        print("\nStopping transcription...")
    finally:
        print("Shutting down recorder...")
        recorder.shutdown()
        print("Recorder shut down.")
        sys.stdout.write("\r" + " " * 80 + "\r")
        sys.stdout.flush()