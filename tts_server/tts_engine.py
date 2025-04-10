from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Header
from typing import Optional, Dict
import asyncio
import logging
import json
from concurrent.futures import ThreadPoolExecutor
import time
import concurrent.futures
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
    AzureEngine,
    ElevenlabsEngine,
    SystemEngine,
    CoquiEngine,
    OpenAIEngine,
)

from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, Query, Request
from fastapi.staticfiles import StaticFiles





logging.basicConfig(
            
    level=logging.INFO,
    
    format="%(asctime)s [%(levelname)s] %(message)s",

    handlers=[
        logging.FileHandler("debug.log"),
        logging.StreamHandler()
    ]
)


active_sessions: Dict[str, asyncio.Event] = {}
GLOBAL_SESSION_ID = None




DEV_MODE = os.environ.get('DEV_MODE', 'true').lower() == 'true'
PORT = int(os.environ.get("TTS_FASTAPI_PORT", 8000))
USE_SSL = os.environ.get('USE_SSL', 'false').lower() == 'true'



SSL_KEYFILE = "private-key.pem"  # Update this path
SSL_CERTFILE = "certificate.pem"  # Update this path


SUPPORTED_ENGINES = [
    "coqui",  
]

# change start engine by moving engine name
# to the first position in SUPPORTED_ENGINES
START_ENGINE = SUPPORTED_ENGINES[0]

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
    "http://localhost:8000",
    "https://localhost:8000",
    "http://localhost",
    "https://localhost",
    "http://127.0.0.1:8000",
    "https://127.0.0.1:8000",
    "http://3.238.98.245:8000",
    "https://3.238.98.245:8000",
    "http://3.238.98.245",
    "https://3.238.98.245"
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




from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Header
from typing import Optional, Dict
import asyncio
import logging
import json
# logging.basicConfig(level=logging.DEBUG)

logging.basicConfig(
            
            level=logging.ERROR,
            
            format="%(asctime)s [%(levelname)s] %(message)s",

            handlers=[
                logging.FileHandler("debug.log"),
                logging.StreamHandler()
            ]
        )




active_sessions: Dict[str, asyncio.Event] = {}
GLOBAL_SESSION_ID = None




DEV_MODE = os.environ.get('DEV_MODE', 'true').lower() == 'true'
PORT = int(os.environ.get("TTS_FASTAPI_PORT", 8000))



SSL_KEYFILE = "private-key.pem"  # Update this path
SSL_CERTFILE = "certificate.pem"  # Update this path


SUPPORTED_ENGINES = [
    "coqui",  
]

# change start engine by moving engine name
# to the first position in SUPPORTED_ENGINES
START_ENGINE = SUPPORTED_ENGINES[0]

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


# EC2_IP = "34.204.200.164"

# origins = [
#     f"https://{EC2_IP}",
#     f"https://{EC2_IP}:{PORT}",
#     "https://localhost",
#     f"https://localhost:{PORT}",
#     "https://127.0.0.1",
#     f"https://127.0.0.1:{PORT}",
# ]



