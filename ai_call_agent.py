import json
import os
import time
import traceback
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from twilio.twiml.voice_response import VoiceResponse, Connect
from elevenlabs import ElevenLabs
from elevenlabs.conversational_ai.conversation import (
    Conversation,
    ClientTools,
    ConversationInitiationData,
)
from twilio_audio_interface import TwilioAudioInterface
from starlette.websockets import WebSocketDisconnect
from twilio.rest import Client

load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")

# Important: Render env mein sirf domain dena, https:// nahi
# Example: project-api-phone-call-agent.onrender.com
ngrok = os.getenv("NGROK_URL", "")

TARGET_PHONE_NUMBER = "+923442862596"

app = FastAPI()


class CustomerDetails(BaseModel):
    customer_name: str
    language: str


# --- LIVE GAS READING MEMORY ---
latest_gas_reading = {
    "reading": 0,
    "level": "UNKNOWN",
    "updated_at": 0.0,
}


def get_gas_level(reading: int) -> str:
    if reading < 100:
        return "SAFE"
    elif reading < 200:
        return "LOW GAS LEAK"
    elif reading < 300:
        return "MEDIUM GAS LEAK"
    else:
        return "HIGH / DANGEROUS GAS LEAK"


def save_latest_reading(reading: int):
    latest_gas_reading["reading"] = reading
    latest_gas_reading["level"] = get_gas_level(reading)
    latest_gas_reading["updated_at"] = time.time()


def get_latest_gas_reading(parameters=None):
    reading = latest_gas_reading["reading"]
    level = latest_gas_reading["level"]

    return (
        f"The latest gas sensor reading is {reading}. "
        f"The current safety level is: {level}."
    )


@app.get("/")
async def root():
    return {
        "message": "Twilio-ElevenLabs Integration Server",
        "latest_reading": latest_gas_reading,
    }


@app.post("/twilio/inbound_call")
async def handle_incoming_call(request: Request):
    customer_name = request.query_params.get("CustomerName", "Azfar")
    language = request.query_params.get("Language", "en")

    form_data = await request.form()
    call_sid = form_data.get("CallSid", "Unknown")
    from_number = form_data.get("From", "Unknown")

    print(
        f"Incoming or answered outbound call: CallSid={call_sid}, "
        f"From={from_number}, CustomerName={customer_name}, Language={language}"
    )

    response = VoiceResponse()
    connect = Connect()
    connect.stream(url=f"wss://{ngrok}/media-stream-eleven/{customer_name}/{language}")
    response.append(connect)

    return HTMLResponse(content=str(response), media_type="application/xml")


# --- ElevenLabs Client Tools ---
def trigger_browser_alert(parameters):
    message = parameters.get("message")
    print(f"Triggering alert: {message}")
    return "Alert triggered successfully"


client_tools = ClientTools()
client_tools.register("triggerBrowserAlert", trigger_browser_alert)
client_tools.register("getLatestGasReading", get_latest_gas_reading)


@app.websocket("/media-stream-eleven/{customer_name}/{language}")
async def handle_media_stream(websocket: WebSocket, customer_name: str, language: str):
    await websocket.accept()
    print(f"WebSocket connection opened for {customer_name} in {language} language.")

    audio_interface = TwilioAudioInterface(websocket)
    eleven_labs_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

    current_reading = latest_gas_reading["reading"]
    current_level = latest_gas_reading["level"]

    conversation_override = {
        "agent": {
            "prompt": {
                "prompt": (
                    "You are Ahmed, a smart home gas safety assistant. "
                    "A critical gas leak has been detected in Azfar's house. "
                    "Speak in clear English only. Be urgent, serious, and concise. "
                    f"At the start of this call, the gas reading was {current_reading}, "
                    f"and the safety level was {current_level}. "
                    "If Azfar asks about the current gas level, latest gas reading, "
                    "or whether the gas level is safe or dangerous, use the tool "
                    "getLatestGasReading before answering. "
                    "Tell Azfar to turn off the gas supply if safe, avoid electrical switches, "
                    "open windows if possible, and leave the area carefully. "
                    "If the user says the issue is fixed, return to a calm tone."
                )
            },
            "first_message": (
                f"Hello Azfar, Ahmed speaking. A critical gas leak has been detected "
                f"in your house. The current gas reading is {current_reading}, "
                f"and the level is {current_level}. Please avoid electrical switches, "
                f"open windows if possible, and leave the area carefully."
            ),
            "language": "en",
        },
        "tts": {
            "model_id": "eleven_multilingual_v2",
            "voice_id": "TX3LPaxmHKxFdv7VOQHJ",
            "output_format": "ulaw_8000",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.7,
            },
        },
    }

    config = ConversationInitiationData(
        conversation_config_override=conversation_override
    )

    conversation_log = []

    def on_agent_response(text: str):
        print(f"Agent: {text}")
        conversation_log.append({"speaker": "agent", "message": text})

    def on_user_transcript(text: str):
        print(f"User: {text}")
        conversation_log.append({"speaker": "user", "message": text})

    conversation = None

    try:
        conversation = Conversation(
            client=eleven_labs_client,
            agent_id=ELEVENLABS_AGENT_ID,
            requires_auth=False,
            audio_interface=audio_interface,
            client_tools=client_tools,
            config=config,
            callback_agent_response=on_agent_response,
            callback_user_transcript=on_user_transcript,
        )

        conversation.start_session()
        print("Conversation session started.")

        async for message in websocket.iter_text():
            if message:
                await audio_interface.handle_twilio_message(json.loads(message))

    except WebSocketDisconnect:
        print("WebSocket disconnected.")

    except Exception as e:
        print(f"Error in WebSocket handler: {e}")
        traceback.print_exc()

    finally:
        if conversation is not None:
            try:
                conversation.end_session()
                conversation.wait_for_session_end()
                print("Conversation session ended.")
            except Exception as e:
                print(f"Error ending conversation session: {e}")
                traceback.print_exc()


