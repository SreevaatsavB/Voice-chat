from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Header
from typing import Optional, Dict, List
import asyncio
import logging
import json
import time
import gc
import torch

from queue import Queue
import threading
import logging
import uvicorn
import wave
import io
import os
import argparse
from pathlib import Path
import subprocess

from RealtimeTTS import (
    TextToAudioStream,
    KokoroEngine,
)

from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, Query, Request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("debug.log"),
        logging.StreamHandler()
    ]
)

active_sessions: Dict[str, object] = {}
GLOBAL_SESSION_ID = None

DEV_MODE = os.environ.get('DEV_MODE', 'true').lower() == 'true'
PORT = int(os.environ.get("TTS_FASTAPI_PORT", 8001))
USE_SSL = os.environ.get('USE_SSL', 'false').lower() == 'true'

SSL_KEYFILE = "private-key.pem"
SSL_CERTFILE = "certificate.pem"

BROWSER_IDENTIFIERS = [
    "mozilla",
    "chrome",
    "safari",
    "firefox",
    "edge",
    "opera",
    "msie",
    "trident",
]

EC2_IP = "3.238.98.245"  # Your EC2 IP
origins = [
    "http://localhost:8001",
    "https://localhost:8001",
    "http://localhost",
    "https://localhost",
    "http://127.0.0.1:8001",
    "https://127.0.0.1:8001",
    f"http://{EC2_IP}:8001",
    f"https://{EC2_IP}:8001",
    f"http://{EC2_IP}",
    f"https://{EC2_IP}"
]

if DEV_MODE:
    # Development origins - allow both HTTP and HTTPS
    origins.extend([
        f"http://{EC2_IP}",
        f"http://{EC2_IP}:{PORT}",
        f"https://{EC2_IP}",
        f"https://{EC2_IP}:{PORT}",
        "http://localhost",
        f"http://localhost:{PORT}",
        "https://localhost",
        f"https://localhost:{PORT}",
    ])
else:
    # Production origins - HTTPS only
    origins.extend([
        f"https://{EC2_IP}",
        f"https://{EC2_IP}:{PORT}",
        "https://localhost",
        f"https://localhost:{PORT}",
    ])

# Single global engine and lock
engine_lock = asyncio.Lock()
tts_engine = None

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_audio_chunk(self, websocket: WebSocket, chunk: bytes):
        try:
            await websocket.send_bytes(chunk)
        except Exception as e:
            print(f"Error sending chunk: {e}")

manager = ConnectionManager()

