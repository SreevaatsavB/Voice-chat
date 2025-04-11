### 

## Speech-to-Text server

[**RealtimeSTT**](https://github.com/KoljaB/RealtimeSTT) is utilised for the speech to text conversion which uses FasterWhisper along with transmission of audio chunks via a websocket connection from this client to server. 

For local development (non-secure):
```
python stt_engine.py --port PORT_NUMBER
```

```
python stt_engine.py --use-ssl --port PORT_NUMBER
```



## Text-to-Speech server

The tts server utilises the streaming capabilities from [**RealtimeTTS**](https://github.com/KoljaB/RealtimeTTS) library for instant streaming of audio chunks for a given text to the TTS model. 

We use HTTP/HTTPS conenction for the transmission of the audio chunks across the server and client. 

While deploying on an external server, secure connections will be needed for relaible data transfer, and this option can be used by using your self-signed certificated or directly mappign to your domain endpoint (HTTPS secure)

For local development (non-secure):
```
python tts_engine.py --port PORT_NUMBER --dev-mode
```

For production/remote server with SSL:
```
python tts_engine.py --port PORT_NUMBER --use-ssl
```



### Create venvs 

python3 -m venv venv_tts

python3 -m venv venv_stt

For stt on MACOS :- 
brew install portaudio
brew install ffmpeg