EC2_IP = "3.238.98.245"  # Your EC2 IP
origins = [
    "http://localhost:8000",
    "https://localhost:8000",
    "http://localhost",
    "https://localhost",
    "http://127.0.0.1:8000",
    "https://127.0.0.1:8000",
    "http://3.238.98.245:8000",
    "https://3.238.98.245:8000",
    "http://3.238.98.245",
    "https://3.238.98.245"
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



play_text_to_speech_semaphore = threading.Semaphore(1)
engines = {}
voices = {}
current_engine = None
speaking_lock = threading.Lock()
tts_lock = threading.Lock()
gen_lock = threading.Lock()



class EngineManager:
    def __init__(self, num_engines=3):
        self.engines = {}
        self.voices = {}
        self.engine_status = {}  # False = available, True = busy
        self.engine_sessions = {}  # Maps engine_id to current session_id
        self.current_engine_idx = 0  # For round-robin assignment
        self.num_engines = num_engines
        self.replacement_lock = threading.Lock()
        self.replacement_thread = None
        

        with ThreadPoolExecutor(max_workers=num_engines) as executor:
            # Create a dictionary of future objects for each engine
            future_to_engine = {
                executor.submit(self._initialize_engine, i): f"coqui_{i}"
                for i in range(num_engines)
            }
            
            # Process completed initializations
            for future in concurrent.futures.as_completed(future_to_engine):
                engine_id = future_to_engine[future]
                try:
                    engine, voices = future.result()
                    self.engines[engine_id] = engine
                    self.voices[engine_id] = voices
                    self.engine_status[engine_id] = False
                    self.engine_sessions[engine_id] = None
                    print(f"Initialized {engine_id}")
                except Exception as e:
                    print(f"Error initializing {engine_id}: {str(e)}")

    def _initialize_engine(self, idx):
        """Helper method to initialize a single engine"""
        engine = CoquiEngine(voices_path='/home/ubuntu/realtime_tts/RealtimeTTS/tests/coqui_voices/',voice='coqui_Alexandra Hisakawa.wav')
        voices = engine.get_voices()
        return engine, voices

    def _replace_engine_background(self, engine_id):
        """Background process to replace an engine"""
        try:

            print("trying to kill the engine ", engine_id)

            s1 = time.time()


            old_engine = self.engines[engine_id]

            try:
                old_engine.shutdown()

            except Exception as e:
                print(f"ERROR while shutting down the engine {engine_id}", str(e))


            print("Engine shutdown complete ", engine_id)
            
            del old_engine

            torch.cuda.empty_cache()
            gc.collect()

            s2 = time.time()

            
            print(f"Starting background replacement of {engine_id}, time = {(s2-s1)/1000}")

            s1 = time.time()
            new_engine = CoquiEngine(voices_path='/home/ubuntu/realtime_tts/RealtimeTTS/tests/coqui_voices/',voice='coqui_Alexandra Hisakawa.wav')
            voices = new_engine.get_voices()
            s2 = time.time()

            print(f"Time taken for loading = {(s2-s1)/1000}")

            # with self.replacement_lock:
            self.engines[engine_id] = new_engine
            self.voices[engine_id] = voices
            self.engine_status[engine_id] = False
            self.engine_sessions[engine_id] = None
                
            print(f"Successfully replaced {engine_id}")


        except Exception as e:
            print(f"Error replacing {engine_id}: {str(e)}")


    def get_next_available_engine(self):
        """Gets next available engine using round-robin with strict cleanup"""
        total_engines = len(self.engines)
        
        
        self.current_engine_idx = (self.current_engine_idx + 1)%total_engines
        # 1

        engine_id = f"coqui_{self.current_engine_idx}"

        prev_idx = (self.current_engine_idx-1) % total_engines
        # 0

        prev_engine_id = f"coqui_{prev_idx}"

        # Start replacement in background thread
        self.replacement_thread = threading.Thread(
            target=self._replace_engine_background,
            args=(prev_engine_id,)
        )
        # self.replacement_thread.daemon = True

        self.replacement_thread.start()

        return engine_id

    

    # async def _cleanup_engine(self, engine_id):

    #     self.engines[engine_id] = CoquiEngine()
    #     voices = self.engines[engine_id].voices()

        


    def assign_engine(self, session_id):
        """Assigns next available engine to session with cleanup"""
        engine_id = self.get_next_available_engine()
        if engine_id:
            print(f"Assigning {engine_id} to session {session_id}")
            # self._cleanup_engine(engine_id)  # Extra cleanup before assignment
            self.engine_status[engine_id] = True
            self.engine_sessions[engine_id] = session_id
            return self.engines[engine_id], engine_id
        return None, None

    def release_engine(self, session_id):
        """Release all engines associated with a session"""
        released = []
        for engine_id, curr_session in list(self.engine_sessions.items()):
            if curr_session == session_id:
                print(f"Releasing {engine_id} from session {session_id}")
                # self._cleanup_engine(engine_id)
                released.append(engine_id)
        return released


    def cleanup_all_engines(self):
        """Clean up all engines during shutdown"""
        for engine_id in list(self.engines.keys()):
            try:
                self.engine_status[engine_id] = False
                self.engine_sessions[engine_id] = None
                del self.engines[engine_id]
                del self.voices[engine_id]
            except Exception as e:
                print(f"Error cleaning up engine {engine_id}: {str(e)}")






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
    def __init__(self, engine, engine_id, session_id: str):
        self.engine = engine
        self.engine_id = engine_id
        self.session_id = session_id
        self.audio_queue = asyncio.Queue()
        self.is_active = True
        self.loop = asyncio.get_event_loop()
        
        # Create stream with stream stop callback
        self.stream = TextToAudioStream(
            engine,
            on_audio_stream_stop=self.on_audio_stream_stop,
            muted=True
        )

        # Set up the player with our playback stop callback
        if hasattr(self.stream, 'player'):
            self.stream.player.on_playback_stop = self.on_playback_stop
            
        self.current_session = session_id
        self.chunks_sent = 0


    

    def clear_all_buffers(self):
        """Clear all audio buffers in the system"""
        print(f"[Session {self.session_id}] Clearing all audio buffers for engine {self.engine_id}")
        
        # Clear async queue
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass

        # Clear stream's buffer if it exists
        if hasattr(self.stream, 'player') and self.stream.player:
            try:
                # Clear StreamPlayer's buffer manager
                self.stream.player.buffer_manager.clear_buffer()
            except Exception as e:
                print(f"Error clearing stream player buffers: {str(e)}")

        # Reset chunk counter
        self.chunks_sent = 0
        print(f"[Session {self.session_id}] All audio buffers cleared for engine {self.engine_id}")





    async def audio_chunk_generator(self, send_wave_headers):
        chunks_sent = 0
        try:
            if send_wave_headers:
                print(f"[Session {self.session_id}] Creating wave header...")
                header = create_wave_header_for_engine(self.engine)
                print(f"[Session {self.session_id}] Wave header size: {len(header)} bytes")
                yield header

            while self.is_active:
                if self.session_id == GLOBAL_SESSION_ID:
                    try:
                        chunk, sess_id = await asyncio.wait_for(self.audio_queue.get(), timeout=1.0)
                        
                        # Only process chunks for current session
                        if sess_id == GLOBAL_SESSION_ID and self.is_active:
                            if chunk is None:
                                print(f"[Session {self.session_id}] End of stream marker received")
                                break
                            
                            chunks_sent += 1
                            yield chunk
                            
                    except asyncio.TimeoutError:
                        if not self.is_active:
                            print(f"[Session {self.session_id}] Session terminated, stopping generator")
                            break
                        continue
                else:
                    # Different session is active, stop processing
                    break

        except Exception as e:
            print(f"[Session {self.session_id}] Error in generator: {str(e)}")
        finally:
            print(f"[Session {self.session_id}] Generator finished. Total chunks sent: {chunks_sent}")
            self.cleanup()

        



        

    def on_audio_chunk(self, chunk):
        if chunk is not None and self.is_active:
            # print(f"[Session {self.session_id}] Received audio chunk: {len(chunk)} bytes")
            # Use run_coroutine_threadsafe instead of create_task
            future = asyncio.run_coroutine_threadsafe(
                self.audio_queue.put([chunk, self.session_id]), 
                self.loop
            )
            future.result()  # Wait for the result





    def on_audio_stream_stop(self):
        print(f"[Session {self.session_id}] Stream stop signal received")
        if self.is_active:
            # Use run_coroutine_threadsafe instead of create_task
            future = asyncio.run_coroutine_threadsafe(
                self.audio_queue.put([None, self.session_id]), 
                self.loop
            )
            future.result()  # Wait for the result



    def cleanup(self):
        """Clean up handler resources"""
        global active_sessions, engine_manager
        print(f"[Session {self.session_id}] Cleaning up")
        self.is_active = False
        if self.session_id in active_sessions:
            del active_sessions[self.session_id]
        if self.engine_id:
            engine_manager.release_engine(self.session_id)



    def stop(self):
        """Safely stop the handler and clear all audio buffers"""
        print(f"[Session {self.session_id}] Stopping handler for engine {self.engine_id}")
        self.is_active = False
        
        if hasattr(self, 'stream') and self.stream:
            try:

                print('Clearing the engine queue')

                # self.stream.engine.clear_queue()
                # Stop text stream first
                self.stream.char_iter.stop()
                
                # Clear all audio buffers
                self.clear_all_buffers()
                
                # Stop the stream
                self.stream.stop()


            except Exception as e:
                print(f"Error stopping stream: {str(e)}")
        
        self.cleanup()
    

    def on_playback_stop(self):
        """Called when audio playback stops"""
        if self.is_active and self.session_id == GLOBAL_SESSION_ID:
            print(f"[Session {self.session_id}] Playback stopped for engine {self.engine_id}")
            self.clear_all_buffers()



    def play_text_to_speech(self, text):
        if self.is_active and self.session_id == GLOBAL_SESSION_ID:
            print(f"[Session {self.session_id}] Starting TTS on engine {self.engine_id} for: {text[:100]}...")
            # Stop any ongoing synthesis and clear buffers
            
            self.stream.stop()
            self.stream.char_iter.items.clear()
            self.clear_all_buffers()
            self.stream._create_iterators()

            print('Clearing the engine queue')
            # self.stream.engine.clear_queue()
            
            # Start new synthesis
            self.stream.feed(text)
            self.stream.play_async(on_audio_chunk=self.on_audio_chunk, muted=True)
            print(f"[Session {self.session_id}] TTS processing started on engine {self.engine_id}")





app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Define a CSP that allows 'self' for script sources for firefox
csp = {
    "default-src": "'self'",
    "script-src": "'self'",
    "style-src": "'self' 'unsafe-inline'",
    "img-src": "'self' data:",
    "font-src": "'self' data:",
    "media-src": "'self' blob:",
}
csp_string = "; ".join(f"{key} {value}" for key, value in csp.items())








@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = csp_string
    return response


@app.get("/favicon.ico")
async def favicon():
    return FileResponse("static/favicon.ico")


def _set_engine(engine_name):
    global current_engine, stream
    if current_engine is None:
        current_engine = engines[engine_name]
    else:
        current_engine = engines[engine_name]

    if voices[engine_name]:
        engines[engine_name].set_voice(voices[engine_name][0].name)


@app.get("/set_engine")
def set_engine(request: Request, engine_name: str = Query(...)):
    if engine_name not in engines:
        return {"error": "Engine not supported"}

    try:
        _set_engine(engine_name)
        return {"message": f"Switched to {engine_name} engine"}
    except Exception as e:
        logging.error(f"Error switching engine: {str(e)}")
        return {"error": "Failed to switch engine"}



def is_browser_request(request):
    user_agent = request.headers.get("user-agent", "").lower()
    is_browser = any(browser_id in user_agent for browser_id in BROWSER_IDENTIFIERS)
    return is_browser



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

    # Create a new BytesIO with the correct MIME type for Firefox
    final_wave_header = io.BytesIO()
    final_wave_header.write(wave_header_bytes)
    final_wave_header.seek(0)

    return final_wave_header.getvalue()




engine_manager = None

@app.on_event("startup")
async def startup_event():
    global active_sessions, engine_manager

    engine_manager = EngineManager(num_engines=5)

    asyncio.create_task(cleanup_inactive_sessions())



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
    print("GLOBAL_SESSION_ID = ", GLOBAL_SESSION_ID)

    print(f"Received TTS request for session {session_id}")
    
    # Terminate any existing session with the same ID
    # active_sessions[session_id]

    global active_sessions

    global current_en

    
    previous_session_ids = list(active_sessions.keys())
    print("Previous session ids :- ", previous_session_ids)
    for sess_id in previous_session_ids:

        old_handler = active_sessions[sess_id]
        old_handler.is_active = False
        old_handler.stream.stop()
        old_handler.stop()
 

    
    with tts_lock:


        engine, engine_id = engine_manager.assign_engine(session_id)
        if not engine:
            return {"error": "No engines available"}
            
        request_handler = TTSRequestHandler(engine, engine_id, session_id)
        active_sessions[session_id] = request_handler
        browser_request = is_browser_request(request)


        if not engine:
            return {"error": "No engines available"}
            

        request_handler.stream.pause()
        request_handler.stream.stop()
        current_engine.clear_queue()

        time.sleep(1)

        request_handler.stream.resume()

        request_handler.stream.pause()
        request_handler.stream.stop()
        current_engine.clear_queue()

        #time.sleep(1)

        request_handler.stream.resume()



        # request_handler.stream.force_clear()

        # global active_sessions
        active_sessions[session_id] = request_handler
        browser_request = is_browser_request(request)

        print("Active_sessions :", active_sessions)
        print(f"TTS Request - Text: {text}, Session: {session_id}")
        print(f"Browser Request: {browser_request}")

        # if play_text_to_speech_semaphore.acquire(blocking=True):
        try:
            # Use asyncio.get_event_loop() instead of undefined loop variable
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, request_handler.play_text_to_speech, text)

        except:
            pass
        # finally:
        #     play_text_to_speech_semaphore.release()

        headers = {
            "Content-Type": "audio/wav",
            # "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Session-ID": session_id,
            "Access-Control-Allow-Origin": "*",  # Add CORS headers here too
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
                if not handler.is_active:
                    print(f"Cleaning up inactive session {session_id}")
                    handler.cleanup()
            await asyncio.sleep(60)
        except Exception as e:
            print(f"Error in cleanup task: {str(e)}")
            await asyncio.sleep(60)




@app.on_event("shutdown")
async def shutdown_event():
    global active_sessions, engine_manager
    current_sessions = list(active_sessions.keys())
    for session_id in current_sessions:
        handler = active_sessions[session_id]
        handler.stop()
        engine_manager.release_engine(session_id)


@app.get("/engines")
def get_engines():
    return list(engines.keys())


@app.get("/voices")
def get_voices():
    voices_list = []
    for voice in voices[current_engine.engine_name]:
        voices_list.append(voice.name)
    return voices_list


@app.get("/setvoice")
def set_voice(request: Request, voice_name: str = Query(...)):
    print(f"Getting request: {voice_name}")
    if not current_engine:
        print("No engine is currently selected")
        return {"error": "No engine is currently selected"}

    try:
        print(f"Setting voice to {voice_name}")
        current_engine.set_voice(voice_name)
        return {"message": f"Voice set to {voice_name} successfully"}
    except Exception as e:
        print(f"Error setting voice: {str(e)}")
        logging.error(f"Error setting voice: {str(e)}")
        return {"error": "Failed to set voice"}


@app.get("/")
def root_page():
    engines_options = "".join(
        [
            f'<option value="{engine}">{engine.title()}</option>'
            for engine in engines.keys()
        ]
    )
    content = f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>Text-To-Speech</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    background-color: #f0f0f0;
                    margin: 0;
                    padding: 0;
                }}
                h2 {{
                    color: #333;
                    text-align: center;
                }}
                #container {{
                    width: 80%;
                    margin: 50px auto;
                    background-color: #fff;
                    border-radius: 10px;
                    padding: 20px;
                    box-shadow: 0 0 10px rgba(0, 0, 0, 0.1);
                }}
                label {{
                    font-weight: bold;
                }}
                select, textarea {{
                    width: 100%;
                    padding: 10px;
                    margin: 10px 0;
                    border: 1px solid #ccc;
                    border-radius: 5px;
                    box-sizing: border-box;
                    font-size: 16px;
                }}
                button {{
                    display: block;
                    width: 100%;
                    padding: 15px;
                    background-color: #007bff;
                    border: none;
                    border-radius: 5px;
                    color: #fff;
                    font-size: 16px;
                    cursor: pointer;
                    transition: background-color 0.3s;
                }}
                button:hover {{
                    background-color: #0056b3;
                }}
                audio {{
                    width: 80%;
                    margin: 10px auto;
                    display: block;
                }}
            </style>
        </head>
        <body>
            <div id="container">
                <h2>Text to Speech</h2>
                <label for="engine">Select Engine:</label>
                <select id="engine">
                    {engines_options}
                </select>
                <label for="voice">Select Voice:</label>
                <select id="voice">
                    <!-- Options will be dynamically populated by JavaScript -->
                </select>
                <textarea id="text" rows="4" cols="50" placeholder="Enter text here..."></textarea>
                <button id="speakButton">Speak</button>
                <audio id="audio" controls></audio> <!-- Hidden audio player -->
            </div>
            <script src="/static/tts.js"></script>
        </body>
    </html>
    """
    return HTMLResponse(content=content)





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
    
    print("Initializing TTS Engines")
    
    # Initialize engines dict
    engines = {}
    voices = {}
    current_engine = None

    try:
        # Initialize Coqui engine
        print("Initializing Coqui engine")
        engines["coqui"] = CoquiEngine(voices_path='/home/ubuntu/realtime_tts/RealtimeTTS/tests/coqui_voices/',voice='coqui_Alexandra Hisakawa.wav')
        
        # Get voices for the engine
        print("Getting voices for Coqui engine")
        voices["coqui"] = engines["coqui"].get_voices()
        
        # Set as current engine
        current_engine = engines["coqui"]
        print("Coqui engine initialized successfully")
        
    except Exception as e:
        print(f"Error initializing Coqui engine: {str(e)}")
        raise

    if not current_engine:
        raise RuntimeError("No TTS engine was successfully initialized")

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

# /home/ubuntu/realtime_tts/RealtimeTTS/tests/coqui_voices/coqui_Alexandra Hisakawa.wav



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





app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Define a CSP that allows 'self' for script sources for firefox
csp = {
    "default-src": "'self'",
    "script-src": "'self' https://cdn.jsdelivr.net",
    "style-src": "'self' https://cdn.jsdelivr.net 'unsafe-inline'",
    # "style-src : "'self' https://cdn.jsdelivr.net 'unsafe-inline'',
    "img-src": "'self' data:",
    "font-src": "'self' data:",
    "media-src": "'self' blob:",

}
csp_string = "; ".join(f"{key} {value}" for key, value in csp.items())






@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = csp_string
    return response


@app.get("/favicon.ico")
async def favicon():
    return FileResponse("static/favicon.ico")


def _set_engine(engine_name):
    global current_engine, stream
    if current_engine is None:
        current_engine = engines[engine_name]
    else:
        current_engine = engines[engine_name]

    if voices[engine_name]:
        engines[engine_name].set_voice(voices[engine_name][0].name)


@app.get("/set_engine")
def set_engine(request: Request, engine_name: str = Query(...)):
    if engine_name not in engines:
        return {"error": "Engine not supported"}

    try:
        _set_engine(engine_name)
        return {"message": f"Switched to {engine_name} engine"}
    except Exception as e:
        logging.error(f"Error switching engine: {str(e)}")
        return {"error": "Failed to switch engine"}



def is_browser_request(request):
    user_agent = request.headers.get("user-agent", "").lower()
    is_browser = any(browser_id in user_agent for browser_id in BROWSER_IDENTIFIERS)
    return is_browser



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

    # Create a new BytesIO with the correct MIME type for Firefox
    final_wave_header = io.BytesIO()
    final_wave_header.write(wave_header_bytes)
    final_wave_header.seek(0)

    return final_wave_header.getvalue()







async def cleanup_inactive_sessions():
    while True:
        try:
            global active_sessions
            current_sessions = list(active_sessions.keys())
            for session_id in current_sessions:
                handler = active_sessions[session_id]
                if not handler.is_active:
                    print(f"Cleaning up inactive session {session_id}")
                    handler.cleanup()
            await asyncio.sleep(60)
        except Exception as e:
            print(f"Error in cleanup task: {str(e)}")
            await asyncio.sleep(60)





@app.on_event("shutdown")
async def shutdown_event():
    global active_sessions, engine_manager
    current_sessions = list(active_sessions.keys())
    for session_id in current_sessions:
        handler = active_sessions[session_id]
        handler.stop()
    engine_manager.cleanup_all_engines()




@app.get("/engines")
def get_engines():
    return list(engines.keys())


@app.get("/voices")
def get_voices():
    voices_list = []
    for voice in voices[current_engine.engine_name]:
        voices_list.append(voice.name)
    return voices_list


@app.get("/setvoice")
def set_voice(request: Request, voice_name: str = Query(...)):
    print(f"Getting request: {voice_name}")
    if not current_engine:
        print("No engine is currently selected")
        return {"error": "No engine is currently selected"}

    try:
        print(f"Setting voice to {voice_name}")
        current_engine.set_voice(voice_name)
        return {"message": f"Voice set to {voice_name} successfully"}
    except Exception as e:
        print(f"Error setting voice: {str(e)}")
        logging.error(f"Error setting voice: {str(e)}")
        return {"error": "Failed to set voice"}





if __name__ == "__main__":
    print("Initializing TTS Engines")
    
    # Initialize engines dict
    engines = {}
    voices = {}
    current_engine = None

    try:
        # Initialize Coqui engine
        print("Initializing Coqui engine")
        engines["coqui"] =CoquiEngine(voices_path='/home/ubuntu/realtime_tts/RealtimeTTS/tests/coqui_voices/',voice='coqui_Alexandra Hisakawa.wav')
        
        # Get voices for the engine
        print("Getting voices for Coqui engine")
        voices["coqui"] = engines["coqui"].get_voices()
        
        # Set as current engine
        current_engine = engines["coqui"]
        print("Coqui engine initialized successfully")
        
    except Exception as e:
        print(f"Error initializing Coqui engine: {str(e)}")
        raise  # Re-raise the exception to see what's going wrong

    if not current_engine:
        raise RuntimeError("No TTS engine was successfully initialized")

    # Rest of your server initialization code...
    print("Server ready")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
    )

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