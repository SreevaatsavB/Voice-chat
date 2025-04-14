import asyncio
import threading
import json
import ssl
import websockets
from RealtimeSTT import AudioToTextRecorder
import numpy as np
from scipy.signal import resample
import time
import traceback
import argparse



end_of_sentence_detection_pause = 0.45
unknown_sentence_detection_pause = 0.7
mid_sentence_detection_pause = 2.0

class SecureSTTServer:
    def __init__(self, ssl_cert_path=None, ssl_key_path=None, port=80, use_ssl=False):
        self.port = port
        self.ssl_cert = ssl_cert_path
        self.ssl_key = ssl_key_path
        self.use_ssl = use_ssl
        self.recorder = None
        self.recorder_ready = threading.Event()
        self.connected_clients = set()
        self.prev_text = ""
        self.loop = None
        self.client_states = {}
        self.current_session_id = None  # Track current transcription session
        self.pending_messages = []  # Buffer for pending messages
    

    def start_new_session(self):
        """Start a new transcription session"""
        self.current_session_id = str(time.time())
        self.pending_messages = []  # Clear pending messages
        return self.current_session_id


    def create_ssl_context(self):
        # Create SSL context with self-signed certificate
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(self.ssl_cert, self.ssl_key)

        ssl_context.options |= ssl.OP_LEGACY_SERVER_CONNECT
        return ssl_context



    async def send_to_clients(self, message_obj):
        if not self.connected_clients:
            return

        print(f"Attempting to send message to {len(self.connected_clients)} clients: {message_obj}")
        disconnected = set()
        
        for websocket in self.connected_clients:
            if websocket in self.client_states and self.client_states[websocket].get('interrupted', False):
                print(f"Skipping message for interrupted client")
                continue
                
            # Only send if client's session matches current session
            if (websocket in self.client_states and 
                self.client_states[websocket].get('session_id') == self.current_session_id):
                try:
                    await asyncio.wait_for(websocket.send(json.dumps(message_obj)), timeout=2.0)
                    print(f"Successfully sent message to client")

                except asyncio.TimeoutError:
                    print(f"Timeout sending to client, marking for removal")
                    disconnected.add(websocket)

                except websockets.exceptions.ConnectionClosed:
                    print(f"Found closed websocket, marking for removal")
                    disconnected.add(websocket)

                except Exception as e:
                    print(f"Error sending to client: {e}")
                    disconnected.add(websocket)
            else:
                print(f"Skipping message for client with different session")

        # Clean up disconnected clients
        for ws in disconnected:
            self.connected_clients.discard(ws)
            if ws in self.client_states:
                del self.client_states[ws]
                
        print(f"Remaining connected clients: {len(self.connected_clients)}")




    def schedule_coroutine(self, coroutine):
        """Helper function to schedule coroutines from non-async code"""
        asyncio.run_coroutine_threadsafe(coroutine, self.loop)




    def text_detected(self, text):
        if not self.connected_clients:
            return  
            
        text = text.lstrip()
        if text.startswith("..."):
            text = text[3:]
        text = text.lstrip()
        if text:
            text = text[0].upper() + text[1:]

        sentence_end_marks = ['.', '!', '?', '。']
        if text.endswith("..."):
            self.recorder.post_speech_silence_duration = mid_sentence_detection_pause
        elif text and text[-1] in sentence_end_marks and self.prev_text and self.prev_text[-1] in sentence_end_marks:
            self.recorder.post_speech_silence_duration = end_of_sentence_detection_pause
        else:
            self.recorder.post_speech_silence_duration = unknown_sentence_detection_pause

        self.prev_text = text
        
        message = {
            'type': 'realtime',
            'text': text
        }
        self.schedule_coroutine(self.send_to_clients(message))
        print(f"\r{text}", flush=True, end='')



    def process_text(self, full_sentence):
        full_sentence = full_sentence.lstrip()
        if full_sentence.startswith("..."):
            full_sentence = full_sentence[3:]
        full_sentence = full_sentence.lstrip()
        if full_sentence:
            full_sentence = full_sentence[0].upper() + full_sentence[1:]
        
        message = {
            'type': 'fullSentence',
            'text': full_sentence
        }
        self.schedule_coroutine(self.send_to_clients(message))
        print(f"\rSentence: {full_sentence}")


    async def handle_audio_input(self, websocket):
        """Modified audio input handling with voice activity detection"""
        print("\nListening for voice input...")
        self.current_websocket = websocket  
        
        try:
            self.input_stream = self.p.open(
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.RATE,
                input=True,
                frames_per_buffer=self.CHUNK
            )
            
            while self.is_running:
                await self.audio_control.wait()
                
                try:
                    data = self.input_stream.read(self.CHUNK, exception_on_overflow=False)
                    
                    # Check for voice activity
                    if self.vad_enabled and self.detect_voice_activity(data):
                        if self.is_speaking:
                            await self.handle_interruption()
                    
                    if not self.is_speaking:
                        metadata = {
                            "sampleRate": self.RATE,
                            "chunkSize": len(data)
                        }
                        metadata_json = json.dumps(metadata)
                        metadata_bytes = metadata_json.encode('utf-8')
                        
                        message = (
                            len(metadata_bytes).to_bytes(4, byteorder='little') +
                            metadata_bytes +
                            data
                        )
                        
                        await websocket.send(message)
                    
                    await asyncio.sleep(0.02)
                    
                except Exception as e:
                    if not self.is_speaking:
                        print(f"\nError sending audio chunk: {e}")
                        break
                    
        except Exception as e:
            print(f"\nError in audio input handling: {e}")
        finally:
            if self.input_stream:
                self.input_stream.stop_stream()
                self.input_stream.close()



    async def handle_client(self, websocket):
        print(f"New client connected. Current clients before adding: {len(self.connected_clients)}")
        
        # Start new session for first client
        if not self.connected_clients:
            self.start_new_session()
            
        self.connected_clients.add(websocket)
        self.client_states[websocket] = {
            'interrupted': False,
            'active': True,
            'session_id': self.current_session_id
        }
        
        print(f"Added client. Total clients now: {len(self.connected_clients)}")
        
        try:
            async for message in websocket:
                if not self.recorder_ready.is_set():
                    print("Recorder not ready")
                    continue
                    
                try:
                    # Check if this is a control message
                    if isinstance(message, str):
                        try:
                            control_msg = json.loads(message)
                            if control_msg.get('type') == 'interrupt':
                                print("Received interrupt signal from client")
                                self.client_states[websocket]['interrupted'] = True
                                continue
                            elif control_msg.get('type') == 'resume':
                                print("Received resume signal from client")
                                self.client_states[websocket]['interrupted'] = False
                                self.start_new_session()  # Start new session on resume
                                self.client_states[websocket]['session_id'] = self.current_session_id
                                continue
                        except json.JSONDecodeError:
                            pass  
                    
                    # Process audio data if client's session is current
                    if self.client_states[websocket].get('session_id') == self.current_session_id:
                        metadata_length = int.from_bytes(message[:4], byteorder='little')
                        metadata_json = message[4:4+metadata_length].decode('utf-8')
                        metadata = json.loads(metadata_json)
                        sample_rate = metadata['sampleRate']
                       # print("samplerate", sample_rate)
                        chunk = message[4+metadata_length:]
                        
                        if not self.client_states[websocket].get('interrupted', False):
                            #print("testing ,,")
                            resampled_chunk = self.decode_and_resample(chunk, sample_rate, 16000)
                            self.recorder.feed_audio(resampled_chunk)
                        
                except Exception as e:
                    print(f"Error processing message: {str(e)}")
                    print(traceback.format_exc())

        except websockets.exceptions.ConnectionClosed:
            print(f"Client disconnected")
        except Exception as e:
            print(f"Error handling client: {str(e)}")
        finally:
            self.connected_clients.discard(websocket)
            if websocket in self.client_states:
                del self.client_states[websocket]
            # Start new session if all clients disconnected
            if not self.connected_clients:
                self.start_new_session()
            print(f"Client removed. Remaining clients: {len(self.connected_clients)}")




    def decode_and_resample(self, audio_data, original_sample_rate, target_sample_rate):
        #print("sample raters",original_sample_rate,target_sample_rate)
        audio_np = np.frombuffer(audio_data, dtype=np.int16)
      #  print("sample audio_np ",audio_np)
        num_original_samples = len(audio_np)
      #  print("sample num_original_samples ",num_original_samples)
        num_target_samples = int(num_original_samples * target_sample_rate / original_sample_rate)
      #  print ("hi there",num_target_samples,num_original_samples)
        resampled_audio = resample(audio_np, num_target_samples)
        #print ("resampled_audio",resampled_audio)
        return resampled_audio.astype(np.int16).tobytes()

