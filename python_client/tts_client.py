import asyncio
import aiohttp
import pyaudio
import wave
import io
import time
import argparse
import ssl
import certifi

class AudioStreamTester:
    def __init__(self):
        self.chunk_size = 1024
        self.p = pyaudio.PyAudio()
        self.stream = None
        self.total_latency = []
        
    async def test_tts_stream(self, url, test_text, ssl_context, num_tests=5):
        """Test TTS streaming with multiple requests"""
        print(f"\nStarting TTS Stream Test with {num_tests} iterations")
        print(f"URL: {url}")
        print(f"Test text: {test_text}\n")
        
        for i in range(num_tests):
            print(f"\nTest {i + 1}/{num_tests}")
            await self.single_tts_test(url, test_text, ssl_context)
            await asyncio.sleep(1) 
            
        if self.total_latency:
            avg_latency = sum(self.total_latency) / len(self.total_latency)
            print(f"\nTest Summary:")
            print(f"Average latency: {avg_latency:.2f}ms")
            print(f"Min latency: {min(self.total_latency):.2f}ms")
            print(f"Max latency: {max(self.total_latency):.2f}ms")
    
    async def single_tts_test(self, url, text, ssl_context):
        """Perform a single TTS test"""
        start_time = time.time()
        first_chunk_time = None
        chunks_received = 0
        total_bytes = 0
        
        try:

            # Generate a random session ID
            session_id = f"test-session-{int(time.time())}"
            
            # Configure client session with SSL context
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            headers = {"X-Session-ID": session_id}  #

            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(f"{url}/tts?text={text}", headers=headers) as response:
                    if response.status != 200:
                        print(f"Error: Server returned status {response.status}")
                        return

                    self.stream = self.p.open(
                        format=pyaudio.paInt16,
                        channels=1,
                        rate=22050,  # Default sample rate, adjust if needed
                        output=True
                    )
                    
                    print("Starting to receive audio chunks...")
                    
                    # Process the stream in chunks
                    async for chunk in response.content.iter_any():
                        if chunks_received == 0:
                            first_chunk_time = time.time()
                            first_chunk_latency = (first_chunk_time - start_time) * 1000
                            print(f"First chunk latency: {first_chunk_latency:.2f}ms")
                            self.total_latency.append(first_chunk_latency)
                        
                        chunks_received += 1
                        total_bytes += len(chunk)
                        
                        # Play the audio chunk
                        self.stream.write(chunk)
                        
                        # Print progress
                        print(f"Received chunk {chunks_received}: {len(chunk)} bytes")
                    
                    end_time = time.time()
                    total_time = end_time - start_time
                    
                    print(f"\nTest Results:")
                    print(f"Total chunks received: {chunks_received}")
                    print(f"Total bytes received: {total_bytes}")
                    print(f"Total processing time: {total_time:.2f}s")
                    print(f"Average chunk size: {total_bytes/chunks_received:.2f} bytes")
                    
        except Exception as e:
            print(f"Error during streaming: {e}")
        finally:
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()

    def cleanup(self):
        """Clean up resources"""
        self.p.terminate()

async def main():
    parser = argparse.ArgumentParser(description='Test TTS Streaming')
    parser.add_argument('--url', default='http://0.0.0.0:8001', help='EC2 Server URL')
    parser.add_argument('--text', default='This is a test of the TTS streaming system.', help='Text to synthesize')
    parser.add_argument('--tests', type=int, default=3, help='Number of tests to run')
    parser.add_argument('--verify-ssl', action='store_true', help='Verify SSL certificate')
    args = parser.parse_args()
    
    # Configure SSL context
    if args.verify_ssl:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
    else:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
    
    tester = AudioStreamTester()
    try:
        await tester.test_tts_stream(args.url, args.text, ssl_context, args.tests)
    finally:
        tester.cleanup()

if __name__ == "__main__":
    asyncio.run(main())