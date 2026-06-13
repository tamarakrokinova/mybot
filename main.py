from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse
from openai import OpenAI
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import os
import json
import uvicorn
import datetime
import random
import requests
import base64

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel - natural female voice
SCOPES = ["https://www.googleapis.com/auth/calendar"]

conversation_history = []

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

def speak(text):
    url = "https://api.elevenlabs.io/v1/text-to-speech/" + VOICE_ID + "/stream"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg"
    }
    data = {
        "text": text,
        "model_id": "eleven_turbo_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
    }
    r = requests.post(url, headers=headers, json=data)
    if r.status_code == 200:
        audio_b64 = base64.b64encode(r.content).decode("utf-8")
        return audio_b64
    else:
        print("ELEVENLABS ERROR:", r.text)
        return None

def make_xml(text, action="/handle-speech", end=False):
    audio = speak(text)
    if audio and not end:
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            '<Play>data:audio/mpeg;base64,' + audio + '</Play>'
            '<Gather input="speech" action="' + action + '" language="en-US" timeout="10" speechTimeout="3"></Gather>'
            "</Response>"
        )
    elif audio and end:
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            '<Play>data:audio/mpeg;base64,' + audio + '</Play>'
            "</Response>"
        )
    else:
        if end:
            xml = '<Response><Say voice="alice">' + text + '</Say></Response>'
        else:
            xml = '<Response><Say voice="alice">' + text + '</Say><Gather input="speech" action="' + action + '" language="en-US" timeout="10" speechTimeout="3"></Gather></Response>'
    return xml

app = FastAPI()

@app.post("/incoming-call")
async def incoming_call(request: Request):
    global conversation_history
    conversation_history = []
    print("=== NEW CALL ===")
    xml = make_xml("Hello! Thank you for calling Union Recording Studio. How can I help you today?")
    return Response(content=xml, media_type="application/xml")

@app.post("/handle-speech")
async def handle_speech(request: Request):
    global conversation_history
    form = await request.form()
    user_said = form.get("SpeechResult", "")
    print("PATIENT:", user_said)
    today = datetime.date.today().strftime("%Y-%m-%d")
    year = datetime.date.today().year

    prompt = (
        "You are a friendly receptionist at Union Recording Studio. "
        "The studio has two locations: Rampart and Santa Monica. "
        "Today is " + today + ". Year is " + str(year) + ". "
        "Help clients book recording sessions. "
        "Ask which location they prefer if they don't mention one. "
        "Never ask for the year. "
        "Convert times to 24-hour format. Examples: 4pm=16:00, 7pm=19:00, 10am=10:00. "
        "When you have location, date and time, output ONLY on the first line: BOOK: LOCATION YYYY-MM-DD HH:MM "
        "Example: BOOK: Santa Monica 2026-06-13 14:00 "
        "After booking, ask: Is there anything else I can help you with? "
        "If client says no or goodbye or thank you, output ONLY: GOODBYE"
    )

    conversation_history.append({"role": "user", "content": user_said})

    result = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "system", "content": prompt}] + conversation_history,
    )

    answer = result.choices[0].message.content.strip()
    print("BOT:", answer)
    conversation_history.append({"role": "assistant", "content": answer})

    if "BOOK:" in answer:
        try:
            book_line = answer.split("BOOK:")[1].split("\n")[0].strip()
            parts = book_line.split()
            if len(parts) == 4:
                location = parts[0] + " " + parts[1]
                date = parts[2]
                time = parts[3]
            elif len(parts) == 3:
                location = parts[0]
                date = parts[1]
                time = parts[2]
            else:
                location = "Studio"
                date = parts[-2]
                time = parts[-1]

            service = get_calendar_service()
            event = {
                "summary": "Recording Session - " + location,
                "start": {"dateTime": date + "T" + time + ":00", "timeZone": "America/Los_Angeles"},
                "end": {"dateTime": date + "T" + time + ":00", "timeZone": "America/Los_Angeles"},
            }
            service.events().insert(calendarId="primary", body=event).execute()
            dt = datetime.datetime.strptime(date + " " + time, "%Y-%m-%d %H:%M")
            friendly_time = dt.strftime("%B %d at %I:%M %p")
            spoken = "Perfect! Your recording session at " + location + " is booked for " + friendly_time + ". Is there anything else I can help you with?"
            print("BOOKED:", location, friendly_time)
            conversation_history.append({"role": "assistant", "content": spoken})
            xml = make_xml(spoken)
            return Response(content=xml, media_type="application/xml")
        except Exception as e:
            print("ERROR:", e)
            xml = make_xml("Sorry, I could not book the session. Please call back.", end=True)
            return Response(content=xml, media_type="application/xml")

    if "GOODBYE" in answer:
        xml = make_xml("Thank you for calling Union Recording Studio. Have a great day! Goodbye!", end=True)
        return Response(content=xml, media_type="application/xml")

    xml = make_xml(answer)
    return Response(content=xml, media_type="application/xml")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