#export LD_LIBRARY=$LD_LIBRARY_PATH:/home/ubuntu/realtime_stt/env_stt/lib/python3.12/site-packages/nvidia/cublas/lib:/home/ubuntu/realtime_stt/env_stt/lib/python3.12/site-packages/nvidia/cudnn/lib
#export  LD_LIBRARY_PATH=/opt/amazon/efa/lib:/opt/amazon/openmpi/lib:/opt/aws-ofi-nccl/lib:/usr/local/cuda/lib:/usr/local/cuda:/usr/local/cuda/lib64:/usr/local/cuda/extras/CUPTI/lib64:/usr/local/cuda/targets/x86_64-linux/lib:/usr/local/lib:/usr/lib:/opt/amazon/efa/lib:/opt/amazon/openmpi/lib:/opt/aws-ofi-nccl/lib:/usr/local/cuda/lib:/usr/local/cuda:/usr/local/cuda/lib64:/usr/local/cuda/extras/CUPTI/lib64:/usr/local/cuda/targets/x86_64-linux/lib:/usr/local/lib:/usr/lib:/opt/amazon/efa/lib:/opt/amazon/openmpi/lib:/opt/aws-ofi-nccl/lib:/usr/local/cuda/lib:/usr/local/cuda:/usr/local/cuda/lib64:/usr/local/cuda/extras/CUPTI/lib64:/usr/local/cuda/targets/x86_64-linux/lib:/usr/local/lib:/usr/lib:/opt/amazon/efa/lib:/opt/amazon/openmpi/lib:/opt/aws-ofi-nccl/lib:/usr/local/cuda/lib:/usr/local/cuda:/usr/local/cuda/lib64:/usr/local/cuda/extras/CUPTI/lib64:/usr/local/cuda/targets/x86_64-linux/lib:/usr/local/lib:/usr/lib:/opt/amazon/efa/lib:/opt/amazon/openmpi/lib:/opt/aws-ofi-nccl/lib:/usr/local/cuda/lib:/usr/local/cuda:/usr/local/cuda/lib64:/usr/local/cuda/extras/CUPTI/lib64:/usr/local/cuda/targets/x86_64-linux/lib:/usr/local/lib:/usr/lib:/opt/amazon/efa/lib:/opt/amazon/openmpi/lib:/opt/aws-ofi-nccl/lib:/usr/local/cuda/lib:/usr/local/cuda:/usr/local/cuda/lib64:/usr/local/cuda/extras/CUPTI/lib64:/usr/local/cuda/targets/x86_64-linux/lib:/usr/local/lib:/usr/lib:/opt/amazon/efa/lib:/opt/amazon/openmpi/lib:/opt/aws-ofi-nccl/lib:/usr/local/cuda/lib:/usr/local/cuda:/usr/local/cuda/lib64:/usr/local/cuda/extras/CUPTI/lib64:/usr/local/cuda/targets/x86_64-linux/lib:/usr/local/lib:/usr/lib

    def _recorder_thread(self):

        # Recorder configuration for the speech to text model (Default ones)
        recorder_config = {
            'spinner': False,
            'use_microphone': False,
            'model': 'medium.en',
            'input_device_index': 1,
            'realtime_model_type': 'medium.en',
            'language': 'en',
            'silero_sensitivity': 0.05,
            'webrtc_sensitivity': 3,
            'post_speech_silence_duration': unknown_sentence_detection_pause,
            'min_length_of_recording': 1.1,
            'min_gap_between_recordings': 0,
            'enable_realtime_transcription': True,
            'realtime_processing_pause': 0.02,
            'on_realtime_transcription_update': self.text_detected,
            'silero_deactivity_detection': True,
            'early_transcription_on_silence': 0.2,
            'beam_size': 5,
            'beam_size_realtime': 3,
            'no_log_file': True,
            'initial_prompt': 'Add periods only for complete sentences. Use ellipsis (...) for unfinished thoughts or unclear endings.'
        }

        print("Creating AudioToTextRecorder with config: %s", recorder_config)

        print("Initializing RealtimeSTT...")
        self.recorder = AudioToTextRecorder(**recorder_config)
        print("RealtimeSTT initialized")
        self.recorder_ready.set()
        
        while True:
            self.recorder.text(self.process_text)



    async def start(self):
        self.loop = asyncio.get_running_loop()
        
        # Start recorder thread
        recorder_thread = threading.Thread(target=self._recorder_thread, daemon=True)
        recorder_thread.start()
        
        while not self.recorder_ready.is_set():
            await asyncio.sleep(0.1)
        
        # Create SSL context if using SSL
        ssl_context = None
        if self.use_ssl:
            if not self.ssl_cert or not self.ssl_key:
                raise ValueError("SSL certificate and key paths must be provided when use_ssl is True")
            ssl_context = self.create_ssl_context()
        
        print("Server started. Press Ctrl+C to stop the server.")
        protocol = "wss" if self.use_ssl else "ws"
        async with websockets.serve(
            self.handle_client,
            "0.0.0.0",
            self.port,
            ping_interval=None,
            ssl=ssl_context
        ):
            print(f"WebSocket server is running on {protocol}://0.0.0.0:{self.port}")
            await asyncio.Future()  