class TTSRequestHandler:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.audio_queue = asyncio.Queue()
        self.is_active = True
        self.loop = asyncio.get_event_loop()
        self.is_finished = False
        
        # Create stream with stream stop callback
        self.stream = TextToAudioStream(
            tts_engine,
            on_audio_stream_stop=self.on_audio_stream_stop,
            muted=True
        )

        # Set up the player with our playback stop callback
        if hasattr(self.stream, 'player'):
            self.stream.player.on_playback_stop = self.on_playback_stop
            
        self.current_session = session_id
        self.chunks_sent = 0

    async def audio_chunk_generator(self, send_wave_headers):
        chunks_sent = 0
        try:
            if send_wave_headers:
                print(f"[Session {self.session_id}] Creating wave header...")
                header = create_wave_header_for_engine(tts_engine)
                print(f"[Session {self.session_id}] Wave header size: {len(header)} bytes")
                yield header

            # Continue loop until we get an explicit end signal and all chunks are processed
            while self.is_active:
                if self.session_id == GLOBAL_SESSION_ID:
                    try:
                        # Increased timeout to ensure we don't miss any chunks
                        chunk, sess_id = await asyncio.wait_for(self.audio_queue.get(), timeout=5.0)
                        
                        # Only process chunks for current session
                        if sess_id == GLOBAL_SESSION_ID and self.is_active:
                            if chunk is None:
                                print(f"[Session {self.session_id}] End of stream marker received")
                                # Add a longer delay to ensure all audio is processed
                                await asyncio.sleep(1.5)
                                break
                            
                            chunks_sent += 1
                            yield chunk
                            
                    except asyncio.TimeoutError:
                        # Don't break on timeout unless session is no longer active
                        if not self.is_active:
                            print(f"[Session {self.session_id}] Session terminated, stopping generator")
                            break
                        
                        # If the stream is finished and we've timed out waiting for chunks,
                        # it's safe to exit
                        if self.is_finished:
                            print(f"[Session {self.session_id}] Stream is finished and queue is empty")
                            await asyncio.sleep(1.0)  # Final safety delay
                            break
                            
                        continue
                else:
                    # Different session is active, stop processing
                    break

        except Exception as e:
            print(f"[Session {self.session_id}] Error in generator: {str(e)}")
        finally:
            print(f"[Session {self.session_id}] Generator finished. Total chunks sent: {chunks_sent}")

    def on_audio_chunk(self, chunk):
        if chunk is not None and self.is_active:
            
            # Use run_coroutine_threadsafe instead of create_task
            future = asyncio.run_coroutine_threadsafe(
                self.audio_queue.put([chunk, self.session_id]), 
                self.loop
            )
            future.result()  

    def on_audio_stream_stop(self):
        print(f"[Session {self.session_id}] Stream stop signal received")
        if self.is_active:
            # Add a longer delay before signaling end of stream to ensure all audio is processed
            time.sleep(1.0)  
            
            self.is_finished = True
            
            # Use run_coroutine_threadsafe instead of create_task
            future = asyncio.run_coroutine_threadsafe(
                self.audio_queue.put([None, self.session_id]), 
                self.loop
            )
            future.result()  

    def cleanup(self):
        """Clean up handler resources"""
        global active_sessions
        print(f"[Session {self.session_id}] Cleaning up")
        self.is_active = False
        if self.session_id in active_sessions:
            del active_sessions[self.session_id]

    def stop(self):
        """Safely stop the handler without clearing audio buffers"""
        print(f"[Session {self.session_id}] Stopping handler gracefully")
        self.is_active = False
        
        if hasattr(self, 'stream') and self.stream:
            try:
                # Stop text stream first but don't clear buffers
                self.stream.char_iter.stop()
                
                # Stop the stream gently
                self.stream.stop()
            except Exception as e:
                print(f"Error stopping stream: {str(e)}")

    def on_playback_stop(self):
        """Called when audio playback stops"""
        if self.is_active and self.session_id == GLOBAL_SESSION_ID:
            print(f"[Session {self.session_id}] Playback stopped")
            

    async def play_text_to_speech(self, text):
        if self.is_active and self.session_id == GLOBAL_SESSION_ID:
            print(f"[Session {self.session_id}] Starting TTS for: {text[:100]}...")
            
            # Reset finished flag for new stream
            self.is_finished = False
            
            # Acquire engine lock to prevent concurrent access
            async with engine_lock:
                # Stop any ongoing synthesis but don't clear audio buffers
                self.stream.stop()
                
                # Reset text iterator but preserve audio buffers
                self.stream.char_iter.items.clear()
                self.stream._create_iterators()
                
                # Start new synthesis
                self.stream.feed(text)
                
                # Use loop.run_in_executor to run the stream.play_async in a thread pool
                await self.loop.run_in_executor(
                    None, 
                    lambda: self.stream.play_async(on_audio_chunk=self.on_audio_chunk, muted=True)
                )
                
            print(f"[Session {self.session_id}] TTS processing started")

app = FastAPI()

