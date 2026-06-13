import os
import json
import uvicorn
import datetime
import asyncio
import websockets
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SCOPES = ["https://www.googleapis.com/auth/calendar"]

def get_calendar_service():
    token_json = os.getenv("GOOGLE_TOKEN")
    if token_json:
        creds_data = json.loads(token_json)
        creds = Credentials(
            token=creds_data.get("token"),
            refresh_token=creds_data.get("refresh_token"),
            token_uri=creds_data.get("token_uri"),
            client_id=creds_data.get("client_id"),
            client_secret=creds_data.get("client_secret"),
            scopes=creds_data.get("scopes"),
        )
    elif os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    else:
        raise Exception("No Google credentials found")
    return build("calendar", "v3", credentials=creds)

today = datetime.date.today().strftime("%Y-%m-%d")
year = datetime.date.today().year

SYSTEM_PROMPT = (
    "You are a friendly receptionist at Union Recording Studio. "
    "The studio has two locations: Rampart and Santa Monica. "
    "Today is " + today + ". Year is always " + str(year) + ". "
    "Help clients book recording sessions. "
    "Ask which location they prefer if they don't mention one. "
    "Never ask for the year. "
    "When you have confirmed location, date and time, say exactly: "
    "BOOKING: LOCATION YYYY-MM-DD HH:MM using 24-hour time. "
    "After booking say: Your session is confirmed. Is there anything else I can help you with? "
    "If client is done, say goodbye warmly."
)

app = FastAPI()

@app.post("/incoming-call")
async def incoming_call(request: Request):
    host = request.headers.get("host")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Connect>"
        '<Stream url="wss://' + host + '/media-stream"/>'
        "</Connect>"
        "</Response>"
    )
    return Response(content=xml, media_type="application/xml")

@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()
    print("=== NEW CALL ===")

    async with websockets.connect(
        "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview",
        additional_headers={
            "Authorization": "Bearer " + OPENAI_API_KEY,
            "OpenAI-Beta": "realtime=v1",
        }
    ) as openai_ws:

        await openai_ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "turn_detection": {"type": "server_vad"},
                "input_audio_format": "g711_ulaw",
                "output_audio_format": "g711_ulaw",
                "voice": "alloy",
                "instructions": SYSTEM_PROMPT,
                "modalities": ["text", "audio"],
                "temperature": 0.7,
            }
        }))

        await openai_ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Hello"}]
            }
        }))
        await openai_ws.send(json.dumps({"type": "response.create"}))

        stream_sid = None

        async def receive_from_twilio():
            nonlocal stream_sid
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data["event"] == "start":
                        stream_sid = data["start"]["streamSid"]
                        print("Stream started:", stream_sid)
                    elif data["event"] == "media":
                        await openai_ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": data["media"]["payload"]
                        }))
                    elif data["event"] == "stop":
                        print("Call ended")
                        break
            except Exception as e:
                print("Twilio error:", e)

        async def send_to_twilio():
            try:
                async for message in openai_ws:
                    data = json.loads(message)
                    if data["type"] == "response.audio.delta" and data.get("delta"):
                        await websocket.send_json({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": data["delta"]}
                        })
                    elif data["type"] == "response.audio_transcript.done":
                        transcript = data.get("transcript", "")
                        print("BOT SAID:", transcript)
                        if "BOOKING:" in transcript:
                            try:
                                book_line = transcript.split("BOOKING:")[1].split("\n")[0].strip()
                                parts = book_line.split()
                                if len(parts) == 4:
                                    location = parts[0] + " " + parts[1]
                                    date = parts[2]
                                    time = parts[3]
                                else:
                                    location = parts[0]
                                    date = parts[1]
                                    time = parts[2]
                                service = get_calendar_service()
                                event = {
                                    "summary": "Recording Session - " + location,
                                    "start": {"dateTime": date + "T" + time + ":00", "timeZone": "America/Los_Angeles"},
                                    "end": {"dateTime": date + "T" + time + ":00", "timeZone": "America/Los_Angeles"},
                                }
                                service.events().insert(calendarId="primary", body=event).execute()
                                print("BOOKED:", location, date, time)
                            except Exception as e:
                                print("CALENDAR ERROR:", e)
            except Exception as e:
                print("OpenAI error:", e)

        await asyncio.gather(receive_from_twilio(), send_to_twilio())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