def generate_self_signed_cert(cert_path, key_path):
    """Generate a self-signed certificate if it doesn't exist"""
    from pathlib import Path
    import subprocess
    
    cert_dir = Path(cert_path).parent
    cert_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Generate private key
        subprocess.run([
            'openssl', 'genrsa',
            '-out', key_path,
            '2048'
        ], check=True)
        
        # Generate CSR and self-signed certificate
        subprocess.run([
            'openssl', 'req', '-x509',
            '-new',
            '-key', key_path,
            '-out', cert_path,
            '-days', '365',
            '-subj', '/CN=localhost'
        ], check=True)
        
        print(f"Generated self-signed certificate at {cert_path}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error generating certificate: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error generating certificate: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='Start the STT server')
    parser.add_argument('--port', type=int, default=80, help='Port to run the server on')
    parser.add_argument('--use-ssl', action='store_true', help='Enable SSL for secure connections')
    parser.add_argument('--ssl-cert', type=str, help='Path to SSL certificate file')
    parser.add_argument('--ssl-key', type=str, help='Path to SSL key file')
    
    args = parser.parse_args()
    
    # If SSL is enabled, check for certificate files
    if args.use_ssl:
        from pathlib import Path
        current_dir = Path(__file__).parent
        ssl_cert = args.ssl_cert or (current_dir / "certs" / "fullchain.pem")
        ssl_key = args.ssl_key or (current_dir / "certs" / "privkey.pem")
        
        # Check if certificates exist, generate if they don't
        if not ssl_cert.exists() or not ssl_key.exists():
            print("SSL certificates not found. Generating self-signed certificates...")
            if not generate_self_signed_cert(ssl_cert, ssl_key):
                print("Failed to generate SSL certificates. Please check if OpenSSL is installed.")
                return
    else:
        ssl_cert = None
        ssl_key = None
    
    server = SecureSTTServer(
        ssl_cert_path=str(ssl_cert) if ssl_cert else None,
        ssl_key_path=str(ssl_key) if ssl_key else None,
        port=args.port,
        use_ssl=args.use_ssl
    )
    
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        print("\nShutting down server...")
    except Exception as e:
        print(f"Server error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()