# CORS middleware   
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Simple security headers
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def create_wave_header_for_engine(engine):
    _, _, sample_rate = engine.get_stream_info()

    num_channels = 1
    sample_width = 2
    frame_rate = sample_rate

    wav_header = io.BytesIO()
    with wave.open(wav_header, "wb") as wav_file:
        wav_file.setnchannels(num_channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(frame_rate)

    wav_header.seek(0)
    wave_header_bytes = wav_header.read()
    wav_header.close()

    final_wave_header = io.BytesIO()
    final_wave_header.write(wave_header_bytes)
    final_wave_header.seek(0)

    return final_wave_header.getvalue()

@app.on_event("startup")
async def startup_event():
    global active_sessions, tts_engine
    
    print("Initializing TTS Engine...")
    try:
        tts_engine = KokoroEngine()
        print("TTS Engine initialized successfully")
    except Exception as e:
        print(f"Error initializing TTS engine: {str(e)}")
        raise
    
    asyncio.create_task(cleanup_inactive_sessions())

def is_browser_request(request):
    user_agent = request.headers.get("user-agent", "").lower()
    is_browser = any(browser_id in user_agent for browser_id in BROWSER_IDENTIFIERS)
    return is_browser

@app.get("/tts")
async def tts(
    request: Request, 
    text: str = Query(...),
    session_id: str = Header(..., alias="X-Session-ID")
):
    if not session_id:
        return {"error": "Session ID is required"}

    global GLOBAL_SESSION_ID
    GLOBAL_SESSION_ID = session_id
    print(f"Received TTS request for session {session_id}")
    
    previous_session_ids = list(active_sessions.keys())
    print("Previous session ids:", previous_session_ids)
    for sess_id in previous_session_ids:
        if sess_id != session_id:  
            old_handler = active_sessions[sess_id]
            old_handler.is_active = False
            old_handler.stop()  
    
    if session_id not in active_sessions:
        request_handler = TTSRequestHandler(session_id)
        active_sessions[session_id] = request_handler
    else:
        request_handler = active_sessions[session_id]
        request_handler.is_active = True
        request_handler.is_finished = False
    
    browser_request = is_browser_request(request)
    
    print(f"TTS Request - Text: {text}, Session: {session_id}")
    print(f"Browser Request: {browser_request}")
    
    await request_handler.play_text_to_speech(text)
    
    headers = {
        "Content-Type": "audio/wav",
        "Cache-Control": "no-cache",
        "X-Session-ID": session_id,
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "X-Session-ID, X-Current-Chunk"
    }

    return StreamingResponse(
        request_handler.audio_chunk_generator(browser_request),
        media_type="audio/wav",
        headers=headers
    )

async def cleanup_inactive_sessions():
    while True:
        try:
            global active_sessions
            current_sessions = list(active_sessions.keys())
            for session_id in current_sessions:
                handler = active_sessions[session_id]

                # Only cleanup sessions that have been inactive for a while
                if not handler.is_active and handler.is_finished:
                    print(f"Cleaning up inactive session {session_id}")
                    handler.cleanup()
            await asyncio.sleep(300)  
        except Exception as e:
            print(f"Error in cleanup task: {str(e)}")
            await asyncio.sleep(300)

@app.get("/")
def root_page():
    return {"status": "TTS server is running", "endpoints": ["/tts"]}

# Handle shutdown event, but don't force cleanup
@app.on_event("shutdown")
async def shutdown_event():
    global active_sessions, tts_engine
    print("Server shutting down...")
    
    current_sessions = list(active_sessions.keys())
    for session_id in current_sessions:
        handler = active_sessions[session_id]
        handler.is_active = False
    
    # Allow time for pending operations to complete
    await asyncio.sleep(3)  

    # Shutdown the engine
    if tts_engine:
        try:
            tts_engine.shutdown()
        except Exception as e:
            print(f"Error shutting down TTS engine: {str(e)}")



@app.get("/")
def root_page():
    return {"status": "TTS server is running", "endpoints": ["/tts"]}

def generate_self_signed_cert(cert_path, key_path):
    """Generate a self-signed certificate if it doesn't exist"""
    # Create directory if it doesn't exist
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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Start the TTS server')
    parser.add_argument('--port', type=int, default=PORT, help='Port to run the server on')
    parser.add_argument('--use-ssl', action='store_true', help='Enable SSL for secure connections')
    parser.add_argument('--ssl-cert', type=str, help='Path to SSL certificate file')
    parser.add_argument('--ssl-key', type=str, help='Path to SSL key file')
    parser.add_argument('--dev-mode', action='store_true', default=DEV_MODE, help='Run in development mode')
    
    args = parser.parse_args()
    
    # Update global variables based on arguments
    PORT = args.port
    DEV_MODE = args.dev_mode
    USE_SSL = args.use_ssl
    
    # SSL Configuration
    ssl_config = None
    if USE_SSL:
        current_dir = Path(__file__).parent
        ssl_cert = args.ssl_cert or (current_dir / "certs" / "fullchain.pem")
        ssl_key = args.ssl_key or (current_dir / "certs" / "privkey.pem")
        
        # Check if certificates exist, generate if they don't
        if not ssl_cert.exists() or not ssl_key.exists():
            print("SSL certificates not found. Generating self-signed certificates...")
            if not generate_self_signed_cert(ssl_cert, ssl_key):
                print("Failed to generate SSL certificates. Please check if OpenSSL is installed.")
                exit(1)
        
        ssl_config = {
            "ssl_keyfile": str(ssl_key),
            "ssl_certfile": str(ssl_cert)
        }

    # Update CORS origins based on mode
    if DEV_MODE:
        # Development origins - allow both HTTP and HTTPS
        origins.extend([
            f"http://{EC2_IP}",
            f"http://{EC2_IP}:{PORT}",
            f"https://{EC2_IP}",
            f"https://{EC2_IP}:{PORT}",
            "http://localhost",
            f"http://localhost:{PORT}",
            "https://localhost",
            f"https://localhost:{PORT}",
        ])
    else:
        # Production origins - HTTPS only if SSL is enabled
        if USE_SSL:
            origins.extend([
                f"https://{EC2_IP}",
                f"https://{EC2_IP}:{PORT}",
                "https://localhost",
                f"https://localhost:{PORT}",
            ])
        else:
            origins.extend([
                f"http://{EC2_IP}",
                f"http://{EC2_IP}:{PORT}",
                "http://localhost",
                f"http://localhost:{PORT}",
            ])

    print("Server ready")
    print(f"Running in {'development' if DEV_MODE else 'production'} mode")
    print(f"SSL {'enabled' if USE_SSL else 'disabled'}")
    print(f"Listening on port {PORT}")

    # Start the server with the appropriate configuration
    uvicorn_config = {
        "app": app,
        "host": "0.0.0.0",
        "port": PORT,
    }
    
    if ssl_config:
        uvicorn_config.update(ssl_config)
    
    uvicorn.run(**uvicorn_config)