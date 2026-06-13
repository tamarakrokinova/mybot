import os
import json
import uuid
import uvicorn
import datetime
import random
from fastapi import FastAPI, Request
from fastapi.responses import Response, FileResponse
from openai import OpenAI
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
SCOPES = ["https://www.googleapis.com/auth/calendar"]

conversation_history = []
os.makedirs("/tmp/audio", exist_ok=True)

FILLERS = ["Mhm...", "Sure...", "Of course...", "Got it..."]

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
        raise Exception("No credentials")
    return build("calendar", "v3", credentials=creds)

def tts(text):
    try:
        response = client.audio.speech.create(model="tts-1", voice="nova", input=text)
        filename = str(uuid.uuid4()) + ".mp3"
        filepath = "/tmp/audio/" + filename
        with open(filepath, "wb") as f:
            f.write(response.content)
        return BASE_URL + "/audio/" + filename
    except Exception as e:
        print("TTS ERROR:", e)
        return None

def xml_play(url, next_action="/handle-speech", end=False):
    if end:
        return '<?xml version="1.0" encoding="UTF-8"?><Response><Play>' + url + '</Play></Response>'
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        '<Play>' + url + '</Play>'
        '<Gather input="speech" action="' + next_action + '" language="en-US" timeout="10" speechTimeout="3"></Gather>'
        '</Response>'
    )

def xml_say(text, end=False):
    if end:
        return '<?xml version="1.0" encoding="UTF-8"?><Response><Say voice="alice">' + text + '</Say></Response>'
    return '<?xml version="1.0" encoding="UTF-8"?><Response><Say voice="alice">' + text + '</Say><Gather input="speech" action="/handle-speech" language="en-US" timeout="10" speechTimeout="3"></Gather></Response>'

app = FastAPI()

@app.get("/audio/{filename}")
async def get_audio(filename: str):
    return FileResponse("/tmp/audio/" + filename, media_type="audio/mpeg")

@app.post("/incoming-call")
async def incoming_call(request: Request):
    global conversation_history
    conversation_history = []
    print("=== NEW CALL ===")
    url = tts("Hello! Thank you for calling Union Recording Studio. How can I help you today?")
    if url:
        return Response(content=xml_play(url), media_type="application/xml")
    return Response(content=xml_say("Hello! Thank you for calling Union Recording Studio. How can I help you?"), media_type="application/xml")

@app.post("/handle-speech")
async def handle_speech_alias(request: Request):
    return await filler(request)

@app.post("/filler")
async def filler(request: Request):
    form = await request.form()
    user_said = form.get("SpeechResult", "")
    print("PATIENT:", user_said)
    conversation_history.append({"role": "user", "content": user_said})
    filler_text = random.choice(FILLERS)
    url = tts(filler_text)
    if url:
        return Response(content=xml_play(url, next_action="/respond"), media_type="application/xml")
    return Response(content=xml_say(filler_text, next_action="/respond"), media_type="application/xml")

@app.post("/respond")
async def respond(request: Request):
    today = datetime.date.today().strftime("%Y-%m-%d")
    year = datetime.date.today().year
    prompt = (
        "You are a friendly receptionist at Union Recording Studio. "
        "Locations: Rampart and Santa Monica. "
        "Today is " + today + ". Year is " + str(year) + ". "
        "Help clients book recording sessions. Ask location if not mentioned. Never ask year. "
        "Convert times to 24-hour. When you have location+date+time output ONLY: BOOK: LOCATION YYYY-MM-DD HH:MM "
        "After booking ask: Is there anything else I can help you with? "
        "If done output ONLY: GOODBYE"
    )

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
            location = parts[0] + " " + parts[1] if len(parts) == 4 else parts[0]
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
            friendly = dt.strftime("%B %d at %I:%M %p")
            spoken = "Perfect! Your session at " + location + " is booked for " + friendly + ". Is there anything else I can help you with?"
            print("BOOKED:", location, friendly)
            conversation_history.append({"role": "assistant", "content": spoken})
            url = tts(spoken)
            if url:
                return Response(content=xml_play(url), media_type="application/xml")
        except Exception as e:
            print("ERROR:", e)

    if "GOODBYE" in answer:
        url = tts("Thank you for calling Union Recording Studio. Have a great day! Goodbye!")
        if url:
            return Response(content=xml_play(url, end=True), media_type="application/xml")
        return Response(content=xml_say("Goodbye!", end=True), media_type="application/xml")

    url = tts(answer)
    if url:
        return Response(content=xml_play(url), media_type="application/xml")
    return Response(content=xml_say(answer), media_type="application/xml")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
