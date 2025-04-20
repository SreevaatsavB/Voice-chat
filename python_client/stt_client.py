import asyncio
import json
import websockets
import pyaudio
import argparse
import time
import traceback

class STTClient:
    def __init__(self, server_url="ws://127.0.0.1:80"):
        self.server_url = server_url
        self.is_running = True
        
        # Audio configuration
        self.CHUNK = 1024
        self.FORMAT = pyaudio.paInt16
        self.CHANNELS = 1
        self.RATE = 16000  
        
        self.p = pyaudio.PyAudio()
        
    async def receive_transcription(self, websocket):
        """Handle incoming transcription messages from the server"""
        print("Starting receive_transcription task...")
        try:
            # Listen for messages
            while self.is_running:
                try:
                    # Use a timeout to periodically check if we're still running
                    message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                                        
                    try:
                        data = json.loads(message)
                        message_type = data.get('type', 'unknown')

                        if message_type == 'realtime':
                            text = data.get('text', '')
                            print(f"\rTranscription: {text}", end='', flush=True)

                        elif message_type == 'fullSentence':
                            text = data.get('text', '')
                            print(f"\nFull sentence: {text}")
                            
                    except json.JSONDecodeError:
                        print(f"Received non-JSON message: {message[:100]}")
                        
                except asyncio.TimeoutError:
                    continue  
                    
        except websockets.exceptions.ConnectionClosed as e:
            print(f"\nConnection to server closed: {e}")
            self.is_running = False

        except Exception as e:
            print(f"\nError in receive_transcription: {e}")
            traceback.print_exc()
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
            
            await asyncio.sleep(0.15)
            
            while self.is_running:
                try:
                    if websocket.closed:
                        print("WebSocket closed, stopping audio stream")
                        break
                        
                    audio_data = stream.read(self.CHUNK, exception_on_overflow=False)
                    
                    metadata = {
                        "sampleRate": self.RATE,
                        "chunkSize": len(audio_data)
                    }
                    metadata_json = json.dumps(metadata)
                    metadata_bytes = metadata_json.encode('utf-8')
                    
                    message = (
                        len(metadata_bytes).to_bytes(4, byteorder='little') +
                        metadata_bytes +
                        audio_data
                    )
                    
                    await websocket.send(message)
                    
                    # Add a small delay to prevent overwhelming the server
                    await asyncio.sleep(0.025)
                    
                except Exception as e:
                    print(f"\nError streaming audio: {e}")
                    traceback.print_exc()
                    break
                    
        except Exception as e:
            print(f"\nError setting up audio stream: {e}")
            traceback.print_exc()
            
        finally:
            if 'stream' in locals():
                stream.stop_stream()
                stream.close()

    async def run(self):
        """Main client loop"""
        try:
            print(f"Attempting to connect to {self.server_url}...")
            
            async with websockets.connect(
                self.server_url,
                ping_interval=None,  # Disable automatic pings
                close_timeout=5      # Wait 5 seconds for close handshake
            ) as websocket:
                print(f"Connected to STT server at {self.server_url}")
                
                audio_task = asyncio.create_task(self.stream_audio(websocket))
                transcription_task = asyncio.create_task(self.receive_transcription(websocket))
                
                print("Both tasks created and running")
                
                # Wait for both tasks to complete
                await asyncio.gather(audio_task, transcription_task)
                
        except websockets.exceptions.ConnectionClosed as e:
            print(f"WebSocket connection closed: {e}")
                
        except (ConnectionRefusedError, OSError) as e:
            print(f"Connection error: {e}")
                
        except Exception as e:
            print(f"Unexpected error: {e}")
            traceback.print_exc()
                
        finally:
            print("Client shutting down...")
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
        traceback.print_exc()

if __name__ == "__main__":
    main()