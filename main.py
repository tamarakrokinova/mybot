from fastapi import FastAPI, Request
from fastapi.responses import Response
from openai import OpenAI
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import os
import json
import uvicorn
import datetime
import random

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
SCOPES = ["https://www.googleapis.com/auth/calendar"]

conversation_history = []

FILLERS = [
    "Mhm, let me check that for you.",
    "Sure, one moment.",
    "Of course, let me look into that.",
    "Absolutely, give me just a second.",
]

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

app = FastAPI()

@app.post("/incoming-call")
async def incoming_call(request: Request):
    global conversation_history
    conversation_history = []
    print("=== NEW CALL ===")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        '<Gather input="speech" action="/handle-speech" language="en-US" timeout="10" speechTimeout="3">'
        '<Say voice="alice" language="en-US">Hello! You have reached our clinic. How can I help you?</Say>'
        "</Gather>"
        "</Response>"
    )
    return Response(content=xml, media_type="application/xml")

@app.post("/handle-speech")
async def handle_speech(request: Request):
    global conversation_history
    form = await request.form()
    user_said = form.get("SpeechResult", "")
    print("PATIENT:", user_said)
    today = datetime.date.today().strftime("%Y-%m-%d")
    year = datetime.date.today().year

    filler = random.choice(FILLERS)

    prompt = (
        "You are a friendly clinic receptionist. Today is " + today + ". Year is " + str(year) + ". "
        "Help patients book appointments. Never ask for the year. "
        "Convert times to 24-hour format. Examples: 4pm=16:00, 7pm=19:00, 10am=10:00. "
        "When you understand a date and time, output ONLY: BOOK: YYYY-MM-DD HH:MM "
        "After booking is confirmed, ask: Is there anything else I can help you with? "
        "If patient says no or goodbye or thank you, output ONLY: GOODBYE"
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
            book_part = answer.split("BOOK:")[1].strip().split()
            date = book_part[0]
            time = book_part[1]
            service = get_calendar_service()
            event = {
                "summary": "Clinic Appointment",
                "start": {"dateTime": date + "T" + time + ":00", "timeZone": "America/Los_Angeles"},
                "end": {"dateTime": date + "T" + time + ":00", "timeZone": "America/Los_Angeles"},
            }
            service.events().insert(calendarId="primary", body=event).execute()
            dt = datetime.datetime.strptime(date + " " + time, "%Y-%m-%d %H:%M")
            friendly_time = dt.strftime("%B %d at %I:%M %p")
            spoken = "Perfect! Your appointment is booked for " + friendly_time + ". Is there anything else I can help you with?"
            print("BOOKED:", friendly_time)
            conversation_history.append({"role": "assistant", "content": spoken})
            xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<Response>"
                '<Say voice="alice" language="en-US">' + filler + "</Say>"
                '<Say voice="alice" language="en-US">' + spoken + "</Say>"
                '<Gather input="speech" action="/handle-speech" language="en-US" timeout="10" speechTimeout="3"></Gather>'
                "</Response>"
            )
            return Response(content=xml, media_type="application/xml")
        except Exception as e:
            print("ERROR:", e)
            answer = "Sorry, I could not book the appointment. Please call back."

    if "GOODBYE" in answer:
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            '<Say voice="alice" language="en-US">Thank you for calling. Have a great day! Goodbye!</Say>'
            "</Response>"
        )
        return Response(content=xml, media_type="application/xml")

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        '<Say voice="alice" language="en-US">' + filler + "</Say>"
        '<Say voice="alice" language="en-US">' + answer + "</Say>"
        '<Gather input="speech" action="/handle-speech" language="en-US" timeout="10" speechTimeout="3"></Gather>'
        "</Response>"
    )
    return Response(content=xml, media_type="application/xml")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