# --- Global Call State ---
call_state = {
    "is_active": False,
    "last_call_time": 0.0,
    "last_success_time": 0.0,
}


@app.post("/twilio/outbound_call")
async def make_outbound_call(customer_name: str, language: str, number: str):
    if not number:
        raise HTTPException(status_code=400, detail="Target number is required.")

    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    try:
        redirect_url = (
            f"https://{ngrok}/twilio/inbound_call"
            f"?CustomerName={customer_name}&Language={language}"
        )

        status_callback_url = f"https://{ngrok}/twilio/call-status"

        twiml_response = VoiceResponse()
        twiml_response.redirect(redirect_url, method="POST")

        call = client.calls.create(
            twiml=str(twiml_response),
            to=number,
            from_=TWILIO_PHONE_NUMBER,
            status_callback=status_callback_url,
            status_callback_event=[
                "completed",
                "busy",
                "no-answer",
                "failed",
                "canceled",
            ],
        )

        call_state["is_active"] = True
        call_state["last_call_time"] = time.time()

        print(f"Outbound call initiated: {call.sid}")

        return {
            "message": "Outbound call initiated",
            "CallSid": call.sid,
            "to": number,
            "from": TWILIO_PHONE_NUMBER,
        }

    except Exception as e:
        print(f"Error initiating outbound call: {e}")
        call_state["is_active"] = False
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/twilio/call-status")
async def call_status_webhook(request: Request):
    form_data = await request.form()
    call_status = form_data.get("CallStatus")
    print(f"Twilio Call Status Update: {call_status}")

    if call_status == "completed":
        call_state["last_success_time"] = time.time()
        call_state["is_active"] = False
        print("✅ Call Completed Successfully. Muting alarms for 10 seconds.")

    elif call_status in ["busy", "no-answer", "failed", "canceled"]:
        call_state["is_active"] = False
        print("❌ Call Failed/Missed. System ready for retry.")

    return {"status": "ok"}


# --- NodeMCU sends latest gas reading here ---
@app.api_route("/update-reading", methods=["GET", "POST"])
async def update_reading(reading: int):
    save_latest_reading(reading)

    print(
        f"📡 Live gas reading updated: {reading} | "
        f"Level: {latest_gas_reading['level']}"
    )

    return {
        "status": "updated",
        "reading": latest_gas_reading["reading"],
        "level": latest_gas_reading["level"],
        "updated_at": latest_gas_reading["updated_at"],
    }


# --- NodeMCU checks call status here ---
# User requested /call-status instead of /check-call-status
@app.get("/call-status")
async def call_status_for_nodemcu():
    return {
        "active": call_state["is_active"],
        "latest_reading": latest_gas_reading["reading"],
        "level": latest_gas_reading["level"],
        "updated_at": latest_gas_reading["updated_at"],
    }


# --- NodeMCU triggers gas alert call here ---
@app.get("/trigger-gas-alert")
async def trigger_gas_alert(reading: int = 0):
    current_time = time.time()

    if reading > 0:
        save_latest_reading(reading)
        print(
            f"⚠️ Alert reading received: {reading} | "
            f"Level: {latest_gas_reading['level']}"
        )

    if call_state["is_active"]:
        return {
            "status": "ignored",
            "reason": "call_in_progress",
            "reading": latest_gas_reading["reading"],
            "level": latest_gas_reading["level"],
        }

    if (current_time - call_state["last_success_time"]) < 10:
        return {
            "status": "ignored",
            "reason": "already_acknowledged",
            "reading": latest_gas_reading["reading"],
            "level": latest_gas_reading["level"],
        }

    if (current_time - call_state["last_call_time"]) < 30:
        return {
            "status": "ignored",
            "reason": "cooldown_active",
            "reading": latest_gas_reading["reading"],
            "level": latest_gas_reading["level"],
        }

    print("⚠️ GAS ALERT RECEIVED! Initiating Call...")

    return await make_outbound_call(
        customer_name="Azfar",
        language="en",
        number=TARGET_PHONE_NUMBER,
    )
