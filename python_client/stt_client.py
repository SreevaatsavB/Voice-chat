import asyncio
import json
import websockets
import pyaudio
import struct
import argparse

class STTClient:
    def __init__(self, server_url="ws://127.0.0.1:80"):
        self.server_url = server_url
        self.is_running = True
        
        # Audio configuration
        self.CHUNK = 1024
        self.FORMAT = pyaudio.paInt16
        self.CHANNELS = 1
        self.RATE = 44100
        
        # Initialize PyAudio
        self.p = pyaudio.PyAudio()
        
    async def receive_transcription(self, websocket):
        """Handle incoming transcription messages from the server"""
        try:
            async for message in websocket:
                data = json.loads(message)
                if data['type'] == 'realtime':
                    # Print realtime transcription (overwrite line)
                    print(f"\rTranscription: {data['text']}", end='', flush=True)
                elif data['type'] == 'fullSentence':
                    # Print full sentences on new lines
                    print(f"\nFull sentence: {data['text']}")
        except websockets.exceptions.ConnectionClosed:
            print("\nConnection to server closed")
            self.is_running = False
        except Exception as e:
            print(f"\nError receiving transcription: {e}")
            self.is_running = False

    async def stream_audio(self, websocket):
        """Capture and stream audio to the server"""
        try:
            stream = self.p.open(
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.RATE,
                input=True,
                frames_per_buffer=self.CHUNK
            )
            
            print("\nStreaming audio... (Press Ctrl+C to stop)")
            
            while self.is_running:
                try:
                    # Read audio data
                    audio_data = stream.read(self.CHUNK, exception_on_overflow=False)
                    
                    # Prepare metadata
                    metadata = {
                        "sampleRate": self.RATE,
                        "chunkSize": len(audio_data)
                    }
                    metadata_json = json.dumps(metadata)
                    metadata_bytes = metadata_json.encode('utf-8')
                    
                    # Create message with metadata length prefix
                    message = (
                        len(metadata_bytes).to_bytes(4, byteorder='little') +
                        metadata_bytes +
                        audio_data
                    )
                    
                    # Send to server
                    await websocket.send(message)
                    
                except Exception as e:
                    print(f"\nError streaming audio: {e}")
                    break
                    
        except Exception as e:
            print(f"\nError setting up audio stream: {e}")
        finally:
            if 'stream' in locals():
                stream.stop_stream()
                stream.close()

    async def run(self):
        """Main client loop"""
        try:
            print(f"Attempting to connect to {self.server_url}...")
            async with websockets.connect(self.server_url) as websocket:
                print(f"Connected to STT server at {self.server_url}")
                
                # Create tasks for sending audio and receiving transcriptions
                audio_task = asyncio.create_task(self.stream_audio(websocket))
                transcription_task = asyncio.create_task(self.receive_transcription(websocket))
                
                # Wait for either task to complete
                done, pending = await asyncio.wait(
                    [audio_task, transcription_task],
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # Cancel remaining tasks
                for task in pending:
                    task.cancel()
                    
        except Exception as e:
            print(f"Connection error: {e}")
        finally:
            self.is_running = False
            self.p.terminate()

def main():
    parser = argparse.ArgumentParser(description='STT Client')
    parser.add_argument('--host', default='127.0.0.1', help='Server hostname')
    parser.add_argument('--port', type=int, default=80, help='Server port')
    parser.add_argument('--use-ssl', action='store_true', help='Use SSL/WSS connection')
    args = parser.parse_args()
    
    # Construct server URL
    protocol = "wss" if args.use_ssl else "ws"
    server_url = f"{protocol}://{args.host}:{args.port}"
    
    # Create and run client
    client = STTClient(server_url)
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        print("\nStopping client...")
    except Exception as e:
        print(f"Client error: {e}")

if __name__ == "__main__":
    main()
